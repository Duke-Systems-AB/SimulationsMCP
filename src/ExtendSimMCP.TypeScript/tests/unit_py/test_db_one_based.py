"""ExtendSim's database API is 1-based; the tools must translate.

Established 2026-09-23 from three independent sources:

* The ModL manual: DBRecordsInsert "insert at record index or append (insertAtRecord
  is zero)" - 0 is reserved for "append", so no record index can be 0.
* ExtendSim's own blocks: 77 vendor loops over records run for(i=1; i<=numRecs), and
  fields for(fieldIndex=1; fieldIndex<=NumFields). Scenario Manager code already in
  this repo checks db_idx > 0 and tbl_idx > 0.
* Live output from this server, which enumerated from 0: db_list showed a phantom
  'table_0' first and DROPPED the last table (StringPool, which db_get_records read
  fine by name the same day); db_table_info showed a phantom 'field_0' and dropped the
  last field ('Value'); db_get_records returned a blank first row. A model with exactly
  one database made db_list return no databases at all.

Decision (2026-09-23): record numbers stay 0-BASED in every tool parameter and
result, as the user manual has always documented. The server adds 1 before a record
number reaches ExtendSim and subtracts 1 on the way back. Database, table and field
indices shown in listings are ExtendSim's own (1-based) - no tool takes them as input;
they exist for raw ModL through execute_command, where the real index is what counts.
"""
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
        self.poked = []
        self.answers = list(answers or [])

    def Execute(self, cmd):
        self.executed.append(cmd)

    def Request(self, _system, _query):
        return self.answers.pop(0) if self.answers else "0"

    def Poke(self, system, address, value):
        self.poked.append((system, address, value))


def _use(monkeypatch, be, fake):
    monkeypatch.setattr(be, "get_extendsim_app", lambda create_if_missing=False: fake)
    monkeypatch.setattr(be, "_validate_model_open", lambda app: {"success": True})
    return fake


def _calls(fake, needle):
    return [c for c in fake.executed if needle in c]


# ---------------------------------------------------------------------------
# Index 0 is never valid: every lookup treats <= 0 as "not found"
# ---------------------------------------------------------------------------

def test_a_zero_index_from_extendsim_means_not_found():
    be = _load_backend()
    for answers, code in ((["0"], "DATABASE_NOT_FOUND"),
                          (["1", "0"], "TABLE_NOT_FOUND"),
                          (["1", "1", "0"], "FIELD_NOT_FOUND")):
        r = be._resolve_db_indices(_FakeApp(answers=answers), "db", "t", "f")
        assert r["success"] is False and r["errorCode"] == code, (answers, r)


# ---------------------------------------------------------------------------
# Enumerations run 1..n, so nothing phantom appears and nothing is dropped
# ---------------------------------------------------------------------------

def test_db_list_enumerates_databases_and_tables_from_one(monkeypatch):
    be = _load_backend()
    # 1 database named ExtendMQTT with 2 tables: VariantMap, StringPool
    fake = _use(monkeypatch, be, _FakeApp(answers=[
        "1",                      # DBDatabasesGetNum
        "ExtendMQTT",             # DBDatabaseGetName(1)
        "2",                      # DBTablesGetNum(1)
        "VariantMap", "2", "4",   # table 1: name, fields, records
        "StringPool", "2", "8",   # table 2: name, fields, records
    ]))
    r = be.db_list()
    dbs = r["databases"]
    assert [d["name"] for d in dbs] == ["ExtendMQTT"], "a one-database model must list it"
    assert [t["name"] for t in dbs[0]["tables"]] == ["VariantMap", "StringPool"]
    assert not any("table_0" == t["name"] for t in dbs[0]["tables"])
    assert _calls(fake, "DBDatabaseGetName(1)"), fake.executed
    assert not _calls(fake, "DBDatabaseGetName(0)")


