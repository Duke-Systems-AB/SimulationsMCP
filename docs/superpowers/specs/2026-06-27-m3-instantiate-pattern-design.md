# Design: M3 — `instantiate_pattern` (molekyl → H-block)

| Fält | Värde |
|------|-------|
| **Modul** | Pattern Mining, milstolpe M3 |
| **Datum** | 2026-06-27 |
| **Stack** | TypeScript-MCP + Python-COM-backend (tillägg till befintlig server) |
| **Status** | Design — **Väg 1 (COM-konstruktion) bevisad & bottnad end-to-end**, väntar spec-granskning |
| **Relaterat** | `docs/SimulationsMCP_Pattern_Mining_PRD.md` (v0.5), FR-13, FR-16, §7.1, §9.4, §12 |

## 1. Mål & avgränsning

Bygga den första byggbara biten av Pattern Mining-modulen: en **deterministisk `instantiate_pattern`** som tar en molekyl-definition + parameterbindningar och bygger den i ExtendSim **som ett H-block** (molekyl = H-block) via COM (**Väg 1**), samt en **schema-validering** av molekyl-definitionen. "Klart" = molekylen byggs på riktigt i ExtendSim **och** en kort smoke-körning bekräftar att items flödar genom den.

**Ingår:** molekyl-datamodell + schema-validering, `instantiate_pattern`-motor (Väg 1), två handskrivna molekyler, smoke-run.
**Ingår inte:** lär-del/mining, `compose_flow` (M4), selektion/fallback, attribut-detektion. `model_validate` återanvänds.

## 2. Bevisade COM-fakta (Steg 0-spikar, 2026-06-27)

Allt nedan är empiriskt verifierat mot live-ExtendSim — det styr designen som hårda krav.

1. **Headless markering kräver `ActivateApplication()`.** Utan förgrundsfokus registreras ingen markering via COM.
2. **Fler-blocks-markering går INTE.** `AddBlockToSelection` honoreras bara för det *sista* blocket (verifierat visuellt + `IsBlockSelected`); ingen marquee/rektangel-markering finns i MODL. Den naturliga vägen "markera del av kedja → gruppera" är därmed omöjlig via COM.
3. **Enkel-blocks-markering fungerar** → seed:a med *ett* block.
4. **Interface skapas BARA av `CreateHblock`, från kanter som korsar gränsen vid wrap-tillfället.** Ett okopplat block som wrappas får **noll** ytterportar. Man kan **inte** lägga till ytterportar i efterhand (koppling utifrån till inre block avvisas — falsk `success`).
5. **Receptet för interface (bevisat):** wrappa ett **enda redan inkopplat** block (stubbe→seed→stubbe). De korsande kanterna skapar inlopp+utlopp automatiskt. **Ta sedan bort stubbarna — ytterportarna består** (okopplade, redo att komposeras).
6. **`PlaceBlockInHblock(name, lib, x, y, hblockId)`** lägger block inuti via H-block-id — ingen markering.
7. **`MakeConnection`** fungerar mellan block inuti ett H-block (globala id).
8. **Infoga i flödet kräver disconnect-FÖRST.** För att sätta B mellan boundary-port och block A: `block_disconnect(boundary, A)` → `MakeConnection(boundary→B)` → `MakeConnection(B→A)`. Hoppar man över bortkopplingen kollapsar noderna (alla portar hamnar på samma nod). `block_disconnect` verifierar via `ClearConnection==1`.
9. **Boundary-connectorer** är connector-objekt inuti H-blocket (namn `Con0In`/`Con1Out`, `library==""`). Referera dem via deras lokala blockId vid disconnect/MakeConnection.
10. **Icke-linjära sidoanslutningar** funkar via `block_connect` på namn. För breakdowns är rätt par **`Shutdown.SD_ValueOut → Activity.SDV_In`** (värdesignal 0=upp/1=ner), *inte* item-paret.
11. **`hierarchy_get_contents` blandar riktiga block med connector-objekt** — riktiga har `library!=""`, filtrera.
12. **LITA ALDRIG PÅ `success:true`.** Falsk framgång observerad ≥3 ggr (dialog-dismiss, stubb-koppling, fel shutdown-par). Verifiera *effekten*: node-matchning, `hierarchy_list`-count, simuleringsutfall.
13. **`connection_list` visar INTE kopplingar till H-blockets ytterportar** (boundary-nivå). De är osynliga där men funktionellt verksamma (bevisat av simulering).
14. **Parametrisering:** Shutdown-breakdowns styrs av `SF_TBF_Arg1_prm` / `SF_TTR_Arg1_prm` (→ molekylens `mtbf`/`mttr`).

