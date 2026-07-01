# Design: tag-items — attribut-skrivning (fix `attribute_set` + molekyl-integration)

| Fält | Värde |
|------|-------|
| **Modul** | Pattern Mining — strukturerade skrivare (PRD §9.6.2), tag-items-molekylen |
| **Datum** | 2026-07-01 |
| **Stack** | TypeScript-MCP + Python-COM-backend (fix + tillägg) |
| **Status** | Design godkänd (Approach A) — väntar spec-granskning |
| **Bygger på** | M3 (instantiate), string-table-kapabiliteten (`table_set`), M6 (attribut-detektion) |

## 1. Mål & avgränsning

Två sammankopplade mål:

1. **Fixa `attribute_set`.** Nuvarande implementation skriver till `AttributeName_prm` / `ValueType_pop` / `ConstantValue_prm` — variabler som **inte existerar** på ExtendSim 2024:s Set-block. Den returnerar `success: True` men tabellen förblir tom (falsk framgång — se `temp/`-testoutput: `AttribsTable_ttbl` rad 0/1 = `['','','','']`). Skriv om den att skriva i **`AttribsTable_ttbl`** (dialogId 7), effekt-verifierad och fail-closed.
2. **Gör tag-items verksam.** Molekylen `tag-items` deklarerar `attributes.writes: ["partType"]` men konfigurerar inte Set-blocket att faktiskt skriva `partType`. Inför en molekyl-nodkonfiguration `setAttributes` som `instantiate` applicerar efter bygget, så H-blocket verkligen märker items.

**Ingår:** ny ren kärna `attribute_config.py` (`set_attribute` med injicerad backend), omskriven `attribute_set` som delegerar dit, molekyl-schema-stöd för `setAttributes`, `instantiate` Phase 4b + `ops.set_attribute`, uppdaterad `tag-items.json` med `partType`-param, enhetstester (FakeBackend/FakeOps) + ett live-test.

**Ingår inte:** connector-/distribution-baserade attributvärden bortom det discovery avslöjar (MVP: konstant numeriskt värde är den garanterade vägen; övriga value-types gate:as bakom ett ärligt `ATTRIBUTE_VALUETYPE_UNSUPPORTED` tills de är live-bekräftade). Get-blockets läs-config (`attribute_get`) rörs inte. Automatisk om-deklaration av molekylers `attributes` (senare M6-steg).

## 2. Bevisad blockstruktur & det som måste upptäckas live

Bekräftat (tidigare live-inspektion): Set-blocket (Item.lbr) konfigurerar attribut via **`AttribsTable_ttbl`**, inte de gamla enskilda `_prm`/`_pop`-variablerna. `table_set` mot `*_ttbl` är live-verifierad.

**Okänt → discovery steg 1 (som M6:s `VAR_COL`):**
- **Kolumnlayout** i `AttribsTable_ttbl`: vilken kolumn håller attributnamnet (`ATTR_NAME_COL`), vilken håller värdet (`ATTR_VALUE_COL`), och finns en value-source-popup-kolumn (`ATTR_TYPE_COL`, t.ex. constant/connector) — och i så fall vilket popup-kodvärde = "constant".
- **Celltyper:** är namn-kolumnen en string-cell (skrivs med `_set_var_string`) och värde-kolumnen numerisk (`_set_var`) eller string? Detta styr vilken primitiv kärnan använder per kolumn.
- **Attribut-pre-existens:** räcker det att skriva attributnamnet i tabellen för att Set-blocket ska definiera/binda attributet, eller måste attributet först existera i modellen? Om det senare — vilken MODL-väg definierar det?

Discovery följer det säkra COM-mönstret (engångs-`dialog_watcher.py` parallellt med `block_add`, endast in-range-läsningar, döda aldrig skriptet mitt i ett anrop — se minnet `extendsim-com-freeze-live-work`). Resultatet pinnas som namngivna konstanter överst i `attribute_config.py`.

## 3. Komponenter

