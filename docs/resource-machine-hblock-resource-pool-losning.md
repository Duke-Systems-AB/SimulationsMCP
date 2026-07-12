# Rapport: Resource Pool i H-block — utmaningen och lösningen

**Datum:** 2026-07-12
**Modul:** Pattern Mining / resource-machine-molekylen
**Status:** Löst och live-verifierat (molekylen bygger som H-block och kör items genom hela acquire→use→release-cykeln)

> **⚠️ Viktig egenhet att känna till (läs även §5):** en `resource-machine` byggd av koden **ser skum ut i ExtendSims GUI** — Release-blockets pool-dropdown (`Serverblocks_pop`) verkar tom/ovald — MEN simuleringen är korrekt och kör som den ska. Det är avsiktligt, se förklaringen nedan.

## 1. Målet

Göra `resource-machine`-molekylen funktionell: en maskin (Activity) vars genomflöde begränsas av en namngiven Resource Pool, med en full **acquire → use → release**-cykel, byggd programmatiskt som ett H-block via `instantiate_pattern`.

## 2. Utmaningarna (lager på lager)

Problemet visade sig ha flera lager, som avslöjades ett i taget:

1. **Tre trasiga backend-funktioner (falsk framgång).** `queue_set_resource_pool`, `resource_pool_release_set_config` och `resource_pool_set_config` rapporterade alla `success:True` men konfigurerade i praktiken ingenting. T.ex. skrev `queue_set_resource_pool` till `ResourceTable` med fel COM-metod (`SetVariableNumeric` på en sträng-tabell = tyst no-op) och skrev dessutom block-**ID** istället för pool-**namn**.

2. **`ResourceTable` skrivs bara via `SetDialogVariable`.** Variabeln `ResourceTable` saknar `_ttbl`-suffix, så suffix-baserad routing skickade skrivningen fel. Måste skrivas via `SetDialogVariable` (sträng-tabell-vägen).

3. **Sluttiden sattes aldrig.** `simulation_run(end_time=X)` tilldelade `endTime`-globalen, vilket inte sätter körningens sluttid (den förblev modell-default 1000). Rätt API är `SetRunParameter(end, dt)`. Detta gjorde tidiga mätningar missvisande (`currentTime=0`).

4. **Kärnproblemet — Release-blocket hittade inte poolen i H-blocket.** Även med allt annat rätt aborterade simuleringen vid t=0:
   > *"Resource pool name not specified in Resource Pool Release ... CHECKDATA message handler"*

   Release-blocket måste peka ut sin pool. Vi provade det uppenbara — popup-menyn `Serverblocks_pop` — och det fungerade i en **platt** modell men **aldrig i ett H-block**. En månghörnig empirisk jakt (index-sökning, `ActivateApplication`-refresh, trigga CheckData …) gick i cirklar.

## 3. Hur vi kom fram till lösningen

Den empiriska vägen konvergerade inte. Genombrottet kom när vi **läste blockets faktiska ModL-källkod** istället för att gissa på beteendet utifrån.

Blockets kod finns läsbar i `Item.lbr` (en SQLite-fil, tabell `VocabsTable`, kolumn `blockBlob`), samt via authoring-MCP:ns `block_inspect` (som listar blockets interna STAT-variabler). Det avslöjade två avgörande saker:

**(a) Hur Release-blocket resolvar sin pool:**
```modl
Integer FindRPBlock(string ResourcePool) {
    for(i=0; i<numBlocks(); i++)
        if("Resource Pool" == BlockName(i)
           && GetDialogVariable(i,"ResourcePoolName",0,0) == ResourcePool)   // matcha på NAMN
              RPBlockFound[numRPFound++] = i;
    ...
    RPHBlocks[i] = GetEnclosingHBlockNum2(RPBlockFound[i]);                   // + samma H-block
}
```
Vid **CheckData** skannar blocket modellen live efter en Resource Pool vars `ResourcePoolName` matchar det namn blocket självt håller, inom samma omslutande H-block.

