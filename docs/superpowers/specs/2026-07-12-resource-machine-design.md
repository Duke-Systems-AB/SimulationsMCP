# Design: resource-machine — funktionell resurs-begränsad maskin (fix + molekyl)

| Fält | Värde |
|------|-------|
| **Modul** | Pattern Mining — strukturerade skrivare, resource-machine-molekylen |
| **Datum** | 2026-07-12 |
| **Stack** | TypeScript-MCP + Python-COM-backend (fix + tillägg) |
| **Status** | Design godkänd — väntar spec-granskning |
| **Bygger på** | M3 (instantiate), string-table-kapabiliteten, tag-items (attribut-config-mönstret) |

## 1. Mål & avgränsning

Gör `resource-machine`-molekylen **funktionell**: en maskin (Activity) vars genomflöde begränsas av en namngiven Resource Pool, med full **acquire → use → release**-cykel. Detta kräver att tre trasiga backend-funktioner fixas och att molekylen samt en layout-algoritm byggs.

Hela receptet är **live-verifierat 2026-07-12**: en modell `Create→Queue→Activity→Resource Pool Release→Exit` + Resource Pool körde **49 items** genom hela cykeln (pool-utilization 49 %, alla resurser återlämnade), konfigurerad **helt i kod**.

**Ingår:** ny ren kärna `resource_pool_config.py` (fixar/ersätter de 3 trasiga funktionerna, effekt-verifierad, fail-closed), `resource-machine.json`-molekyl + config-applicering i instantiate, en layout-algoritm i instantiate (gäller alla molekyler), fix av `simulation_run`:s sluttid, enhetstester + live-test.
**Ingår inte:** avancerade resurspooler (`AR_*`-läge), shift-scheman, flera pooler per kö, resurs-attribut-baserad release (`ReleaseBy=attribut`), kostnadsmodellering.

## 2. Live-verifierat recept (bevisad mekanism)

| Block | Var | Värde / metod |
|---|---|---|
| **Resource Pool** | `ResourcePoolName` | pool-namn (sträng) via `SetDialogVariable`/`_set_var_string` |
| | `NumServ` | kapacitet (numeriskt) |
| **Queue** | `QueueType_pop` | `2` (Resource Pool-läge) |
| | `ResourceTable[0,0]` | pool-**namn** (sträng) via **`SetDialogVariable`** — *inte* `_set_var` (ingen `_ttbl`-suffix → routas fel till `SetVariableNumeric` = tyst no-op) |
| | `ResourceTable[0,1]` | antal resurser kön kräver (t.ex. 1) |
| **Resource Pool Release** | `ResourcePoolName` + `ServerBlockNum` | sätts **direkt** (sträng-namn + poolens blocknummer) via `SetDialogVariable`. **INTE** `Serverblocks_pop`-popupen — dess `RPNames`-lista är tom i ett färskt H-block; blocket resolvar poolen via `FindRPBlock(ResourcePoolName)` vid CheckData (uppdaterat 2026-07-12 efter läsning av blockets ModL-källa, se `docs/resource-machine-hblock-resource-pool-solution.md`) |
| | `ResourcePoolName` | pool-namn (sträng) — läses tillbaka för att verifiera vilket index som valdes |
| | `NumReleased_PRM` | antal som återlämnas (t.ex. 1) |
| **Flöde** | | `Create→Queue→Activity→Release→Exit` + wire `Pool.ValuesOut(con 1) → Queue.ResourcePoolQuantityIn(con 5)` |

**Kritiskt fynd (root cause för tidigare 0-genomflöde):** utan att peka ut poolen på Release-blocket (`Serverblocks_pop`) aborterar ExtendSim hela simmen vid t=0 med *"Resource pool name not specified in Resource Pool Release ... CHECKDATA message handler"* — `currentTime` förblir 0, 0 items. `Serverblocks_pop` är en **int-index-popup**; man kan inte matcha strängen "Pool1" (en popup-läsning ger tillbaka indexet, inte etiketten). Rätt index hittas genom att sätta index och läsa tillbaka `ResourcePoolName`.