**End-to-end bevis:** `machine-with-breakdowns` (Queue→Activity + Shutdown) byggd helt via COM, smoke-körd → 990 items utan shutdown, 970 med (utnyttjande 99%→97%, breakdowns verksamma).

## 3. Komponenter (isolerade enheter)

| Enhet | Ansvar | Beroende | Ny/återanvänd |
|---|---|---|---|
| `molecule_schema.py` | Validera molekyl-definition + param-bindningar. **Ren, ingen COM.** | — | Ny |
| `instantiate.py` | Deterministisk Väg 1-motor: molekyl + params → bygg H-block, verifiera varje COM-steg | `simulation_backend`, COM-app | Ny |
| `patterns/molecules/*.json` | Handskrivna molekyldefinitioner (§7.1) | — | Ny |
| `block_add`, `block_connect`, `block_disconnect`, `execute_command`, `block_set_value`, `hierarchy_list/get_contents`, `model_validate`, `simulation_run` | primitiver + validering + körning | — | Återanvänd |

`molecule_schema` och plan-genereringen hålls COM-fria → enhetstestbara med fejk-COM (som `connection_list`-testet). Egna filer, inte i 10k-raders `simulation_backend.py`.

## 4. Konstruktionsalgoritm — `instantiate_pattern(molecule_id, params)`

```
1.  molecule_schema.validate(molecule, params)              [fail-closed, FÖRE bygge, COM-fritt]
2.  expandera placeholders ({{mtbf}} → bundet värde)
3.  ActivateApplication()                                   [krav 1]
4.  bygg seed-i-kontext:
      upstreamStub=Create, downstreamStub=Exit, seed=molekylens första flödesnod
      MakeConnection(upstreamStub→seed.in); MakeConnection(seed.out→downstreamStub.in)
5.  UnselectAll(); AddBlockToSelection(seedId); CreateHblock(namn)        [krav 3,4,5]
6.  verifiera H-block finns (hierarchy_list-count), hämta hblockId        [krav 12]
7.  block_remove(upstreamStub, downstreamStub); verifiera ytterportar kvar [krav 5]
8.  för varje övrig nod: PlaceBlockInHblock(lib,type,x,y,hblockId) → globalId   [krav 6]
9.  koppla item-flödeskedjan med disconnect-FÖRST:                        [krav 8,9]
      för att sätta B mellan boundary-inlopp och A:
        block_disconnect(inletConnObj, A.in)
        MakeConnection(inletConnObj → B.in); MakeConnection(B.out → A.in)
      verifiera node-topologin efter varje steg (distinkta noder, ingen kollaps)  [krav 12]
10. koppla icke-linjära sidoanslutningar på namn (t.ex. Shutdown.SD_ValueOut→Activity.SDV_In)
      verifiera via node-matchning                                       [krav 10,12]
11. block_set_value för params (inkl SF_TBF/SF_TTR för breakdowns)        [krav 14]
12. läs interface via GetEnclosingHblockCon(innerId, conNum) → ytterport  [krav 4]
13. returnera { hblockId, interfaceMap, internalBlockIds }
14. (om begärt) smoke: koppla temp Create/Exit till H-blockets ytterportar →
      simulation_run → assert items flödar (Exit itemsExited > 0) → koppla bort temp   [krav 13]
```
Steg 1–2 rent/deterministiskt; 3–14 mot COM, **varje steg effekt-verifierat** (aldrig success-flaggan).

