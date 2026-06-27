# Design: M3 — `instantiate_pattern` (molekyl → H-block)

| Fält | Värde |
|------|-------|
| **Modul** | Pattern Mining, milstolpe M3 |
| **Datum** | 2026-06-27 |
| **Stack** | TypeScript-MCP + Python-COM-backend (tillägg till befintlig server) |
| **Status** | Design — godkänd ansats B, väntar spec-granskning |
| **Relaterat** | `docs/SimulationsMCP_Pattern_Mining_PRD.md` (v0.5), FR-13, FR-16, §7.1, §9.4, §12 |

## 1. Mål & avgränsning

Bygga den första byggbara biten av Pattern Mining-modulen: en **deterministisk `instantiate_pattern`** som tar en molekyl-definition + parameterbindningar och bygger den i ExtendSim **som ett H-block** (molekyl = H-block), samt en **schema-validering** av molekyl-definitionen. "Klart" = molekylen byggs på riktigt i ExtendSim **och** en kort smoke-körning bekräftar att den kör utan fel.

**Ingår:** molekyl-datamodell + schema-validering, `instantiate_pattern`-motor (ansats B), två handskrivna molekyler, smoke-run.
**Ingår inte:** lär-del/mining, `compose_flow` (M4), selektion/fallback, attribut-detektion. `model_validate` återanvänds (finns redan).

## 2. Bevisade COM-fakta (Steg 0-spike, 2026-06-27)

Dessa fynd styr designen och är dyrköpta — de gäller som hårda krav:

1. **Headless markering kräver `ActivateApplication()`.** Utan att ta ExtendSim till förgrunden registreras ingen markering via COM.
2. **Fler-blocks-markering ackumuleras INTE.** `AddBlockToSelection` honoreras bara för det *sista* blocket (verifierat visuellt + via `IsBlockSelected`). Därför kan vi **inte** markera N block och gruppera (ansats A är utesluten).
3. **Enkel-blocks-markering fungerar.** → seed:a H-blocket med *ett* block.
4. **`CreateHblock(namn)`** gör enkel-markeringen till ett H-block och **auto-skapar de yttre connectorerna** (= molekylens interface). `GetEnclosingHblockCon` mappar dem.
5. **`PlaceBlockInHblock(blockName, libName, x, y, HblockNum)`** placerar block inuti via **H-block-id** — ingen markering behövs.
6. **`hierarchy_get_contents` blandar riktiga block med connector-objekt.** Riktiga block har `library != ""`; connector-objekt har `library == ""`. Filtrera på detta.
7. **`execute_command` returnerar `success:true` även när MODL inte fick effekt** (falsk framgång). Verifiera alltid *effekten* (`IsBlockSelected`, `hierarchy_list`-count), lita aldrig på success-flaggan.

## 3. Komponenter (isolerade enheter)

| Enhet | Ansvar | Beroende | Ny/återanvänd |
|---|---|---|---|
| `molecule_schema.py` | Validera molekyl-definition + param-bindningar. **Ren, ingen COM.** | — | Ny |
| `instantiate.py` | Deterministisk motor (ansats B): molekyl + params → bygg H-block | `simulation_backend`, COM-app | Ny |
| `patterns/molecules/*.json` | Handskrivna molekyldefinitioner (§7.1) | — | Ny |
| `block_add`, `execute_command`, `block_set_value`, `hierarchy_list/get_contents`, `model_validate`, `simulation_run` | primitiver + validering + körning | — | Återanvänd |

`molecule_schema` och plan-genereringen i `instantiate` hålls COM-fria → enhetstestbara med fejk-COM (som `connection_list`-testet). Läggs i **egna filer**, inte i 10k-raders `simulation_backend.py`.

## 4. Dataflöde — `instantiate_pattern(molecule_id, params)`

