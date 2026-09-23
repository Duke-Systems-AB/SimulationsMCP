"""Offline coverage for model_overview, model_snapshot, model_extract, context_get
(gap sweep G2, wave 8 - the last of the 42).

Three bugs found writing these:

* model_overview read the AI context from the top level of context_get's answer, but
  context_get nests it under "context". A model WITH context showed purpose, tags,
  assumptions and notes as None - indistinguishable from empty context. It also passed
  a failed section (hierarchies, databases, setup, context) off as an empty one.
* model_snapshot ignored success False from block_list and connection_list, so a COM
  failure came back as a successful snapshot of an EMPTY model - the hole wave 5
  closed in those readers themselves.
* model_extract kept only nodes with exactly two endpoints. A value output wired to
  several inputs (a fan-out) lost every one of those connections, and array-connector
  slots (a 2nd line into a Queue's ItemIn) were never read. It now shares
  connection_list's endpoint reader, splits fan-outs into one connection per target,
  and reports what it cannot resolve instead of dropping it.
"""
import json
import os
import sys

_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


def _load_backend():
    import importlib
    try:
        return importlib.import_module("simulation_backend")
    except Exception:
        import pytest
        pytest.skip("simulation_backend not importable (no pywin32 in this env)")


class _FakeApp:
    def __init__(self, answers=None):
        self.executed = []
        self.answers = list(answers or [])

    def Execute(self, cmd):
        self.executed.append(cmd)

    def Request(self, _system, _query):
        return self.answers.pop(0) if self.answers else "0"


def _use(monkeypatch, be, fake, model_open=True):
    monkeypatch.setattr(be, "get_extendsim_app", lambda create_if_missing=False: fake)
    monkeypatch.setattr(be, "_validate_model_open",
                        lambda app: {"success": True, "modelName": "Plant.mox"} if model_open
                        else {"success": False, "errorCode": be.ErrorCode.MODEL_NOT_OPEN,
                              "error": "No model open"})
    return fake


# ---------------------------------------------------------------------------
# context_get
# ---------------------------------------------------------------------------

def test_context_get_without_a_context_database(monkeypatch):
    be = _load_backend()
    _use(monkeypatch, be, _FakeApp())
    monkeypatch.setattr(be, "_context_read_all", lambda app: None)
    assert be.context_get() == {"success": True, "exists": False}


def test_context_get_nests_the_values_under_context(monkeypatch):
    be = _load_backend()
    _use(monkeypatch, be, _FakeApp())
    stored = {"context": {"purpose": "Line"}, "changeHistory": []}
    monkeypatch.setattr(be, "_context_read_all", lambda app: stored)
    r = be.context_get()
    assert r["exists"] is True and r["context"] == {"purpose": "Line"}


# ---------------------------------------------------------------------------
# model_overview
# ---------------------------------------------------------------------------

def _overview_parts(monkeypatch, be, ctx=None, db=None, hier=None, setup=None):
    monkeypatch.setattr(be, "hierarchy_list", lambda model_id=None: hier or {
        "success": True, "hierarchies": []})
    monkeypatch.setattr(be, "db_list", lambda model_id=None: db or {
        "success": True, "databases": []})
    monkeypatch.setattr(be, "simulation_setup_get", lambda model_id=None: setup or {
        "success": True, "endTime": 480, "startTime": 0, "numberOfRuns": 1, "timeUnits": 3})
    monkeypatch.setattr(be, "context_get", lambda model_id=None: ctx or {
        "success": True, "exists": False})


def test_model_overview_shows_the_stored_ai_context(monkeypatch):
    be = _load_backend()
    # GetModelName | then one in-ExtendSim count each: text, blocks, H-blocks
    _use(monkeypatch, be, _FakeApp(answers=["Plant.mox", "54", "12", "2"]))
    _overview_parts(monkeypatch, be, ctx={
        "success": True, "exists": True,
        "context": {"purpose": "Bottleneck study", "tags": ["line"], "notes": "n",
                    "assumptions": ["8h shift"]},
        "changeHistory": []})
    r = be.model_overview()
    assert r["success"] is True and r["totalBlocks"] == 12
    assert r["hierarchicalBlocks"] == 2 and r["textBlocks"] == 54
    assert r["aiContext"] == {"purpose": "Bottleneck study", "assumptions": ["8h shift"],
                              "tags": ["line"], "notes": "n"}


