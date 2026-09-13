# tests/unit_py/test_compose_build.py
import os, sys
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import pytest
from fake_ops import FakeOps
from compose import build_flow

FLOW = {
    "id": "line2",
    "instances": [
        {"ref": "m1", "pattern": "machine-with-breakdowns", "params": {"process_time": 1, "mtbf": 30, "mttr": 10}},
        {"ref": "m2", "pattern": "machine-with-breakdowns", "params": {"process_time": 1, "mtbf": 30, "mttr": 10}},
    ],
    "wiring": [{"from": "m1.out", "to": "m2.in"}],
}

def test_build_flow_instantiates_each_and_wires_hblocks():
    ops = FakeOps()
    res = build_flow(FLOW, ops)

    # both molecules instantiated -> two distinct H-blocks recorded
    assert set(res["instances"]) == {"m1", "m2"}
    h1 = res["instances"]["m1"]["hblockId"]
    h2 = res["instances"]["m2"]["hblockId"]
    assert h1 != h2

    # the inter-molecule wiring connected m1's outlet H-block connector to m2's inlet
    out_idx = res["instances"]["m1"]["interfaceMap"]["out"]["outerCon"]
    in_idx = res["instances"]["m2"]["interfaceMap"]["in"]["outerCon"]
    assert ("connect", h1, out_idx, h2, in_idx) in ops.calls


def test_build_flow_reports_all_built_hblocks_when_a_later_instance_fails():
    # m1 builds fine; m2's build blows up mid-build. m1's H-block (already
    # built) plus m2's own orphaned H-block must both be reported (W2-3).
    call_count = {"n": 0}

    class BoomOnSecondBuild(FakeOps):
        def set_value(self, block_id, var, value):
            call_count["n"] += 1
            if call_count["n"] > 3:      # let m1 finish, blow up during m2
                raise RuntimeError("COM exploded during m2's build")
            super().set_value(block_id, var, value)

    ops = BoomOnSecondBuild()
    with pytest.raises(RuntimeError) as exc_info:
        build_flow(FLOW, ops)

    partial = getattr(exc_info.value, "partial", None)
    assert partial is not None
    assert partial["partialBuild"] is True
    hblock_ids = [c[-1] for c in ops.calls if c[0] == "create_hblock"]
    assert len(hblock_ids) == 2                              # both H-blocks created
    assert set(partial["orphanedHblockIds"]) == set(hblock_ids)


def test_build_flow_preserves_stray_block_ids_from_pre_hblock_failure():
    # m1 builds fine; m2 fails BEFORE its H-block exists (2nd add_block raises).
    # The re-wrap in build_flow must NOT drop build_molecule's orphanedBlockIds:
    # m2's already-placed stub block(s) + m1's completed H-block are all reported.
    call_count = {"n": 0}

    class BoomOnFifthAddBlock(FakeOps):
        def add_block(self, lib, type_):
            call_count["n"] += 1
            if call_count["n"] == 5:     # m1 places 3 stubs; fail on m2's 2nd
                raise RuntimeError("COM exploded placing m2's second stub")
            return super().add_block(lib, type_)

    ops = BoomOnFifthAddBlock()
    with pytest.raises(RuntimeError) as exc_info:
        build_flow(FLOW, ops)

    partial = getattr(exc_info.value, "partial", None)
    assert partial is not None
    assert partial["partialBuild"] is True
    hblock_ids = [c[-1] for c in ops.calls if c[0] == "create_hblock"]
    assert len(hblock_ids) == 1                              # only m1's H-block
    assert set(partial["orphanedHblockIds"]) == set(hblock_ids)
    # m2's first stub was placed before the failure -> must be reported
    assert partial["orphanedBlockIds"], "stray stub block ids were dropped"


def test_build_flow_reports_orphans_when_wiring_fails():
    class BoomOnConnect(FakeOps):
        def connect(self, a_id, a_con, b_id, b_con):
            # Only the inter-molecule wiring step connects hblockId->hblockId
            # directly; every intra-build connect uses inner/boundary block ids.
            if a_id in self._hblocks and b_id in self._hblocks:
                raise RuntimeError("wiring connect failed")
            super().connect(a_id, a_con, b_id, b_con)

    ops = BoomOnConnect()
    with pytest.raises(RuntimeError) as exc_info:
        build_flow(FLOW, ops)

    partial = getattr(exc_info.value, "partial", None)
    assert partial is not None
    assert partial["partialBuild"] is True
    # both instances fully built (both H-blocks exist) before wiring failed
    assert len(partial["orphanedHblockIds"]) == 2