def test_db_list_skips_a_table_slot_that_does_not_exist(monkeypatch):
    """Live 2026-09-23: DBTablesGetNum said 7 for _RightClickConnect, but slot 7 has no
    name and answers -1 for fields and records. DBDatabasesGetNum likewise answers the
    highest index (101), not a count - hence the unnamed database slots are skipped."""
    be = _load_backend()
    _use(monkeypatch, be, _FakeApp(answers=[
        "2",                        # DBDatabasesGetNum - highest index, not a count
        "",                         # slot 1: nothing there
        "_RightClickConnect",       # slot 2
        "2",                        # DBTablesGetNum
        "Libraries", "1", "8",      # table 1: name, fields, records
        "", "-1", "-1",             # table 2: an empty slot
    ]))
    r = be.db_list()
    assert [d["name"] for d in r["databases"]] == ["_RightClickConnect"]
    assert [t["name"] for t in r["databases"][0]["tables"]] == ["Libraries"]
    assert r["databases"][0]["tableCount"] == 1


def test_db_list_keeps_an_unnamed_table_that_exists(monkeypatch):
    be = _load_backend()
    _use(monkeypatch, be, _FakeApp(answers=["1", "db", "1", "", "2", "0"]))
    r = be.db_list()
    assert [t["name"] for t in r["databases"][0]["tables"]] == ["table_1"]


def test_db_table_info_reads_every_field_including_the_last(monkeypatch):
    be = _load_backend()
    # resolve db=1 tbl=1 | records 8 | fields 2 | (name,type) x2
    fake = _use(monkeypatch, be, _FakeApp(answers=["1", "1", "8", "2", "ID", "4096", "Value", "16384"]))
    r = be.db_table_info("ExtendMQTT", "StringPool")
    assert [f["name"] for f in r["fields"]] == ["ID", "Value"]
    assert _calls(fake, "DBFieldGetName(1, 1, 2)"), "the last field must be read"
    assert not _calls(fake, "DBFieldGetName(1, 1, 0)")


# ---------------------------------------------------------------------------
# Record numbers: 0-based in the tool API, 1-based at ExtendSim
# ---------------------------------------------------------------------------

def test_db_get_value_record_zero_is_extendsims_record_one(monkeypatch):
    be = _load_backend()
    fake = _use(monkeypatch, be, _FakeApp(answers=["1", "1", "2", "7.5"]))
    r = be.db_get_value("db", "t", "f", record=0)
    assert r["record"] == 0 and r["value"] == 7.5
    assert _calls(fake, "DBDataGetAsNumber(1, 1, 2, 1)"), fake.executed


def test_db_set_value_pokes_the_translated_record(monkeypatch):
    be = _load_backend()
    fake = _use(monkeypatch, be, _FakeApp(answers=["1", "2", "3"]))
    be.db_set_value("db", "t", "f", record=9, value=42)
    assert fake.poked[0][1] == "DB:#1:2:10:3:10:3", fake.poked


def test_db_get_records_reads_real_records_and_all_fields(monkeypatch):
    be = _load_backend()
    # resolve 1,1 | records 3 | fields 2 | field types (8192 = real) x2 | 3 recs x 2 flds
    fake = _use(monkeypatch, be, _FakeApp(
        answers=["1", "1", "3", "2", "8192", "8192"] + [str(v) for v in (10, 11, 20, 21, 30, 31)]))
    r = be.db_get_records("db", "t")
    assert len(r["records"]) == 3
    assert r["records"][0] == {"field_0": 10.0, "field_1": 11.0}
    reads = _calls(fake, "DBDataGetAsNumber")
    recs = sorted({c.rstrip(");").split(",")[-1].strip() for c in reads})
    assert recs == ["1", "2", "3"], recs
    assert not any(c.endswith(", 0);") for c in reads), "record 0 does not exist"