```
1.  Ladda molekyl-JSON → molecule_schema.validate(molecule, params)   [fail-closed FÖRE bygge]
2.  Expandera placeholders ({{process_time}} → bundet värde)
3.  ActivateApplication()                                            [krav 1]
4.  block_add(seed-nodens lib/type)  → seedGlobalId                  [ansats B, krav 3]
5.  UnselectAll(); AddBlockToSelection(seedGlobalId)  [aktiv app, ETT block]; CreateHblock(namn)
6.  Verifiera: hierarchy_list-count ökade + hitta nya H-blockets id  [krav 7 — lita ej på success]
7.  För varje övrig nod:  PlaceBlockInHblock(lib, type, x, y, hblockId) → globalId   [krav 5]
8.  För varje intern kant:  MakeConnection(fromId, fromCon, toId, toCon)
9.  För varje nod:  block_set_value(...)   [fixed + bundna params]
10. Läs interface: för varje deklarerad inlet/outlet → GetEnclosingHblockCon(innerId, conNum) → yttre connector
11. Returnera { hblockId, interfaceMap, internalBlockIds }
12. (om begärt) simulation_run kort → assert inga fel   [smoke]
```
Steg 1–2 rent/deterministiskt; 3–12 mot COM via befintliga primitiver.

## 5. Molekylformat & första molekyler

Format = PRD §7.1 oförändrat (id, params, psg med placeholders, interface, attributes, example, provenance). En nod märks som **`seed: true`** (den som CreateHblock startar från); övriga placeras via `PlaceBlockInHblock`. Två handskrivna molekyler:

1. **`machine-with-breakdowns`** — Queue→Activity + Shutdown (täcker item- + shutdown-connector).
2. **`source-sink`** — Create→Exit (trivial molekyl, verifierar motorn på enklast möjliga fall).

## 6. Felhantering (fail-closed, FR-19)

- **Allt valideras före bygget** (`molecule_schema`): schema, required-params, kända blocktyper/portar. Vanligaste felklassen rör aldrig ExtendSim.
- **Verifiera effekter, inte success-flaggor** (krav 7): efter `CreateHblock` kontrolleras att H-blocket faktiskt finns; misslyckas det → fel + best-effort cleanup.
- **COM-fel mitt i bygget:** best-effort cleanup — `block_remove` av skapade block / H-blocket — och returnera ärligt fel med `createdBlockIds` + om cleanup lyckades. Aldrig tysta orphan-block.
- **Ingen gissning:** saknad param, okänd port → fel, aldrig default-gissning.

## 7. Testning (TDD, tre lager)

1. **Enhet, ren (fejk-COM):** `molecule_schema` — giltig molekyl, saknad required-param, okänd port/blocktyp, trasig interface-bindning.
2. **Enhet, ren (fejk-COM):** `instantiate`-plan — given molekyl+params, assert exakt sekvens av primitiv-/MODL-anrop (block_add seed, CreateHblock-namn, PlaceBlockInHblock-anrop per övrig nod med rätt hblockId, MakeConnection-par, block_set_value). Fejk-COM modellerar krav 2–7 (t.ex. att bara sista markeringen "fastnar", att contents blandar connector-objekt).
3. **Live (skippas utan ExtendSim):** bygg `machine-with-breakdowns` → assert H-block skapat, riktiga block (filtrerat `library!=""`) = förväntade, interface-connectorer mappade → `simulation_run` kort → assert inga fel.

## 8. Sekvensering

- **Steg 0 — KLAR:** COM-spike bevisade ansats B (se §2).
- **Steg 1:** `molecule_schema` + de två molekyl-JSON (TDD lager 1).
- **Steg 2:** `instantiate`-plan-motor, COM-fri orkestrering (TDD lager 2 med fejk-COM).
- **Steg 3:** COM-exekvering + interface-läsning + smoke-run (TDD lager 3, live).
- **Steg 4:** MCP-verktygsregistrering (`instantiate_pattern`) i TS-index + dispatch.

## 9. Öppna frågor

1. `ActivateApplication()` tar ExtendSim till förgrunden vid varje instansiering — acceptabelt för interaktivt modellbygge, men noteras för framtida obevakad körning.
2. Connect mellan block inuti H-block (`MakeConnection` med globala id) — antas fungera; verifieras i Steg 3.
3. Exakt mappning inlet/outlet → yttre connector-nummer via `GetEnclosingHblockCon` — verifieras i Steg 3.