| Enhet | Ansvar | Beroende | Ny/återanvänd |
|---|---|---|---|
| `attribute_config.py: set_attribute(backend, block_id, name, value, value_type="constant", row=0)` | Ren kärna: skriv namn (+ ev. typ + värde) i `AttribsTable_ttbl`-raden via backend-primitiver, läs tillbaka, verifiera. **Ingen direkt COM** (injicerad backend). | — | Ny |
| `attribute_config.py: ATTR_NAME_COL / ATTR_VALUE_COL / ATTR_TYPE_COL / _CONSTANT_CODE` | Live-upptäckta kolumn-/kodkonstanter | — | Ny |
| `attribute_config.py: set_attribute_entry(...)` | MCP-entry: lazy `import simulation_backend`, delegerar | — | Ny |
| `simulation_backend.attribute_set(...)` | **Omskriven**: validerar blocktyp Set, delegerar till kärnan, behåller sin nuvarande signatur/returkontrakt | `attribute_config` | Ändrad |
| `molecule_schema.py: validate_molecule` + ny `resolve_set_attributes(node, params)` | Validera nodens `setAttributes` (name obligatorisk), lös `{{param}}`-platshållare i värden | — | Ändrad |
| `instantiate.py: build_molecule` Phase 4b | Efter param-sättning: för varje nod med `setAttributes`, anropa `ops.set_attribute(...)` | `molecule_schema` | Ändrad |
| `instantiate.py: RealOps.set_attribute` | Delegerar till `backend.attribute_set`, höjer `BuildError` vid `success:false` (effekt-verifierat via kärnan) | `simulation_backend` | Ny |
| `tag-items.json` | Set-noden får `setAttributes: [{name:"partType", value:"{{partType}}"}]`; molekylen får param `partType` (default 1) | — | Ändrad |

Backend-kontraktet kärnan använder: `get_extendsim_app()`, `_validate_model_open(app)`, `_validate_block_type(app, block_id, "Set")`, `_set_var_string(app, id, var, str, row, col)`, `_set_var(app, id, var, num, row, col)`, `_get_var(app, id, var, row, col)`. Samma yta som `dialog_table.py` använder → samma FakeBackend-teststil.

## 4. Dataflöde — `set_attribute(backend, block_id, name, value, value_type, row)`

```
1. app = backend.get_extendsim_app(); kontrollera _validate_model_open, _validate_block_type(Set)
2. om value_type != "constant": returnera _err("ATTRIBUTE_VALUETYPE_UNSUPPORTED", ...)  # ärligt, ingen falsk framgång
3. skriv namn:   _set_var_string(app, id, "AttribsTable_ttbl", name, row, ATTR_NAME_COL)     [try → ATTRIBUTE_WRITE_FAILED]
   (om ATTR_TYPE_COL finns) _set_var(app, id, "AttribsTable_ttbl", _CONSTANT_CODE, row, ATTR_TYPE_COL)
   skriv värde:  _set_var(app, id, "AttribsTable_ttbl", value, row, ATTR_VALUE_COL)          [samma try-block]
4. läs tillbaka namn + värde                                                                 [try → ATTRIBUTE_READ_FAILED]
5. om namn matchar (och värde matchar): success  annars _err("ATTRIBUTE_WRITE_REJECTED", requested/actual)
```

Separata try-block för skriv vs återläsning (samma mönster som `table_set`): en skrivning som kastar → `ATTRIBUTE_WRITE_FAILED`; en återläsning som kastar → `ATTRIBUTE_READ_FAILED` (skrivningen kan ha persisterat, får ej maskeras som skrivfel).

## 5. Molekyl-integration

**`tag-items.json`:**
```json
{
  "params": { "partType": { "required": false, "default": 1 } },
  "nodes": [
    { "ref": "set", "lib": "Item.lbr", "type": "Set", "seed": true,
      "setAttributes": [ { "name": "partType", "value": "{{partType}}" } ] }
  ]
}
```

**`instantiate.py` Phase 4b** (efter befintlig Phase 4 `set_value`):
```
för varje nod med "setAttributes":
    för varje {name, value, valueType?} i resolve_set_attributes(nod, params):
        ops.set_attribute(internal[nod.ref], name, value, valueType or "constant")
```
`RealOps.set_attribute` anropar `backend.attribute_set(block_id, name, value=value, value_type=value_type)` och höjer `BuildError` om `success` är falskt — bygget failar hellre högt än lämnar ett tyst omärkt block (krav 12).

