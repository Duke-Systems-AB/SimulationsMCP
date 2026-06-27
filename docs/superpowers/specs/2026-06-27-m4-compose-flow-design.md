# Design: M4 — `compose_flow` (kedja molekyler till ett flöde)

| Fält | Värde |
|------|-------|
| **Modul** | Pattern Mining, milstolpe M4 |
| **Datum** | 2026-06-27 |
| **Stack** | TypeScript-MCP + Python-COM-backend (tillägg) |
| **Status** | Design godkänd — väntar spec-granskning |
| **Bygger på** | M3 `instantiate_pattern` (`src/instantiate.py`), PRD §7.3, §8, FR-17–FR-19 |

## 1. Mål & avgränsning

`compose_flow` tar en **flödesdefinition** (molekyl-instanser + wiring) och bygger ett helt processflöde i ExtendSim: instansierar varje molekyl som ett H-block (via M3) och kopplar ihop dem utlopp→inlopp. Validering är **fail-closed** på rollkompatibilitet och deklarerat attributkontrakt.

**Ingår:** `compose.py` med `compose_flow` + rena validerings-helpers, `attributes`-fält på molekyler, MCP-verktyg, tre TDD-lager.
**Ingår inte:** mining av flöden (M8), attribut-*detektion* (§9.6 — kontraktet använder *deklarerade* reads/writes), must/may-write-distinktion, sparade flödesfiler i bibliotek, best-effort rollback (uppskjutet, konsekvent med M3).

## 2. Flödesdefinition (inline-argument)

```json
{
  "id": "line-two-machines",
  "instances": [
    {"ref": "m1", "pattern": "machine-with-breakdowns", "params": {"process_time":3,"mtbf":120,"mttr":8}},
    {"ref": "m2", "pattern": "machine-with-breakdowns", "params": {"process_time":2,"mtbf":90,"mttr":6}}
  ],
  "wiring": [ {"from": "m1.out", "to": "m2.in"} ]
}
```
`out`/`in` refererar molekylens interface-portnamn (`molecule.interface.inlets/outlets[].port`).

## 3. Komponenter (isolerade enheter)

| Enhet | Ansvar | Beroende | Ny/återanvänd |
|---|---|---|---|
| `compose.py: validate_flow(flow_def, molecules)` | Ren validering: unika refs, kända portar, rollkompatibilitet, attributkontrakt över wiring-DAG. **Ingen COM.** | — | Ny |
| `compose.py: compose_flow(flow_def, ops)` | Orkestrering: instansiera varje molekyl, koppla H-block enligt wiring | `instantiate` (build_molecule, _load_molecule), COM-app via ops | Ny |
| `instantiate.build_molecule`, `RealOps`, `_load_molecule`, `instantiate_pattern` | Bygga en molekyl som H-block | — | Återanvänd (oförändrat) |
| molekyl-JSON: nytt `attributes:{reads,writes}` (default tomt) | Deklarerat attributkontrakt | — | Tillägg |

## 4. Dataflöde — `compose_flow(flow_def, ops)`

```
1. molecules = { pattern: _load_molecule(pattern) for varje unik instans-pattern }
2. validate_flow(flow_def, molecules)                 [rent, fail-closed FÖRE bygge]
3. instances = {}
   för varje instans i: res = build_molecule(molecules[i.pattern], i.params, ops)
                        instances[i.ref] = {hblockId, interfaceMap}
4. för varje wiring {from: A.outPort, to: B.inPort}:
     outOuter = instances[A].interfaceMap[outPort]["outerCon"]
     inOuter  = instances[B].interfaceMap[inPort]["outerCon"]
     ops.connect(instances[A].hblockId, outOuter, instances[B].hblockId, inOuter)   [verifierad]
5. returnera { success, flowId, instances:{ref:{hblockId,interfaceMap}}, wiring:[...] }
```
H-block↔H-block-koppling använder samma `ops.connect` som M3; H-blockens **yttre** connectorer rapporterar noder normalt (till skillnad från de inre boundary-objekten), så node-verifieringen i `RealOps.connect` fungerar oförändrad.

## 5. Validering (`validate_flow`, fail-closed, FR-17–19)

1. **Unika instans-refs.** Dubblett → fel.
2. **Wiring-refs & portar finns.** `from`=`ref.port` måste vara en känd instans och ett deklarerat **utlopp** på dess molekyl; `to` en känd instans och ett deklarerat **inlopp**. Annars fel.
3. **Rollkompatibilitet (FR-17).** `role(utlopp) == role(inlopp)` (t.ex. item→item). Rollkrock → fel.
4. **Attributkontrakt (FR-18).** Bygg riktad graf: nod=instans, kant=wiring (from-instans → to-instans). För varje instans `I` och varje attribut `X` i dess molekyls `attributes.reads`: någon instans som är **uppströms** `I` (når `I` via kanter) måste ha `X` i sin `attributes.writes`. Saknas → fel. (Deklarerade writes räcker; must/may-write ej distingerat än — noteras.)

## 6. Felhantering (fail-closed, FR-19)

- All validering före någon COM-operation.
- Okänd ref/port, rollkrock, brutet attributkontrakt → fel, inget byggs.
- COM-fel mitt i bygget → `BuildError` propageras; svaret anger vilka H-block som hann skapas. Best-effort rollback uppskjutet (som M3).
- `compose_flow`-entry mappar `MoleculeError`→`INVALID_MOLECULE`, valideringsfel→`INVALID_FLOW`, övrigt→`COMPOSE_FAILED`.

## 7. Testning (TDD, tre lager)

1. **Enhet, rent:** `validate_flow` med in-memory molekyl-dicts — giltigt flöde pass; dubblett-ref, okänd port, rollkrock, läser-attribut-utan-uppströms-skrivare → fel; attributkontrakt uppfyllt när uppströms instans skriver attributet.
2. **Enhet, fejk-COM:** `compose_flow` med `FakeOps` (utökad efter behov) — assert varje molekyl instansierad och att rätt `connect`-anrop görs mellan H-block med rätt outer-index ur interfaceMap.
3. **Live (skippas utan ExtendSim):** komponera två `machine-with-breakdowns` (m1→m2), koppla Create→m1.inlopp och m2.utlopp→Exit, kör → assert items flödar genom **båda** maskinerna (Exit itemsExited > 0 på den anslutna Exit-blocken).

## 8. Sekvensering

- **Steg 1:** lägg `attributes:{reads,writes}` (tomt) på molekylerna; molekyl-schema defaultar tomt om saknas.
- **Steg 2:** `validate_flow` (TDD lager 1).
- **Steg 3:** `compose_flow`-orkestrering, COM-fri via ops (TDD lager 2, FakeOps).
- **Steg 4:** entry point + dispatch + live-test (TDD lager 3).
- **Steg 5:** MCP-verktygsregistrering i TS.

## 9. Öppna frågor

1. Attribut-detektion saknas → kontraktet validerar bara *deklarerade* reads/writes (tomma idag). Blir meningsfullt när §9.6 finns. Inget hinder för M4-strukturen.
2. Inga externa attributkällor modelleras (bara instanser i flödet kan tillfredsställa reads). Acceptabelt för M4.
3. Best-effort rollback vid partiellt byggt flöde uppskjutet — samma beslut som M3.
