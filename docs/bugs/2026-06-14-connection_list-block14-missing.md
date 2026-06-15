# Bug: `connection_list` rapporterar inte block 14 (utelämnar enkelsidiga kopplingar)

**Status:** PRIMÄR FIX IMPLEMENTERAD & TESTAD (2026-06-14) — array-slots enumereras nu. Sekundär förbättring (synliga dangling-noder) återstår, valfri.

**Fix:** `simulation_backend.py` `connection_list` läser nu `ConArrayGetNumCons` per connector och enumererar extra array-slots via `_get_array_connector_index`. Regressionstest: `tests/unit_py/test_connection_list_array_slots.py` (RED→GREEN). Full svit grön: Python 1/1, TypeScript 142/142.
**Hittad:** 2026-06-14, session `f1c9cd` (MCP v1.19.1)
**Rapporterad av:** Jonas
**Allvarlighetsgrad:** Medel — modellen ser ofullständig ut för AI:n, vilket kan leda till felaktiga byggbeslut

## Symptom

Modellen är byggd så här (enligt användaren):

```
Create(5) + Create(14) → Q transport(18) → Transport(35) → Publish Event1(54)
  → Q process(60) → Process(77) → Publish Event2(96) → Exit(102)
```

Men `connection_list` rapporterar att **block 14 (Create) inte är kopplat till någonting**.
Block 14 förekommer inte som `from` eller `to` i någon koppling.

## Bevis (full request/response-fångst, `temp/mcp_session.log`, 2026-06-14 19:03)

`block_list` → returnerade 12 block, **inklusive** `blockId:14` (`Create`, `Item.lbr`, label "Create").

`connection_list` → returnerade 7 kopplingar, **ingen** refererar `blockId:14`:

| nodeIndex | från | → | till |
|---|---|---|---|
| 4 | 5 (ItemOut) | → | 18 (ItemIn) |
| 19 | 18 | → | 35 |
| 25 | 35 | → | 54 |
| 34 | 54 | → | 60 |
| 39 | 60 | → | 77 |
| 45 | 77 | → | 96 |
| 54 | 96 | → | 102 |

Block 5 → 18 finns. Block 14 → 18 saknas helt.

### Avgörande: `block_info` per block (samma session, 19:11)

`block_info(blockId:14)` → `ItemOut` har **`nodeIndex: 77`** (≠ 0 → blocket ÄR kopplat).
`block_info(blockId:18)` → `ItemIn` har **`nodeIndex: 4`** och är märkt **`isArray: true`** (tar flera inkopplingar).

Slutsats av nodeIndex:
- Create(5).ItemOut (nodeIndex 4) ↔ Queue.ItemIn slot 0 (nodeIndex 4) → syns i connection_list.
- Create(14).ItemOut (**nodeIndex 77**) ↔ Queue.ItemIn slot 1 (array, **nodeIndex 77**) → **syns inte**.

### Körnings-bekräftelse: `simulation_run` (samma session, 19:15)

Simuleringen bevisar att Create(14):s koppling är **funktionell**, inte bara statiskt registrerad:
- `Q transport` (block 18): **`maxLength: 2`** — två items samtidigt i kön (kräver inflöde från båda Create-blocken)
- `Exit` (102): **`itemsExited: 2`**
- `Transport` (35) och `Process` (77): 2 items bearbetade vardera
- Både Create(5) och Create(14) listas i `createStatistics`

Dvs items från Create(14) flödar fysiskt in i Q transport under körning — kopplingen finns och fungerar. `connection_list` ser den bara inte statiskt.

## Rotorsaksanalys — `connection_list` (`simulation_backend.py:1705-1779`)

Algoritmen:
1. Bygger `node_map: nodeIndex -> [(blockId, connectorIdx, direction, name)]` genom att iterera varje blocks connectorer och läsa `NodeGetIDIndex`.
2. Hoppar över connectorer med `nodeIndex == 0` (okopplade) — rad 1733.
3. Bygger en koppling **endast** när en `nodeIndex` har:
   - exakt **2** endpoints → normal `from`/`to`-koppling (rad 1761), eller
   - **>2** endpoints → `type:"shared"` (rad 1771).

**Buggen:** En `nodeIndex` med exakt **1** endpoint **släpps tyst** — det finns ingen `else`-gren (rad 1771 är sista villkoret). En enkelsidig koppling försvinner alltså utan minsta diagnostik.

## Bekräftad rotorsak — array-connector-slots enumereras inte

`block_info`-bevisen avgör saken: **block 14 ÄR kopplat till Queue.ItemIn, via en array-slot.** Ingen modellfråga — en ren MCP-bugg.

ExtendSim numrerar array-slots med en särskild formel (`_get_array_connector_index`, `simulation_backend.py:1033-1047`):
- slot 0 = basindex
- slot N>0 = **256 − N** (slot 1 = connector 255, slot 2 = 254, …)

`connection_list` loopar bara `for conn_idx in range(num_cons)` (rad 1728), där `num_cons = GetNumCons(block)` = antalet **bas**-connectorer. Den besöker alltså **aldrig** connector 255/254/… → läser aldrig array-slot 1+. Create(14):s inkoppling sitter på Queue.ItemIn slot 1 (connector 255, nodeIndex 77) och blir därmed osynlig.

**Två samverkande defekter:**
1. **Primär:** `connection_list` enumererar inte array-connector-slots → varje 2:a+ inkoppling till en array-input (Queue.ItemIn, m.fl.) tappas.
2. **Sekundär:** noden blir då enkelsidig (`len(endpoints)==1`) och släpps **tyst** (ingen `else`-gren efter rad 1771) → ingen diagnostik om att något halvsetts.

## Föreslagen åtgärd

1. **Enumerera array-slots i `connection_list`.** För varje connector som är `isArray`, iterera dess slots via samma schema som `_find_free_array_slot` redan använder (`ConArrayGetNumCons` + `_get_array_connector_index`), och läs `NodeGetIDIndex` per slot. Då matchas Create(14):s nod 77 mot Queue.ItemIn slot 1.
2. **Sluta tyst släppa enkelsidiga noder.** Lägg till hantering för `len(endpoints) == 1`: returnera `{nodeIndex, type:"dangling", endpoint:{...}}` (eller en `danglingNodes`-lista) så halva kopplingar blir synliga istället för osynliga.
3. **Regressionstest:** modell med två Create → en Queue.ItemIn ska ge 2 inkopplingar till block 18 från connection_list.

## Fältnotering — bekräftelse från byggande agenten

> "So the honest answer to your question: partly. I can read connections via MCP, but `connection_list` alone is unreliable on this model — it silently dropped the Create(14)→Q transport edge. To read connectivity accurately I have to use `block_info` per block (the nodeIndex on each connector tells me whether/where it's wired) and confirm with an AppCapture screenshot. From now on I'll treat the visual + `block_info` as the source of truth, not `connection_list`."

Detta stämmer exakt med rotorsaken: `block_info` exponerar nodeIndex per connector (inkl. array-slot 1 = nodeIndex 77) medan `connection_list` missar den. **Workaround tills fixen är på plats:** lita på `block_info` per block + visuell kontroll, inte `connection_list`, för modeller med array-inputs.

## Relaterat (separat ärende, samma session)

`COM_TIMEOUT`/Ghost-state-dialogbuggen: backenden sätter `dismissed:true` med metod `win32gui SendInput`, men `SendInput` når inte ett "ghosted" (hängt) fönster — dialogen stängdes i praktiken manuellt av användaren. Spåras separat.