def test_model_overview_reports_a_failed_section_instead_of_an_empty_one(monkeypatch):
    be = _load_backend()
    _use(monkeypatch, be, _FakeApp(answers=["Plant.mox", "12"]))
    _overview_parts(monkeypatch, be, db={"success": False, "errorCode": "COM_ERROR",
                                         "error": "RPC server is unavailable"})
    r = be.model_overview()
    assert r["success"] is True
    assert r["databases"] == []
    assert r["sectionErrors"] == {"databases": "RPC server is unavailable"}


def test_model_overview_has_no_section_errors_when_everything_reads(monkeypatch):
    be = _load_backend()
    _use(monkeypatch, be, _FakeApp(answers=["Plant.mox", "0"]))
    _overview_parts(monkeypatch, be)
    assert "sectionErrors" not in be.model_overview()


def test_model_overview_collapses_many_identical_hierarchies(monkeypatch):
    be = _load_backend()
    _use(monkeypatch, be, _FakeApp(answers=["Plant.mox", "90"]))
    hier = [{"blockName": "Cell", "blockId": 100 + i, "depth": 0, "internalBlockCount": 7}
            for i in range(8)]
    hier.append({"blockName": "Sub", "blockId": 300, "depth": 1, "internalBlockCount": 2})
    _overview_parts(monkeypatch, be, hier={"success": True, "hierarchies": hier})
    r = be.model_overview()
    cell = next(h for h in r["hierarchySummary"] if h["name"] == "Cell")
    assert cell["count"] == 8 and cell["blockIds"] == [100, 101, 102]
    assert "8 identical copies" in cell["note"]
    assert r["totalHierarchies"] == 9


def test_model_overview_marks_internal_databases(monkeypatch):
    be = _load_backend()
    _use(monkeypatch, be, _FakeApp(answers=["Plant.mox", "3"]))
    _overview_parts(monkeypatch, be, db={"success": True, "databases": [
        {"name": "_RightClickConnect", "tableCount": 6, "tables": []},
        {"name": "Orders", "tableCount": 1,
         "tables": [{"name": "Lines", "records": 4, "fields": 3}]}]})
    r = be.model_overview()
    assert r["databases"][0] == {"name": "_RightClickConnect", "tables": 6, "internal": True}
    assert r["databases"][1] == {"name": "Orders",
                                 "tables": [{"name": "Lines", "records": 4, "fields": 3}]}


def test_model_overview_refuses_without_an_open_model(monkeypatch):
    be = _load_backend()
    fake = _use(monkeypatch, be, _FakeApp(), model_open=False)
    r = be.model_overview()
    assert r["success"] is False and fake.executed == []


# ---------------------------------------------------------------------------
# model_snapshot
# ---------------------------------------------------------------------------

def test_model_snapshot_combines_blocks_and_connections(monkeypatch):
    be = _load_backend()
    _use(monkeypatch, be, _FakeApp())
    monkeypatch.setattr(be, "block_list", lambda model_id=None: {
        "success": True, "blocks": [{"blockId": 1}, {"blockId": 2}]})
    monkeypatch.setattr(be, "connection_list", lambda model_id=None: {
        "connections": [{"nodeIndex": 4}], "count": 1})
    r = be.model_snapshot()
    assert r["success"] is True and r["modelName"] == "Plant.mox"
    assert r["blockCount"] == 2 and r["connectionCount"] == 1


def test_model_snapshot_fails_closed_when_blocks_cannot_be_read(monkeypatch):
    be = _load_backend()
    _use(monkeypatch, be, _FakeApp())
    failure = {"success": False, "blocks": [], "errorCode": "COM_ERROR", "error": "RPC"}
    monkeypatch.setattr(be, "block_list", lambda model_id=None: failure)
    monkeypatch.setattr(be, "connection_list", lambda model_id=None: {"connections": []})
    r = be.model_snapshot()
    assert r["success"] is False, "a COM failure must not look like an empty model"
    assert r["errorCode"] == "COM_ERROR"


