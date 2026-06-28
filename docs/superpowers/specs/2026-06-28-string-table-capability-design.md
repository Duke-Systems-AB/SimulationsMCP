# Design: Sträng-tabell-kapabilitet (`table_get` / `table_set`)

| Fält | Värde |
|------|-------|
| **Modul** | Pattern Mining — tvärgående kapabilitet (avblockerare) |
| **Datum** | 2026-06-28 |
| **Stack** | TypeScript-MCP + Python-COM-backend (tillägg) |
| **Status** | Design godkänd — väntar spec-granskning |
| **Avblockerar** | M6 attribut-detektion (läs IVars/OVars), `tag-items` (skriv Set-attributtabell), `resource-machine` (definiera namngiven pool) |

## 1. Mål & avgränsning

ExtendSims dialog-**sträng-tabeller** (`*_ttbl`, typ stringtable) är den återkommande väggen i pattern-mining-arbetet. De befintliga verktygen `block_get_value`/`block_set_value` är **numeriska** och kraschar på sträng-celler (`could not convert string to float`). Detta tillägg ger två nya MCP-verktyg som läser och skriver sträng-celler via COM:

- `table_get(block_id, var, row, col)` → läser en sträng-cell.
- `table_set(block_id, var, value, row, col)` → skriver en sträng-cell, **skriv-läs-tillbaka-verifierat och fail-closed**.

**Ingår:** ny modul `src/dialog_table.py` (ren COM-tunn wrapper + entries), dispatch-registrering, två MCP-verktyg, enhetstester (mock-backend) + live-test.
**Ingår inte:** rad-/kolumn-insättning eller borttagning i tabeller (endast celler i befintliga rader); popup-/menyval (`*_pop`) — de är numeriska och hanteras redan av `block_set_value`; automatisk upptäckt av *vilken* var/kolumn ett attribut binds i (det är M6:s ansvar, denna kapabilitet är det verktyg M6 använder).

## 2. Bevisad COM-grund (live-inspektion 2026-06-28)

På ett `Equation(I)`-block:
- `GetDialogVariable(block, "IVars_ttbl", 0, 1)` → `'inCon0'` — **sträng-läsning fungerar.**
- `SetDialogVariable(block, "OVars_ttbl", "testAttr", 0, 1)` följt av återläsning → `'outCon0'` — **skrivningen persisterade INTE** på den auto-namngivna cellen (cellen var blockstyrd/skrivskyddad).

Slutsats som formar designen: läsning är rättfram; **skrivning får aldrig antas lyckas** bara för att anropet returnerar utan fel. Cellen kan vara blockstyrd och tyst förkasta värdet. Därför är `table_set` skriv-läs-tillbaka-verifierat och fail-closed (samma hårda regel som genomsyrar M3/M4: *lita aldrig på COM `success` — effekt-verifiera*).

**Mekanism (korrigerad efter live-test 2026-06-28):** `GetDialogVariable`/`SetDialogVariable` är **MODL-språkfunktioner**, INTE direkta COM-metoder. De anropas via `app.Execute("<modl>;")` och resultat hämtas via `app.Request("System", "globalStr0+:0:0:0")`. (Ett tidigt utkast antog direkta COM-metoder + `SetDialogVariableNoMsg`; ett live-test gav `AttributeError` — den vägen finns inte.)

**Befintliga hjälpfunktioner återanvänds** — `simulation_backend.py` har redan precis denna routing (rad ~101–147):
- `_get_var(app, block_id, var_name, row, col)` — för `_ttbl`/`_dtbl`/`_dtxt`-suffix kör den `globalStr0 = GetDialogVariable(...)` + `Request` och returnerar strängen. **Detta är kärnan i `table_get`.**
- `_set_var_string(app, block_id, var_name, value, row, col)` — kör `SetDialogVariable(..., "value", row, col)` (MODL-sträng-escapad). **Detta är kärnan i `table_set`.**

`table_get`/`table_set` blir alltså **tunna wrappers** runt dessa befintliga hjälpfunktioner, där `table_set` lägger till läs-tillbaka-verifieringen.

## 3. Komponenter

| Enhet | Ansvar | Beroende | Ny/återanvänd |
|---|---|---|---|
| `dialog_table.py: table_get_entry(block_id, var, row, col)` | MCP-entry: läs sträng-cell via `_get_var`; success/error-dict | `simulation_backend._get_var` | Ny |
| `dialog_table.py: table_set_entry(block_id, var, value, row, col)` | MCP-entry: `_set_var_string` → `_get_var` (läs tillbaka) → verifiera; fail-closed | `simulation_backend._set_var_string` + `_get_var` | Ny |
| `simulation_backend.py: COMMANDS` | Dispatch: `"table_get"`, `"table_set"` → entries | — | Modifierad |
| `index.ts` + `backend.ts` | MCP-verktygsregistrering `table_get`, `table_set` + zod-scheman + backend-helpers | — | Modifierad |

Designprincip: `dialog_table.py` är en **tunn, fokuserad** modul som bara gör cell-IO. Den innehåller ingen domänlogik (attribut-tolkning bor i M6:s `attribute_detect.py`, pool-config i resp. molekyl).

## 4. Dataflöde

