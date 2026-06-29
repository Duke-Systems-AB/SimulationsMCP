# Design: M6 (steg 1) — Attribut-detektion för equation-block

| Fält | Värde |
|------|-------|
| **Modul** | Pattern Mining, milstolpe M6 (§9.6), första steget |
| **Datum** | 2026-06-28 |
| **Stack** | TypeScript-MCP + Python-COM-backend (tillägg) |
| **Status** | Design godkänd — väntar spec-granskning |
| **Bygger på** | PRD §9.6 (attribut-detektion), FR-22; M3–M5 |

## 1. Mål & avgränsning

Första steget mot attribut-detektion: en `detect_attributes(block_id)` som för **equation-block** härleder vilka item-attribut blocket **läser** och **skriver**, genom att läsa blockets in/ut-variabeltabeller via COM. Detta är PRD:ns primärväg (§9.6.3) och gör M4:s attributkontrakt verksamt för equation-baserade molekyler.

**Ingår:** `attribute_detect.py` (`detect_attributes` med injicerad reader + RealReader + MCP-entry), writer-katalog för equation-blocktyper, fail-closed, enhetstester + ett live-test (manuellt fixtur).
**Ingår inte (senare M6-steg):** strukturerade skrivare Set/Create (§9.6.2 — tabellbaserad config, samma vägg som tag-items), ModL-kod + include-parse (§9.6.4), must/may-write-kontrollflöde (§9.6.6), runtime cross-check (§9.6.7), automatisk om-deklaration av molekylernas `attributes`.

## 2. Bevisad blockstruktur (live-inspektion 2026-06-28)

`Equation(I)` (Item.lbr) har connectorer `ItemIn, iVarsIn, ItemOut, oVarsOut` och dialog-tabeller:
- **`IVars_ttbl`** — in-variabler (läser från attribut) → **reads**
- **`OVars_ttbl`** — ut-variabler (skriver till attribut) → **writes**
- `Incl_FileNames_ttbl` (include-filer, §9.6.4 senare), `Equation_dtxt` (ModL-koden, senare).

Tabeller läses cell-för-cell via befintliga `block_get_value(blockId, tableVar, row, col)` (stödjer redan row/col). Den exakta **kolumnen** för attributnamnet i IVars/OVars upptäcks i implementationens första steg (read-only).

## 3. Komponenter

| Enhet | Ansvar | Beroende | Ny/återanvänd |
|---|---|---|---|
| `attribute_detect.py: detect_attributes(block_id, reader)` | Ren mappningslogik: equation-tabeller → {reads, writes, confidence}. **Ingen direkt COM** (injicerad reader). | — | Ny |
| `attribute_detect.py: RealReader` | COM-baserad reader: blocktyp + tabell-rad/cell via `simulation_backend` (effekt-verifierad) | `simulation_backend` | Ny |
| `attribute_detect.py: detect_attributes_entry(block_id)` | MCP-entry: RealReader + success/error-dict | — | Ny |
| `_EQUATION_TYPES` | Writer-katalog: `{Equation(I), Query Equation (I), Queue Equation}` | — | Ny |

Reader-gränssnittet (implementeras av RealReader + FakeReader i test): `block_type(block_id) -> str`; `table_rows(block_id, table_name) -> list[dict]` (varje rad = en variabel med dess namn + bundet attribut + ev. typ).

## 4. Dataflöde — `detect_attributes(block_id, reader)`

```
1. t = reader.block_type(block_id)
2. om t inte i _EQUATION_TYPES: returnera {reads: [], writes: [], confidence: "none"}
3. reads  = attribut bundna i reader.table_rows(block_id, "IVars_ttbl")
   writes = attribut bundna i reader.table_rows(block_id, "OVars_ttbl")
4. en rad utan tydlig attributbindning (men tabellen finns) → lägg "?" i resp. lista, confidence "low"
5. annars confidence "high"
6. returnera {reads, writes, confidence}
```

## 5. Fail-closed (FR-22 + PRD §9.6.7)

- Mappningslogiken är ren och deterministisk.
- `RealReader` effekt-verifierar varje `block_get_value` (success); en cell som inte kan läsas → raden markeras oläslig → "?" + confidence "low" (aldrig tyst tom när en tabell finns).
- Okänt/icke-equation blocktyp → `confidence: "none"` (ärligt "vet ej", inte "skriver inget").

## 6. Testning (TDD)

1. **Enhet, rent (FakeReader):** equation-block med IVars=[attrA], OVars=[attrB] → reads=[attrA], writes=[attrB], confidence high; tom tabell → reads/writes tomma; rad utan attributbindning → "?" + low; icke-equation typ → confidence none.
2. **Live (skippas utan ExtendSim):** kräver ett **manuellt konfigurerat** `Equation(I)`-block (du binder en in-variabel och en ut-variabel till kända attribut i UI:t — tabell-konfig kan vi inte skriva programmatiskt ännu). Sedan `detect_attributes_entry(blockId)` → assert rätt reads/writes. Markeras `skipif` om blocket/fixturen saknas.

## 7. Sekvensering

- **Steg 1:** read-only-inspektion av IVars_ttbl/OVars_ttbl-kolumner (hitta attribut-kolumnen) + bekräfta cell-läsning via `block_get_value(row,col)`.
- **Steg 2:** ren `detect_attributes(block_id, reader)` + FakeReader-enhetstester (TDD).
- **Steg 3:** `RealReader` (block_type + table_rows via backend, effekt-verifierad) + enhetstest med mock-backend.
- **Steg 4:** `detect_attributes_entry` + dispatch + live-test (manuellt fixtur).
- **Steg 5:** MCP-verktygsregistrering `detect_attributes`.

## 8. Öppna frågor

1. Exakt kolumn-layout i IVars_ttbl/OVars_ttbl (var attributnamnet ligger, hur "ingen bindning" ser ut) — upptäcks i Steg 1.
2. Live-fixturen är manuell (tabell-skrivning saknas) — samma rot som tag-items/resource-machine. En framtida "skriv dialog-tabell"-kapabilitet skulle göra fixturen automatisk.
3. Andra equation-blocktyper (`Query Equation (I)`, `Queue Equation`) antas ha samma IVars/OVars-tabeller — bekräftas vid behov; katalogen är utbyggbar.
