# Report: Resource Pool inside an H-block — the challenge and the solution

**Date:** 2026-07-12
**Module:** Pattern Mining / the resource-machine molecule
**Status:** Solved and live-verified (the molecule builds as an H-block and runs items through the full acquire→use→release cycle)

> **⚠️ Important gotcha to be aware of (see also §5):** a `resource-machine` built by the code **looks wrong in the ExtendSim GUI** — the Release block's pool dropdown (`Serverblocks_pop`) appears empty/unselected — BUT the simulation is correct and runs as intended. This is deliberate; see the explanation below.

## 1. The goal

Make the `resource-machine` molecule functional: a machine (Activity) whose throughput is limited by a named Resource Pool, with a full **acquire → use → release** cycle, built programmatically as an H-block via `instantiate_pattern`.

## 2. The challenges (layer upon layer)

The problem turned out to have several layers, revealed one at a time:

1. **Three broken backend functions (false success).** `queue_set_resource_pool`, `resource_pool_release_set_config` and `resource_pool_set_config` all reported `success:True` but in practice configured nothing. For example, `queue_set_resource_pool` wrote to `ResourceTable` with the wrong COM method (`SetVariableNumeric` on a string table = silent no-op) and additionally wrote the block **ID** instead of the pool **name**.

2. **`ResourceTable` is only written via `SetDialogVariable`.** The variable `ResourceTable` has no `_ttbl` suffix, so suffix-based routing sent the write down the wrong path. It must be written via `SetDialogVariable` (the string-table path).

3. **The end time was never set.** `simulation_run(end_time=X)` assigned the `endTime` global, which does not set the run's end time (it stayed at the model default of 1000). The correct API is `SetRunParameter(end, dt)`. This made early measurements misleading (`currentTime=0`).

4. **The core problem — the Release block couldn't find the pool inside the H-block.** Even with everything else correct, the simulation aborted at t=0:
   > *"Resource pool name not specified in Resource Pool Release ... CHECKDATA message handler"*

   The Release block must point at its pool. We tried the obvious thing — the `Serverblocks_pop` popup — and it worked in a **flat** model but **never inside an H-block**. A many-sided empirical hunt (index search, `ActivateApplication` refresh, triggering CheckData …) went in circles.

## 3. How we arrived at the solution

The empirical path did not converge. The breakthrough came when we **read the block's actual ModL source** instead of guessing at its behavior from the outside.

The block's code is readable in `Item.lbr` (a SQLite file, table `VocabsTable`, column `blockBlob`), and via the authoring MCP's `block_inspect` (which lists the block's internal STAT variables). That revealed two decisive things:

**(a) How the Release block resolves its pool:**
```modl
Integer FindRPBlock(string ResourcePool) {
    for(i=0; i<numBlocks(); i++)
        if("Resource Pool" == BlockName(i)
           && GetDialogVariable(i,"ResourcePoolName",0,0) == ResourcePool)   // match on NAME
              RPBlockFound[numRPFound++] = i;
    ...
    RPHBlocks[i] = GetEnclosingHBlockNum2(RPBlockFound[i]);                   // + same H-block
}
```
At **CheckData** the block scans the model live for a Resource Pool whose `ResourcePoolName` matches the name the block itself holds, within the same enclosing H-block.

**(b) What the popup actually does:**
```modl
on Serverblocks_pop {
    if(Serverblocks_pop > GetDimension(RPNames))     // list empty → out of range
        { ResourcePoolName = ""; ServerBlockNum = -1; }   // ← zeroes the link
    else {
        ResourcePoolName = RPNames[Serverblocks_pop-1];
        ServerBlockNum   = RPNumbers[Serverblocks_pop-1];
    }
}
```
The popup merely sets `ResourcePoolName` + `ServerBlockNum` from the `RPNames`/`RPNumbers` list — a list that is **built on a UI redraw** and is **empty in a freshly-built H-block** (`PlaceBlockInHblock` triggers no redraw). So setting the popup there **zeroed** the link. That is why that path could never work.

## 4. The solution

Set the two underlying link variables **directly**, bypassing the popup:

```python
# resource_pool_config.configure_release
backend._set_var(app, id, "NumReleased_PRM", qty, ...)              # quantity (numeric param)
backend._set_dialog_var(app, id, "ResourcePoolName", "Pool1")       # SetDialogVariable(string)
backend._set_dialog_var(app, id, "ServerBlockNum", pool_block_id)   # SetDialogVariable(number)
```

These are two perfectly ordinary `SetDialogVariable` calls — no hack. By writing `ResourcePoolName` we give the block's own `FindRPBlock` exactly what it looks for at CheckData; `ServerBlockNum` (the pool's block number) is the direct link the popup would otherwise have set. The pool's block number comes from the molecule build (`RealOps` has it), or from a name scan (`find_resource_pool`, which mirrors `FindRPBlock`) for the standalone tool. The write is effect-verified (reads `ResourcePoolName` back) and fail-closed.

## 5. The GUI gotcha (important)

**In the ExtendSim GUI a code-built resource-machine looks "wrong":** open the Release block and the pool dropdown (`Serverblocks_pop`) is empty/unselected, because we never set the popup index — we set the underlying variables directly. But the block's `ResourcePoolName`/`ServerBlockNum` ARE set, and `FindRPBlock` resolves the pool correctly at run time.

In other words:
- **GUI (static):** looks odd — the dropdown shows no selected pool.
- **Run (CheckData→Simulate):** entirely correct — items flow, the pool limits throughput.

This is deliberate and a consequence of ExtendSim's dynamic popup list only being built on UI interaction. If you want the GUI to also "look right", open the Release block's dialog manually once (ExtendSim then rebuilds `RPNames` and the popup shows the already-set pool) — but that is not needed for the simulation to be correct.

## 6. Lessons

- **Read the block's ModL source instead of guessing at its behavior.** Hours of empirical probing went in circles; the source gave the answer directly. (See the memory `reading-extendsim-block-modl-source`.)
- **Distinguish "what the UI sets" from "what the run reads".** The link lived in `ResourcePoolName`/`ServerBlockNum`, not in the popup — the popup was just an interface that sometimes zeroed the link.
- **Fail-closed saved us.** Throughout the hunt the code never built a silently broken model — it refused (`RELEASE_POOL_NOT_FOUND`) until the link was actually set.
- **A correct model need not "look right" in the GUI.** Verify function via the run (`exitStatistics.itemsExited`), not via how the dialog looks.

## 7. Result

`instantiate_pattern("resource-machine", {process_time, capacity, pool_name})` now builds a working H-block. Live-verified: **92 items** through the machine over 100 time units, pool utilization ~46%, no crash, no dialog. Unit tests (FakeBackend) + live test green.