### `table_get(block_id, var, row, col)`
```
1. hämta COM-app (befintlig backend-koppling)
2. value = _get_var(app, block_id, var, row, col)   # MODL GetDialogVariable + Request
3. returnera {success: true, value: str(value)}
4. vid COM/MODL-fel → {success: false, errorCode: "TABLE_READ_FAILED", error: <meddelande>}
```

### `table_set(block_id, var, value, row, col)`
```
1. hämta COM-app
2. _set_var_string(app, block_id, var, value, row, col)   # MODL SetDialogVariable (escapad)
   vid fel här → {success: false, errorCode: "TABLE_WRITE_FAILED", error: <meddelande>}
3. readback = _get_var(app, block_id, var, row, col)       # effekt-verifiering (MODL)
   vid fel här → {success: false, errorCode: "TABLE_READ_FAILED", error: <meddelande>}
4. om str(readback) == str(value):
       returnera {success: true, value: str(readback)}
   annars:
       returnera {success: false, errorCode: "TABLE_WRITE_REJECTED",
                  requested: str(value), actual: str(readback)}
```

> Skriv- och verifierings-läsningen ligger i **separata** try-block: ett skrivfel ger `TABLE_WRITE_FAILED`, ett fel under den efterföljande verifieringsläsningen ger `TABLE_READ_FAILED`. Då rapporteras inte en redan persisterad skrivning felaktigt som "write failed" (vilket annars kunde få anroparen att dubbel-skriva vid retry).

## 5. Fel-hantering & fail-closed (FR-22)

- **`table_set` lyckas bara om återläsningen matchar det skrivna värdet.** En blockstyrd/skrivskyddad cell (som den bevisade `OVars_ttbl`-cellen) ger `TABLE_WRITE_REJECTED` med både `requested` och `actual` — anroparen får veta exakt att och varför skrivningen inte tog.
- Felkoder: `TABLE_READ_FAILED` (läsning, eller verifieringsläsning efter skrivning, kastade), `TABLE_WRITE_FAILED` (själva skrivningen kastade), `TABLE_WRITE_REJECTED` (skrivning tyst, värdet fastnade inte).
- Inga tysta tomma svar: ett misslyckande är alltid en explicit `success:false` med felkod.

## 6. Testning (TDD)

1. **Enhet, `table_get` (mock-backend):** mock-app vars `GetDialogVariable` returnerar `"attrA"` → `{success:true, value:"attrA"}`. Mock som kastar → `{success:false, errorCode:"TABLE_READ_FAILED"}`.
2. **Enhet, `table_set` lyckad (mock-backend):** mock där `GetDialogVariable` efter skrivning returnerar det skrivna värdet → `{success:true, value:...}`; assert att `SetDialogVariableNoMsg` anropades med rätt argument **och** att readback skedde.
3. **Enhet, `table_set` förkastad (mock-backend):** mock där readback ≠ skrivet värde → `{success:false, errorCode:"TABLE_WRITE_REJECTED", requested, actual}`.
4. **Enhet, `table_set` COM-fel:** mock där `SetDialogVariableNoMsg` kastar → `{success:false, errorCode:"TABLE_WRITE_FAILED"}`.
5. **Enhet, MODL-escaping:** `table_set` med ett värde som innehåller `"` → assert att `_set_var_string` escapar strängen korrekt (ingen trasig MODL).
6. **Live (skippas utan ExtendSim):**
   - Läs en känd cell på ett `Equation(I)`-block (`IVars_ttbl[0,1]`) → assert sträng tillbaka.
   - Skriv en **skrivbar** cell och assert readback == värdet.
   - Försök skriv en **skrivskyddad** cell (den bevisade auto-namngivna `OVars_ttbl`-cellen) → assert `success:false, errorCode:"TABLE_WRITE_REJECTED"` (verifierar fail-closed mot verklig ExtendSim).

`dispatch-coverage.test.ts`: verktygsantalet **96 → 98** (två nya verktyg).

## 7. Sekvensering

- **Steg 1:** `table_get_entry` + dispatch + enhetstester (mock).
- **Steg 2:** `table_set_entry` (skriv-läs-tillbaka, fail-closed) + dispatch + enhetstester (lyckad / förkastad / COM-fel / NoMsg-fallback).
- **Steg 3:** MCP-registrering (`index.ts`/`backend.ts`) + uppdatera `dispatch-coverage.test.ts` till 98.
- **Steg 4:** Live-test mot ExtendSim (läs, skriv-skrivbar, skriv-skrivskyddad fail-closed).

## 8. Öppna frågor

1. Vilka celler i `Equation(I)`-tabellerna är skrivbara vs blockstyrda — kartläggs i live-steget; påverkar inte API:t (fail-closed hanterar båda).
2. Verktygen återanvänder backendens `_get_var`/`_set_var_string` (MODL via `Execute`/`Request`). Inga nya COM-metoder behövs — den vägen är redan beprövad i produktion.
3. Värdejämförelsen är sträng-exakt (`str(readback) == str(value)`). Om ExtendSim normaliserar värden (trimmar blanksteg, ändrar skiftläge) kan en korrekt skrivning rapporteras som förkastad — bevakas i live-steget; vid behov införs en normaliserande jämförelse, men default är exakt (konservativt/fail-closed).
