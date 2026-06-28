"""MINIMAL REPRODUCTION — ExtendSim bug (do NOT run against a live ExtendSim you care about).

Connecting a Resource Pool's ValuesOut to a Queue's ResourcePoolQuantityIn while the
Queue is left in its DEFAULT (FIFO) mode (QueueType_pop not set to "Resource Pool")
and then running the simulation makes ExtendSim UNRESPONSIVE: the app hangs and COM
dies with "The RPC server is unavailable" (0x800706BA). ExtendSim must be restarted.

Workaround: set the Queue's QueueType_pop to the "Resource Pool" index BEFORE running.

See 2026-06-28-extendsim-resource-pool-queue-mode-unresponsive.md for the full report.
This script is kept ONLY as documentation for the bug submission to Imagine That.
"""
import os
import sys

# Backend lives next to the MCP source.
_SRC = os.path.join(os.path.dirname(__file__), "..", "..",
                    "src", "ExtendSimMCP.TypeScript", "src")
sys.path.insert(0, _SRC)

import simulation_backend as sb  # noqa: E402

ec = sb.execute_command


def reproduce():
    ec("ActivateApplication();")

    create = sb.block_add("Item.lbr", "Create", x=200, y=300)["blockId"]
    queue = sb.block_add("Item.lbr", "Queue", x=340, y=300)["blockId"]
    activity = sb.block_add("Item.lbr", "Activity", x=480, y=300)["blockId"]
    exit_ = sb.block_add("Item.lbr", "Exit", x=620, y=300)["blockId"]
    pool = sb.block_add("Item.lbr", "Resource Pool", x=340, y=180)["blockId"]

    # Item flow: Create -> Queue -> Activity -> Exit
    ec(f"MakeConnection({create}, 0, {queue}, 0);")     # Create.ItemOut  -> Queue.ItemIn
    ec(f"MakeConnection({queue}, 1, {activity}, 0);")   # Queue.ItemOut   -> Activity.ItemIn
    ec(f"MakeConnection({activity}, 1, {exit_}, 0);")   # Activity.ItemOut-> Exit.ItemIn

    # The offending edge: Resource Pool -> Queue, but the Queue is NOT in Resource Pool mode.
    ec(f"MakeConnection({pool}, 1, {queue}, 5);")       # Pool.ValuesOut -> Queue.ResourcePoolQuantityIn
    # NOTE: QueueType_pop is intentionally left at its default (FIFO). Setting it to the
    # "Resource Pool" index here would AVOID the bug.

    # This run is what makes ExtendSim unresponsive:
    sb.simulation_run(end_time=100, include_stats=True)


if __name__ == "__main__":
    raise SystemExit(
        "Refusing to run: this reproduction makes ExtendSim unresponsive. "
        "Read the file; run reproduce() manually only on a throwaway ExtendSim."
    )