def test_db_get_records_keeps_end_record_exclusive_in_the_api(monkeypatch):
    """0..3 in the API is ExtendSim records 1, 2, 3 - the API contract is unchanged."""
    be = _load_backend()
    fake = _use(monkeypatch, be, _FakeApp(answers=["1", "1", "10", "1", "8192"] + ["1.0"] * 10))
    r = be.db_get_records("db", "t", start_record=0, end_record=3)
    assert len(r["records"]) == 3
    recs = sorted({c.rstrip(");").split(",")[-1].strip() for c in _calls(fake, "DBDataGetAsNumber")})
    assert recs == ["1", "2", "3"], recs


def test_db_delete_records_translates_both_ends(monkeypatch):
    """API 0..2 (inclusive) is ExtendSim records 1..3."""
    be = _load_backend()
    fake = _use(monkeypatch, be, _FakeApp(answers=["1", "1", "10", "7"]))
    be.db_delete_records("db", "t", start_record=0, end_record=2)
    assert "DBRecordsDelete(1, 1, 1, 3)" in _calls(fake, "DBRecordsDelete")[0]


def test_db_add_records_appends_with_zero_by_default(monkeypatch):
    """The manual: insertAtRecord 0 appends. Passing the current count inserted the new
    record BEFORE the old last one."""
    be = _load_backend()
    fake = _use(monkeypatch, be, _FakeApp(answers=["1", "1", "5", "6"]))
    be.db_add_records("db", "t", count=1)
    assert "DBRecordsInsert(1, 1, 0, 1)" in _calls(fake, "DBRecordsInsert")[0]


def test_db_add_records_at_a_position_inserts_before_that_record(monkeypatch):
    be = _load_backend()
    fake = _use(monkeypatch, be, _FakeApp(answers=["1", "1", "5", "6"]))
    be.db_add_records("db", "t", count=1, position=2)      # before API record 2
    assert "DBRecordsInsert(1, 1, 3, 1)" in _calls(fake, "DBRecordsInsert")[0]


def test_db_find_record_translates_the_start_and_the_hit(monkeypatch):
    be = _load_backend()
    fake = _use(monkeypatch, be, _FakeApp(answers=["1", "1", "2", "4"]))  # hit at ES record 4
    r = be.db_find_record("db", "t", "f", "OK", start_record=0)
    assert r["found"] is True and r["record"] == 3, r
    assert "DBRecordFind(1, 1, 2, 1, 1, \"OK\")" in _calls(fake, "DBRecordFind")[0]


def test_db_find_record_treats_zero_as_not_found(monkeypatch):
    be = _load_backend()
    _use(monkeypatch, be, _FakeApp(answers=["1", "1", "2", "0"]))
    r = be.db_find_record("db", "t", "f", "missing")
    assert r["found"] is False, "0 is never a valid record, so it cannot be a hit"


# ---------------------------------------------------------------------------
# AI context storage - it could not have worked before
# ---------------------------------------------------------------------------

def test_context_upsert_writes_a_new_key_to_the_record_it_just_appended(monkeypatch):
    """Append puts the new record at N+1. Writing to N overwrote the previous last key;
    on an empty table it wrote to record 0, which does not exist."""
    be = _load_backend()
    monkeypatch.setattr(be, "_context_get_field_idx",
                        lambda app, db, tbl, name: {"key": 1, "value": 2}[name])
    fake = _FakeApp(answers=["2", "purpose", "notes"])     # 2 existing rows, neither matches
    be._context_upsert(fake, 1, 1, "tags", "[1]")
    writes = _calls(fake, "DBDataSetAsString")
    assert writes and all(", 3, " in w for w in writes), writes


def test_context_upsert_updates_the_matching_row_in_place(monkeypatch):
    be = _load_backend()
    monkeypatch.setattr(be, "_context_get_field_idx",
                        lambda app, db, tbl, name: {"key": 1, "value": 2}[name])
    fake = _FakeApp(answers=["2", "purpose", "notes"])
    be._context_upsert(fake, 1, 1, "notes", "updated")
    writes = _calls(fake, "DBDataSetAsString")
    assert len(writes) == 1 and "DBDataSetAsString(1, 1, 2, 2," in writes[0], writes
    assert not _calls(fake, "DBRecordsInsert"), "an existing key must not add a record"