def test_model_snapshot_fails_closed_when_connections_cannot_be_read(monkeypatch):
    be = _load_backend()
    _use(monkeypatch, be, _FakeApp())
    monkeypatch.setattr(be, "block_list", lambda model_id=None: {"success": True, "blocks": [{"blockId": 1}]})
    monkeypatch.setattr(be, "connection_list", lambda model_id=None: {
        "success": False, "connections": [], "errorCode": "COM_ERROR", "error": "RPC"})
    assert be.model_snapshot()["success"] is False


# ---------------------------------------------------------------------------
# model_extract - connections
# ---------------------------------------------------------------------------

def _extract_with(monkeypatch, be, endpoints_by_block):
    """endpoints_by_block: {blockId: [(conIdx, direction, name, nodeIndex)]}"""
    _use(monkeypatch, be, _FakeApp(answers=["Plant.mox", "C:/m/Plant.mox"]))
    blocks = [{"id": b, "type": "T", "library": "", "label": ""} for b in endpoints_by_block]
    monkeypatch.setattr(be, "_extract_blocks", lambda app: blocks)
    monkeypatch.setattr(be, "_block_wired_endpoints", lambda app, bid: endpoints_by_block[bid])
    return be.model_extract(sections=["connections"])


def _pairs(r):
    return sorted((c["sourceBlockId"], c["targetBlockId"]) for c in r["sections"]["connections"])


def test_model_extract_keeps_a_plain_connection(monkeypatch):
    be = _load_backend()
    r = _extract_with(monkeypatch, be, {1: [(1, "out", "ItemOut", 7)], 2: [(0, "in", "ItemIn", 7)]})
    assert _pairs(r) == [(1, 2)]
    assert "unresolvedConnectionNodes" not in r["sections"]


def test_model_extract_splits_a_fan_out_into_one_connection_per_target(monkeypatch):
    """One ValueOut into three inputs - the old code dropped all three."""
    be = _load_backend()
    r = _extract_with(monkeypatch, be, {
        1: [(2, "out", "ValueOut", 9)],
        2: [(0, "in", "ValueIn", 9)],
        3: [(0, "in", "ValueIn", 9)],
        4: [(1, "in", "D", 9)],          # a name with no direction still gets its line
    })
    assert _pairs(r) == [(1, 2), (1, 3), (1, 4)]


def test_model_extract_includes_array_slot_connections(monkeypatch):
    """Two Creates into one Queue ItemIn: slot 1 lives at connector index 255."""
    be = _load_backend()
    r = _extract_with(monkeypatch, be, {
        5: [(1, "out", "ItemOut", 4)],
        14: [(1, "out", "ItemOut", 77)],
        18: [(0, "in", "ItemIn", 4), (255, "in", "ItemIn", 77)],
    })
    assert _pairs(r) == [(5, 18), (14, 18)]
    slot = next(c for c in r["sections"]["connections"] if c["sourceBlockId"] == 14)
    assert slot["targetConnectorIndex"] == 255


def test_model_extract_reports_what_it_cannot_resolve(monkeypatch):
    be = _load_backend()
    r = _extract_with(monkeypatch, be, {
        1: [(1, "out", "ItemOut", 3)],                  # partner inside an H-block
        2: [(0, "in", "A", 8)], 3: [(0, "in", "B", 8)], 4: [(0, "in", "C", 8)],  # no out
    })
    assert r["sections"]["connections"] == []
    nodes = {n["nodeIndex"]: n for n in r["sections"]["unresolvedConnectionNodes"]}
    assert set(nodes) == {3, 8}
    assert len(nodes[8]["endpoints"]) == 3


def test_model_extract_keeps_the_two_endpoint_fallback_when_direction_is_unknown(monkeypatch):
    be = _load_backend()
    r = _extract_with(monkeypatch, be, {1: [(0, "unknown", "A", 6)], 2: [(0, "unknown", "B", 6)]})
    assert _pairs(r) == [(1, 2)]