## 3. Trasiga funktioner som ersätts (alla ger falsk framgång idag)

| Funktion | Bugg | Fix |
|---|---|---|
| `queue_set_resource_pool` | skriver `ResourceTable` med `_set_var` (→ `SetVariableNumeric`, tyst no-op) **och** skriver block-ID istället för pool-namn | `SetDialogVariable` + pool-namn, effekt-verifierat |
| `resource_pool_release_set_config` | sätter **aldrig** poolen (bara `NumReleased_PRM`) → CHECKDATA-abort | sätt `ResourcePoolName` + `ServerBlockNum` (poolens blocknummer) + `NumReleased_PRM`, verifierat |
| `resource_pool_set_config` | ingen effekt-verifiering (skriver namn+kapacitet men litar på COM) | återläs namn (via `GetDialogVariable`) + kapacitet |
| `simulation_run(end_time=X)` | `endTime = X`-tilldelning sätter **inte** sluttiden (förblir modell-default 1000) | använd `SetRunParameter(end_time, dt)` (bevisat i `test_distribution_roundtrip.py`) |

## 4. Komponenter

| Enhet | Ansvar | Ny/ändrad |
|---|---|---|
| `resource_pool_config.py: configure_pool(be, id, name, capacity)` | Ren kärna: `ResourcePoolName`+`NumServ`, återläst, fail-closed | Ny |
| `resource_pool_config.py: configure_queue_pool(be, id, pool_name, qty)` | `QueueType_pop=2` + `ResourceTable` via `SetDialogVariable`, återläst | Ny |
| `resource_pool_config.py: configure_release(be, id, pool_name, pool_block_id=None, qty)` | Sätt release-blockets **`ResourcePoolName`** (sträng) + **`ServerBlockNum`** (poolens blocknummer) direkt via `SetDialogVariable`, + `NumReleased_PRM`. `pool_block_id` fås från bygget (RealOps) eller slås upp via namn (`find_resource_pool`). Effekt-verifierat (läs `ResourcePoolName` tillbaka); fail-closed om poolen inte hittas. | Ny |
| `simulation_backend`: de 3 gamla funktionerna | Delegerar till kärnan (behåller yttre kontrakt) | Ändrad |
| `simulation_backend.simulation_run` | Sätt sluttid via `SetRunParameter` | Ändrad |
| `instantiate.py`: layout-fas | Positionera block: flödesnoder längs x (Δx≈120), sido-noder under (Δy≈140) | Ny |
| `instantiate.py`: resurs-config-fas | Applicera nod-config (pool/queue/release) efter bygget | Ny |
| `resource-machine.json` | Molekyl: queue→activity→release + resource-pool sido-nod + wire + config | Ny |

Backend-kontraktet kärnan använder: `get_extendsim_app`, `_validate_model_open`, `_validate_block_type`, `_set_var`, `_set_var_string`, `_get_var` (+ rå `GetDialogVariable`/`SetDialogVariable` för `ResourceTable` och popup-återläsning). Samma FakeBackend-teststil som `dialog_table.py`/`attribute_config.py`.

## 5. Molekyl `resource-machine.json`

```json
{
  "id": "resource-machine", "kind": "molecule",
  "intent": "Maskin vars genomflöde begränsas av en resurspool",
  "params": {
    "process_time": { "required": false, "default": 1 },
    "capacity":     { "required": false, "default": 2 },
    "pool_name":    { "required": false, "default": "Pool1" }
  },
  "nodes": [
    { "ref": "q",   "lib": "Item.lbr", "type": "Queue" },
    { "ref": "act", "lib": "Item.lbr", "type": "Activity", "params": { "D": "{{process_time}}" } },
    { "ref": "rel", "lib": "Item.lbr", "type": "Resource Pool Release", "seed": true },
    { "ref": "rp",  "lib": "Item.lbr", "type": "Resource Pool" }
  ],
  "resourcePool": {
    "poolNode": "rp", "queueNode": "q", "releaseNode": "rel",
    "name": "{{pool_name}}", "capacity": "{{capacity}}", "qty": 1
  },
  "edges": [
    { "kind": "flow", "from": "q.ItemOut",   "to": "act.ItemIn" },
    { "kind": "flow", "from": "act.ItemOut", "to": "rel.ItemIn" },
    { "kind": "side", "from": "rp.ValuesOut", "to": "q.ResourcePoolQuantityIn" }
  ],
  "interface": {
    "inlets":  [ { "port": "in",  "binds": "q.ItemIn",   "role": "item" } ],
    "outlets": [ { "port": "out", "binds": "rel.ItemOut", "role": "item" } ]
  }
}
```