**Platshållare:** `resolve_set_attributes` återanvänder `_PLACEHOLDER`-regexen (`^\{\{(\w+)\}\}$`) på varje `value`/`name`; default-param `partType=1` fylls i om anroparen inte anger den. (Default-hantering läggs i param-upplösningen så att `{{partType}}` alltid har ett värde.)

## 6. Fail-closed

- Kärnan är ren; all COM injiceras.
- Ingen skrivning litar på success-flaggan: namn (och värde) läses tillbaka och jämförs. Mismatch → `ATTRIBUTE_WRITE_REJECTED` med `requested`/`actual`.
- Ej stödd value_type → explicit felkod, aldrig `success:True`.
- `RealOps.set_attribute` höjer `BuildError` vid delegerat fel → hela `instantiate_pattern` returnerar `INSTANTIATE_FAILED` istället för ett halvbyggt "märkt" H-block.
- `attribute_set` behåller sitt yttre kontrakt men returnerar nu ärliga felkoder från kärnan istället för falsk framgång.

## 7. Testning (TDD)

**Enhet, rent (FakeBackend, speglar `test_dialog_table.py`):**
- namn+värde skrivs på rätt (var, row, col) och återläses lika → success; verifiera exakt call-sekvens (set namn → [set typ] → set värde → get:ar).
- återläst namn ≠ skrivet → `ATTRIBUTE_WRITE_REJECTED` (requested/actual).
- skrivning kastar → `ATTRIBUTE_WRITE_FAILED`; återläsning kastar → `ATTRIBUTE_READ_FAILED`.
- `value_type="connector"` → `ATTRIBUTE_VALUETYPE_UNSUPPORTED` (ingen COM-skrivning sker).
- fel blocktyp / stängd modell → propagerar respektive fel.

**Enhet, backend-delegation (mock-backend):** `attribute_set` anropar kärnan och returnerar dess dict; ingen kvarvarande referens till `AttributeName_prm`/`ValueType_pop`/`ConstantValue_prm`.

**Enhet, molekyl (FakeOps):** `resolve_set_attributes` löser `{{partType}}`→default 1 och explicit param; `build_molecule(tag-items)` producerar ett `("set_attribute", set_id, "partType", 1, "constant")`-anrop i `ops.calls`; validering avvisar `setAttributes` utan `name`.

**Live (`skipif` utan ExtendSim):** `instantiate_pattern("tag-items", {"partType": 2})` → läs `AttribsTable_ttbl` → assert `partType`/`2` finns i rätt kolumner. (Om tid: kör en minimal sim med ett Get-block och verifiera att ett item bär `partType==2`.)

## 8. Sekvensering

- **Steg 0 (live discovery):** `AttribsTable_ttbl`-layout (kolumner, celltyper, ev. typ-popup + constant-kod, attribut-pre-existens). Säkert COM-mönster. Pinna konstanterna.
- **Steg 1:** ren `set_attribute` + FakeBackend-enhetstester (TDD).
- **Steg 2:** skriv om `simulation_backend.attribute_set` att delegera + mock-backend-test.
- **Steg 3:** `molecule_schema`: validera `setAttributes` + `resolve_set_attributes` + enhetstester.
- **Steg 4:** `instantiate` Phase 4b + `RealOps.set_attribute` + `FakeOps.set_attribute` + enhetstest.
- **Steg 5:** uppdatera `tag-items.json` (param + setAttributes) + molekyl-enhetstest.
- **Steg 6:** live-test (instantiate → läs tillbaka partType).
- **Steg 7:** packaging — `attribute_config.py` in i `copy-files`; dispatch/registrering (befintliga verktyg `attribute_set`/`instantiate_pattern` oförändrade utåt → verktygsantal förblir 99).

## 9. Öppna frågor (löses i Steg 0)

1. `AttribsTable_ttbl` exakta kolumnindex + celltyper (name/value/type).
2. Finns en value-source-popup, och vilket kodvärde = "constant"?
3. Måste attributet pre-existera i modellen, eller definierar Set-blocket det vid tabellskrivning? Om pre-existens krävs: lägg ett `define_attribute`-försteg (MODL) i kärnan — annars utgår det.
4. Bekräfta att `_set_var`/`_get_var` mot en numerisk `_ttbl`-kolumn beter sig som förväntat (string-tabell med blandade kolumntyper).
