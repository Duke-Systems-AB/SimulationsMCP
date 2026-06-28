# Design: M5 — Baspaket av molekyler + flöden + upptäckt

| Fält | Värde |
|------|-------|
| **Modul** | Pattern Mining, milstolpe M5 |
| **Datum** | 2026-06-28 |
| **Stack** | TypeScript-MCP + Python-COM-backend (tillägg) |
| **Status** | Design godkänd — väntar spec-granskning |
| **Bygger på** | M3 `instantiate_pattern`, M4 `compose_flow`, PRD §7.1, §7.3, §11 (list_patterns/get_pattern) |

## 1. Mål & avgränsning

Leverera ett **handskrivet baspaket** av återanvändbara molekyler + ett par bas-flöden, och **upptäckts-verktyg** (`list_patterns`/`get_pattern`) så en AI-agent kan hitta vilka mönster som finns och deras params/interface — vilket gör hela bygg-loopen (upptäck → instansiera → komponera) självförsörjande.

**Ingår:** 3 nya molekyler, 2 bas-flöden, `patterns.py` (ren upptäckt) + 2 MCP-verktyg, tre TDD-lager.
**Ingår inte:** grenande molekyler/flöden (kräver motor-utökning — eget framtida steg), scoring/embedding-retrieval (M7 — `list_patterns` använder enkel substräng-filtrering), mining (M6/M8).

**Hård begränsning:** M3/M4-motorn bygger bara **linjära** flöden (en kedja + sidoanslutningar). Alla M5-molekyler och -flöden är linjära.

## 2. Nya molekyler (`patterns/molecules/`)

Alla får `attributes:{reads,writes}` och en `seed`-nod = flödeskedjans svans (M3-krav).

1. **`simple-machine`** — `Queue → Activity`, param `process_time` (Activity `D`). Interface inlopp `q.ItemIn`, utlopp `act.ItemOut`. (Ersätter den separata "process-time-machine" — samma struktur, parametriserad.)
2. **`resource-machine`** — `Queue → Activity` + en **Resource Pool** kopplad till Activityns resurs-connector (sidoanslutning), params `process_time`, `capacity`. **Risk:** exakta connector-namn för Resource Pool↔Activity är inte verifierade — kräver en COM-discovery-spike först (som vi gjorde för Shutdown). Om resurspoolen visar sig kräva grenande/specialhantering rapporteras BLOCKED och `resource-machine` skjuts upp.
3. **`tag-items`** — ett `Set`-block som skriver item-attributet `partType`. Linjär (Set har ItemIn+ItemOut → kan vara seed). `attributes.writes=["partType"]`. Gör attributkontraktet (M4) verkligt som uppströms-skrivare.

Befintliga `buffer` och `machine-with-breakdowns` ingår i paketet (totalt 5 molekyler).

## 3. Bas-flöden (`patterns/flows/`, linjära)

Sparade flödesdefinitioner (PRD §7.3-format: `id`, `instances`, `wiring`).

1. **`two-stage-line`** — `machine-with-breakdowns` → `machine-with-breakdowns` (två maskiner i serie).
2. **`tagged-line`** — `tag-items` → `simple-machine` (uppströms attribut-skrivare följt av en maskin).

`get_pattern("two-stage-line")` returnerar flödesdefinitionen; agenten skickar den till `compose_flow` (M4, oförändrat). Inga ändringar i `compose_flow` behövs.

## 4. Upptäckt (`patterns.py`, ren — ingen COM)

| Funktion | Beteende |
|---|---|
| `list_patterns(intent=None)` | Läser `patterns/molecules/*.json` + `patterns/flows/*.json`; returnerar `[{id, kind, intent, params, interface}]` (kind = "molecule"/"flow"). Om `intent` ges: substräng-filter (case-insensitivt) på postens `intent`. Ingen scoring (M7). |
| `get_pattern(pattern_id)` | Returnerar hela JSON-definitionen för id:t (molekyl eller flöde). Okänt id → fel. |

Två MCP-verktyg `list_patterns` och `get_pattern` registreras (mönster som M3/M4-verktygen).

## 5. Felhantering (fail-closed)

- `get_pattern` på okänt id → `{success:false, errorCode:"UNKNOWN_PATTERN"}`.
- `list_patterns`: en ogiltig/trasig JSON-fil i katalogen → **fel** (inte tyst hoppa över) — vi vill upptäcka korrupt bibliotek.
- Molekyl-/flödesdefinitioner valideras av befintliga `molecule_schema`/`validate_flow` när de instansieras (inte i upptäckten).

## 6. Testning (TDD, tre lager)

1. **Enhet, rent:** `list_patterns` listar alla poster + intent-substräng-filter fungerar; `get_pattern` hittar känt id och felar fail-closed på okänt; trasig JSON ger fel.
2. **Enhet, fejk-COM:** varje ny molekyl (`simple-machine`, `tag-items`, och `resource-machine` om discovery lyckas) går att `build_molecule` via FakeOps (rätt sekvens); varje bas-flöde går att `build_flow` via FakeOps.
3. **Live (skippas utan ExtendSim):** instansiera varje ny molekyl och smoke-kör (items flödar); bygg `two-stage-line` och smoke-kör (items genom båda maskinerna).

## 7. Sekvensering

- **Steg 0 (vid behov):** COM-discovery-spike för Resource Pool↔Activity-connectorer (för `resource-machine`). Om olösbart linjärt → skjut upp `resource-machine`, fortsätt med resten.
- **Steg 1:** `simple-machine` + `tag-items` molekyl-JSON + fejk-COM-byggtester.
- **Steg 2:** `resource-machine` (efter Steg 0).
- **Steg 3:** bas-flöden + fejk-COM-byggtester.
- **Steg 4:** `patterns.py` (`list_patterns`/`get_pattern`) + rena tester.
- **Steg 5:** live-tester (instansiera + smoke-kör molekyler & flöde).
- **Steg 6:** MCP-verktygsregistrering (`list_patterns`, `get_pattern`).

## 8. Öppna frågor

1. Resource Pool-wiring (connector-namn) overifierad → Steg 0-spike avgör om `resource-machine` är linjärt byggbar nu.
2. `tag-items`: attributet `partType` är hårdkodat i molekylen; att parametrisera attributnamnet kan komma senare (YAGNI nu).
3. `list_patterns` returnerar params/interface-sammanfattning — exakt fältform bestäms i planen, men ingen scoring (M7).