Notera: seed = flödeskedjans **tail**. Kedjan är `q→act→rel`, så seed = `rel` (bunden till outlet). Instantiate applicerar `resourcePool`-blocket via de tre config-funktionerna, med `{{...}}` upplösta.

## 6. Layout-algoritm

Idag placeras alla block på fasta koordinater → de staplas. Ny fas i `build_molecule`: efter att blocken finns, positionera dem — flödesnoderna i inlet→outlet-ordning längs x med fast Δx, sido-noder en rad under. Använder befintlig `block_move`/positionering, effekt-verifierat. Enkel, deterministisk, och gäller alla molekyler (löser stapling generellt).

## 7. Fail-closed

- Kärnan är ren; all COM injiceras. Ingen skrivning litar på success-flaggan (återläsning + jämförelse).
- `configure_release` läser tillbaka release-blockets `ResourcePoolName` efter skrivningen (effekt-verifiering); hittas ingen pool med namnet → explicit `RELEASE_POOL_NOT_FOUND`, aldrig falsk framgång.
- `ResourceTable`/`ResourcePoolName` läses tillbaka via `GetDialogVariable` (rätt metod för sträng/popup), inte `GetVariableNumeric` (som ger `-nan(ind)`).
- Config-fel i instantiate → `BuildError` → hela bygget failar högt, inget halvkonfigurerat H-block.

## 8. Testning (TDD)

- **Enhet, rent (FakeBackend):** varje config-funktion — lyckad skrivning+återläsning; återläsning ≠ begärt → rejected; skriv/läs-fel → distinkta felkoder; `configure_release` löser pool via `pool_block_id` eller namn-uppslag / ger `RELEASE_POOL_NOT_FOUND` när poolen saknas.
- **Enhet, molekyl (FakeOps):** `build_molecule("resource-machine")` producerar rätt config-anrop (pool/queue/release) + layout-anrop; validering.
- **Enhet, layout (FakeOps):** block får icke-överlappande positioner.
- **Live (`skipif`):** bygg + `SetRunParameter` + kör → assert `itemsExited > 0` (bevisat möjligt: 49 items).

## 9. Sekvensering

- **Steg 1:** `resource_pool_config.py` kärnor + FakeBackend-enhetstester (inkl. index-sökning).
- **Steg 2:** skriv om de 3 backend-funktionerna att delegera + mock-test.
- **Steg 3:** fixa `simulation_run` (SetRunParameter) + test.
- **Steg 4:** layout-fas i instantiate + FakeOps-test.
- **Steg 5:** resurs-config-fas i instantiate + molekyl-schema-stöd för `resourcePool` + FakeOps-test.
- **Steg 6:** `resource-machine.json` + molekyl-enhetstest.
- **Steg 7:** packaging (`resource_pool_config.py` i `copy-files`) + full svit + live-test.

## 10. Öppna frågor (mestadels lösta av live-discovery)

1. **LÖST (2026-07-12):** pool-länkningen sker via release-blockets `ResourcePoolName` + `ServerBlockNum`, resolvat av blockets `FindRPBlock` vid CheckData (matchar Resource Pool med samma namn i samma H-block). `Serverblocks_pop`-popupen var en återvändsgränd (tom `RPNames`-lista i färskt H-block). Se `docs/resource-machine-hblock-resource-pool-solution.md`. Flera pooler hanteras robust: `find_resource_pool` matchar på namn.
2. Wire-riktning/-verifiering `Pool.ValuesOut→Queue.ResourcePoolQuantityIn` i H-block-kontext (M3 gör sido-kanter node-verifierat — bör fungera, bekräftas i live-test).
