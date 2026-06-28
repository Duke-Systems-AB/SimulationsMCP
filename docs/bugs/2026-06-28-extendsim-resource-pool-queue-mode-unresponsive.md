# ExtendSim bug report: Resource Pool connected to a non-Resource-Pool-mode Queue makes ExtendSim unresponsive on run

**To:** Imagine That, Inc. (ExtendSim support)
**Reported by:** Duke Systems AB (Jonas Enhörning)
**Date:** 2026-06-28
**ExtendSim version:** ____________ (fill from Help → About)
**OS:** Windows 11 (build 10.0.26200)
**Interface:** Driven via COM/OLE automation, but the trigger is a plain model configuration (reproducible by hand).

## Summary

If a **Resource Pool** block's `ValuesOut` is connected to a **Queue** block's `ResourcePoolQuantityIn` connector while the **Queue is left in its default (FIFO) queueing mode** (i.e. *not* switched to "Resource Pool" via the Queue's queue-type popup), then **running the simulation makes ExtendSim unresponsive** — the application hangs and must be force-restarted. Over COM/OLE the process becomes unreachable ("The RPC server is unavailable", `0x800706BA`).

Expected behaviour: ExtendSim should either ignore the unused Resource Pool connection, or surface a validation message ("a Resource Pool is connected to a Queue that is not in Resource Pool mode"), and run normally — **not** hang/crash.

## Minimal reproduction (by hand, Item library)

1. New model.
2. Place: `Create`, `Queue`, `Activity`, `Exit`, and `Resource Pool` (all from `Item.lbr`).
3. Connect the item flow:
   - `Create.ItemOut → Queue.ItemIn`
   - `Queue.ItemOut → Activity.ItemIn`
   - `Activity.ItemOut → Exit.ItemIn`
4. Connect the resource link **without** changing the Queue's mode:
   - `Resource Pool.ValuesOut → Queue.ResourcePoolQuantityIn`
5. Leave the **Queue's queue-type popup at its default** (FIFO) — do **not** set it to "Resource Pool".
6. Run the simulation.

**Result:** ExtendSim becomes unresponsive (UI hangs; if automated via COM, the next call fails with RPC-server-unavailable and the process must be restarted).

**Workaround:** set the Queue's queue-type popup to "Resource Pool" mode before running. With the mode set, the model runs normally.

## Connector-level detail (for exact reproduction / automation)

- `Queue` connector `ResourcePoolQuantityIn` is connector index 5; queue-type popup dialog variable is `QueueType_pop`.
- `Resource Pool` connector `ValuesOut` is connector index 1.
- The offending edge is `MakeConnection(resourcePoolId, 1, queueId, 5)` with `QueueType_pop` left at its default value.

## How we hit it (context)

We build hierarchical "molecule" blocks via COM automation. A molecule wired a Resource Pool to a Queue's `ResourcePoolQuantityIn` but did not set `QueueType_pop` to Resource Pool mode. On `RunSimulation`, ExtendSim went unresponsive and COM died with RPC-server-unavailable; the application had to be restarted. A reproduction snippet is in `repro-resource-pool-queue-unresponsive.py` (same directory) — **do not run it against a live ExtendSim you care about; it makes ExtendSim unresponsive.**

## Severity / impact

High for automation: a single mis-configured (but structurally legal) connection takes down the whole application with no recoverable error — only a force-restart. Even interactively, a user who connects a Resource Pool before switching the Queue to Resource Pool mode loses their session.