# ---------------------------------------------------------------------------
# model_extract - sections, saving, model check
# ---------------------------------------------------------------------------

def test_model_extract_runs_only_the_requested_sections(monkeypatch):
    be = _load_backend()
    _use(monkeypatch, be, _FakeApp(answers=["Plant.mox", ""]))
    called = []
    monkeypatch.setattr(be, "_extract_simulation", lambda app: called.append("sim") or {"endTime": 1})
    monkeypatch.setattr(be, "_extract_blocks", lambda app: called.append("blocks") or [])
    r = be.model_extract(sections=["simulation"])
    assert called == ["sim"]
    assert list(r["sections"]) == ["simulation"]


def test_model_extract_saves_json_and_returns_a_summary(monkeypatch, tmp_path):
    be = _load_backend()
    # GetModelName, then GetModelPath(name) - which returns the FOLDER
    fake = _use(monkeypatch, be, _FakeApp(answers=["Plant.mox", "C:\\m\\"]))
    monkeypatch.setattr(be, "_extract_simulation", lambda app: {"endTime": 480.0})
    target = tmp_path / "plant.json"
    r = be.model_extract(save_path=str(target), sections=["simulation"])
    assert r == {"success": True, "savedTo": str(target), "modelName": "Plant.mox",
                 "sectionCount": 1, "sections": ["simulation"]}
    saved = json.loads(target.read_text(encoding="utf-8"))
    assert saved["sections"]["simulation"] == {"endTime": 480.0}
    assert saved["modelPath"] == "C:/m/Plant.mox"
    assert any('GetModelPath("Plant.mox")' in c for c in fake.executed)
    assert not any("GetModelPathName" in c for c in fake.executed), "does not exist in ModL"


def test_model_extract_refuses_without_an_open_model(monkeypatch):
    be = _load_backend()
    fake = _use(monkeypatch, be, _FakeApp(), model_open=False)
    r = be.model_extract()
    assert r["success"] is False and fake.executed == []


# ---------------------------------------------------------------------------
# ModL names that do not exist - each one a compile-error modal that blocks COM
# ---------------------------------------------------------------------------

def test_no_call_uses_a_modl_function_that_does_not_exist():
    """Found 2026-09-23 against ExtendSim 2026: GetModelPathName (model_extract),
    GAGetCols (ga_list, ga_read, ga_write, model_extract) and GetDialogVariableString
    (optimizer_get_results) are not ModL functions - not in ExtendSim's function list,
    never used by its own blocks, and GetModelPathName raised the modal live. And
    GAGetRows/GAGetType take a NAME; the index forms are ...ByIndex."""
    be = _load_backend()
    import re
    with open(be.__file__, encoding="utf-8") as f:
        src = f.read()
    calls = re.findall(r"Execute\(f?[\"'](.*?)[\"']\)", src)
    assert len(calls) > 300, "the scan must actually find the Execute calls"
    for bad in ("GetModelPathName(", "GAGetCols(", "GetDialogVariableString(",
                "GAGetRows({", "GAGetType({", "GAGetColumns({"):
        offenders = [c for c in calls if bad in c]
        assert offenders == [], (bad, offenders)


def test_model_overview_counts_blocks_not_numblocks_slots(monkeypatch):
    """Live on Bank.mox: NumBlocks() = 703 (anchor points, empty slots, text...) for
    50 ordinary blocks. totalBlocks was NumBlocks(); it now matches block_list."""
    be = _load_backend()
    fake = _use(monkeypatch, be, _FakeApp(answers=["Bank.mox", "54", "50", "10"]))
    _overview_parts(monkeypatch, be)
    r = be.model_overview()
    assert (r["totalBlocks"], r["hierarchicalBlocks"], r["textBlocks"]) == (50, 10, 54)
    loops = [c for c in fake.executed if "while" in c]
    assert len(loops) == 3, "one ModL loop per count - never a COM call per block"
    assert not any(c.strip() == "global0 = NumBlocks();" for c in fake.executed)