## 5. Molekylformat & första molekyler

Format = PRD §7.1, med två tillägg motiverade av Väg 1:
- En nod märks **`seed: true`** (molekylens första item-flödesnod; den wrappas).
- Varje kant märks som **`flow`** (item-flöde, byggs med disconnect-först ordning) eller **`side`** (sidoanslutning som Shutdown, kopplas på namn).

Två handskrivna molekyler:
1. **`machine-with-breakdowns`** — Queue→Activity (flow) + Shutdown→Activity (side). Bevisad end-to-end (§2).
2. **`buffer`** — en enda Queue (trivial; seed måste vara ett mid-flödesblock med både ItemIn och ItemOut för att seed-wrap ska skapa rent inlopp+utlopp — en Create-seed saknar ItemIn och passar inte strategin).

## 6. Felhantering (fail-closed, FR-19)

- **Allt valideras före bygget** (`molecule_schema`): schema, required-params, kända blocktyper/portar.
- **Effekt-verifiering, inte success-flaggor** (krav 12): efter varje COM-steg läses faktisk topologi; avvikelse → fel + best-effort cleanup.
- **COM-fel mitt i bygget:** best-effort cleanup (`block_remove` av skapade block/H-blocket) + ärligt fel med `createdBlockIds`. Aldrig tysta orphan-block.
- **Ingen gissning:** saknad param, okänd port, kollapsad nod-topologi → fel, aldrig default-gissning.

## 7. Testning (TDD, tre lager)

1. **Enhet, ren (fejk-COM):** `molecule_schema` — giltig molekyl, saknad required-param, okänd port/blocktyp, trasig interface-bindning.
2. **Enhet, ren (fejk-COM):** `instantiate`-plan — assert exakt sekvens av COM-anrop för en given molekyl+params (seed-i-kontext, CreateHblock, stubb-borttagning, PlaceBlockInHblock per nod, disconnect-först-sekvens, side-connect, set_value). Fejk-COM modellerar krav 2–14 (bara sista markeringen fastnar; connector-objekt i contents; node-kollaps utan disconnect; falsk success).
3. **Live (skippas utan ExtendSim):** bygg `machine-with-breakdowns` → assert riktiga block (`library!=""`) = {Queue,Activity,Shutdown}, ren node-topologi, interface mappat → smoke `simulation_run` → assert items flödar + breakdowns sänker utnyttjande.

## 8. Sekvensering

- **Steg 0 — KLAR:** COM-spikar bevisade hela Väg 1 (se §2, inkl. end-to-end machine-with-breakdowns).
- **Steg 1:** `molecule_schema` + de två molekyl-JSON (TDD lager 1).
- **Steg 2:** `instantiate`-plan-motor, COM-fri orkestrering (TDD lager 2 med fejk-COM som modellerar krav 2–14).
- **Steg 3:** COM-exekvering med per-steg effekt-verifiering + interface-läsning + smoke-run (TDD lager 3, live).
- **Steg 4:** MCP-verktygsregistrering (`instantiate_pattern`) i TS-index + dispatch.

## 9. Öppna frågor (alla lösta 2026-06-27)

1. `ActivateApplication()` tar ExtendSim till förgrunden vid varje instansiering — acceptabelt för interaktivt modellbygge; noteras för framtida obevakad körning.
2. ~~Connect inuti H-block~~ **LÖST:** `MakeConnection` med globala id fungerar.
3. ~~Interface-mappning~~ **LÖST:** `GetEnclosingHblockCon` mappar inlopp/utlopp efter omkoppling.
4. ~~Hur exponeras interface?~~ **LÖST:** wrappa enkel seed i kontext → korsande kanter skapar interface → ta bort stubbar → interface består (krav 5). Stubb-trick utifrån fungerar INTE.
5. ~~Fler-blocks-molekyl med ren topologi?~~ **LÖST:** väx inifrån med disconnect-först (krav 8); bevisad ren `inlet→Queue→Activity→outlet` + icke-linjär Shutdown + smoke-run.