**(b) Vad popupen egentligen gör:**
```modl
on Serverblocks_pop {
    if(Serverblocks_pop > GetDimension(RPNames))     // listan tom → out of range
        { ResourcePoolName = ""; ServerBlockNum = -1; }   // ← nollställer länken
    else {
        ResourcePoolName = RPNames[Serverblocks_pop-1];
        ServerBlockNum   = RPNumbers[Serverblocks_pop-1];
    }
}
```
Popupen sätter bara `ResourcePoolName` + `ServerBlockNum` från listan `RPNames`/`RPNumbers` — en lista som **byggs vid en UI-redraw** och är **tom i ett nybyggt H-block** (`PlaceBlockInHblock` triggar ingen redraw). Att sätta popupen där **nollställde** alltså länken. Det var därför den vägen aldrig kunde fungera.

## 4. Lösningen

Sätt de två underliggande länk-variablerna **direkt**, bypassa popupen:

```python
# resource_pool_config.configure_release
backend._set_var(app, id, "NumReleased_PRM", qty, ...)              # antal (numerisk param)
backend._set_dialog_var(app, id, "ResourcePoolName", "Pool1")       # SetDialogVariable(sträng)
backend._set_dialog_var(app, id, "ServerBlockNum", pool_block_id)   # SetDialogVariable(tal)
```

Det är två helt vanliga `SetDialogVariable`-anrop — ingen hack. Genom att skriva `ResourcePoolName` ger vi blockets egen `FindRPBlock` exakt det den letar efter vid CheckData; `ServerBlockNum` (poolens blocknummer) är den direkta länk popupen annars skulle ha satt. Poolens blocknummer kommer från molekyl-bygget (`RealOps` har det), eller från en namn-skanning (`find_resource_pool`, som speglar `FindRPBlock`) för det fristående verktyget. Skrivningen är effekt-verifierad (läser tillbaka `ResourcePoolName`) och fail-closed.

## 5. GUI-egenheten (viktig)

**I ExtendSims GUI ser en koddbyggd resource-machine "fel" ut:** öppnar man Release-blocket är pool-dropdownen (`Serverblocks_pop`) tom/ovald, för vi satte aldrig popup-indexet — vi satte de underliggande variablerna direkt. Men blockets `ResourcePoolName`/`ServerBlockNum` ÄR satta, och `FindRPBlock` löser upp poolen korrekt vid körning.

Med andra ord:
- **GUI (statiskt):** ser skumt ut — dropdownen visar ingen vald pool.
- **Körning (CheckData→Simulate):** helt korrekt — items flödar, poolen begränsar genomflödet.

Detta är avsiktligt och en följd av att ExtendSims dynamiska popup-lista bara byggs vid UI-interaktion. Om man vill att GUI:t också ska "se rätt ut" kan man öppna Release-blockets dialog manuellt en gång (då bygger ExtendSim om `RPNames` och popupen visar den redan satta poolen) — men det behövs inte för att simuleringen ska vara korrekt.

## 6. Lärdomar

- **Läs blockets ModL-kod istället för att gissa på beteendet.** Timmar av empirisk sondering gick i cirklar; källkoden gav svaret direkt. (Se minnet `reading-extendsim-block-modl-source`.)
- **Skilj på "vad UI:t sätter" och "vad körningen läser".** Länken bodde i `ResourcePoolName`/`ServerBlockNum`, inte i popupen — popupen var bara ett gränssnitt som ibland nollställde länken.
- **Fail-closed räddade oss.** Genom hela jakten byggde koden aldrig en tyst trasig modell — den vägrade (`RELEASE_POOL_NOT_FOUND`) tills länken faktiskt satt.
- **En korrekt modell behöver inte se "rätt" ut i GUI:t.** Verifiera funktion via körning (`exitStatistics.itemsExited`), inte via hur dialogen ser ut.

## 7. Resultat

`instantiate_pattern("resource-machine", {process_time, capacity, pool_name})` bygger nu ett fungerande H-block. Live-verifierat: **92 items** genom maskinen på 100 tidsenheter, pool-utilization ~46 %, ingen krasch, ingen dialog. Enhetstester (FakeBackend) + live-test gröna.
