"""Offline coverage for the database tools (gap sweep G2, wave 3).

Nine of the fourteen `db_*` tools had no test at all. Driven against a fake COM
app, so no ExtendSim is required.

Two things here are worth a test more than the rest:

* `_resolve_db_indices` is the gateway every db tool goes through, and it is the
  one place that decides between DATABASE_NOT_FOUND, TABLE_NOT_FOUND and
  FIELD_NOT_FOUND. Getting that wrong makes every tool's error useless.
* `db_get_records` treats `endRecord` as **exclusive** while `db_delete_records`
  treats it as **inclusive**. That asymmetry is real, documented in the user
  manual, and exactly the kind of thing someone "fixes" into a data-loss bug.
  Both are pinned below so the inconsistency cannot drift silently.
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


def _use(monkeypatch, be, fake, model_open=True):
    monkeypatch.setattr(be, "get_extendsim_app", lambda create_if_missing=False: fake)
    monkeypatch.setattr(be, "_validate_model_open",
                        lambda app: {"success": True} if model_open
                        else {"success": False, "errorCode": "MODEL_NOT_OPEN"})
    return fake


def _calls(fake, needle):
    return [c for c in fake.executed if needle in c]


# ---------------------------------------------------------------------------
# _resolve_db_indices — the shared gateway
# ---------------------------------------------------------------------------

def test_resolve_db_indices_returns_each_level_of_not_found(monkeypatch):
    be = _load_backend()
    # database missing
    fake = _FakeApp(answers=["-1"])
    r = be._resolve_db_indices(fake, "nope", "t", "f")
    assert r["success"] is False and r["errorCode"] == be.ErrorCode.DATABASE_NOT_FOUND

    # database ok, table missing
    fake = _FakeApp(answers=["0", "-1"])
    r = be._resolve_db_indices(fake, "db", "nope", "f")
    assert r["success"] is False and r["errorCode"] == be.ErrorCode.TABLE_NOT_FOUND

    # database + table ok, field missing
    fake = _FakeApp(answers=["0", "1", "-1"])
    r = be._resolve_db_indices(fake, "db", "t", "nope")
    assert r["success"] is False and r["errorCode"] == be.ErrorCode.FIELD_NOT_FOUND


def test_resolve_db_indices_resolves_all_three_indices(monkeypatch):
    be = _load_backend()
    fake = _FakeApp(answers=["2", "3", "4"])
    r = be._resolve_db_indices(fake, "db", "t", "f")
    assert r == {"success": True, "dbIdx": 2, "tblIdx": 3, "fldIdx": 4}


def test_resolve_db_indices_escapes_every_name(monkeypatch):
    """W1-6 fixed this; the test keeps it fixed."""
    be = _load_backend()
    fake = _FakeApp(answers=["0", "0", "0"])
    be._resolve_db_indices(fake, 'd" ; Evil("', 't" ; Evil("', 'f" ; Evil("')
    for cmd in fake.executed:
        assert '" ; Evil("' not in cmd, cmd


def test_resolve_db_indices_stops_before_the_table_lookup_when_the_db_is_missing():
    be = _load_backend()
    fake = _FakeApp(answers=["-1"])
    be._resolve_db_indices(fake, "nope", "t", "f")
    assert _calls(fake, "DBTableGetIndex") == []


# ---------------------------------------------------------------------------
# db_get_value / db_set_value
# ---------------------------------------------------------------------------

def test_db_get_value_dispatches_on_as_string(monkeypatch):
    be = _load_backend()
    fake = _use(monkeypatch, be, _FakeApp(answers=["0", "0", "0", "7.5"]))
    r = be.db_get_value("db", "t", "f", record=3)
    assert r["success"] is True and r["value"] == 7.5
    assert _calls(fake, "DBDataGetAsNumber")

    fake = _use(monkeypatch, be, _FakeApp(answers=["0", "0", "0", "hello"]))
    r = be.db_get_value("db", "t", "f", record=3, as_string=True)
    assert r["value"] == "hello"
    assert _calls(fake, "DBDataGetAsString")


def test_db_get_value_propagates_a_resolve_failure_unchanged(monkeypatch):
    be = _load_backend()
    fake = _use(monkeypatch, be, _FakeApp(answers=["-1"]))
    r = be.db_get_value("nope", "t", "f", record=0)
    assert r["success"] is False
    assert r["errorCode"] == be.ErrorCode.DATABASE_NOT_FOUND
    assert _calls(fake, "DBDataGet") == [], "must not read after a failed resolve"


def test_db_set_value_pokes_the_documented_address_shape(monkeypatch):
    """Address is DB:#db:tbl:rec:fld:rec:fld - the record/field pair repeats."""
    be = _load_backend()
    fake = _use(monkeypatch, be, _FakeApp(answers=["1", "2", "3"]))
    r = be.db_set_value("db", "t", "f", record=9, value=42)
    assert r["success"] is True
    assert len(fake.poked) == 1
    system, address, value = fake.poked[0]
    assert system == "System"
    assert address == "DB:#1:2:9:3:9:3", address
    assert value == "42"


# ---------------------------------------------------------------------------
# endRecord: exclusive for reads, inclusive for deletes
# ---------------------------------------------------------------------------

def test_db_get_records_treats_end_record_as_exclusive(monkeypatch):
    be = _load_backend()
    # resolve(db, tbl) -> 0,0 | DBRecordsGetNum -> 10 | DBFieldsGetNum -> 1
    # | DBFieldGetProperties -> 0 (numeric) | then one read per record
    fake = _use(monkeypatch, be, _FakeApp(
        answers=["0", "0", "10", "1", "0"] + ["1.0"] * 10))
    r = be.db_get_records("db", "t", start_record=0, end_record=3)
    assert r["success"] is True
    assert len(r["records"]) == 3, "0,1,2 - end_record itself is not read"
    reads = _calls(fake, "DBDataGetAsNumber")
    assert len(reads) == 3
    assert ", 3);" not in "".join(reads), "record 3 must not be read"


def test_db_get_records_clamps_end_record_to_the_table_size(monkeypatch):
    be = _load_backend()
    _use(monkeypatch, be, _FakeApp(
        answers=["0", "0", "2", "1", "0"] + ["1.0"] * 10))
    r = be.db_get_records("db", "t", start_record=0, end_record=999)
    assert len(r["records"]) == 2, "only the records that exist"


def test_db_delete_records_treats_end_record_as_inclusive(monkeypatch):
    """Deliberately the opposite of db_get_records; both are documented."""
    be = _load_backend()
    fake = _use(monkeypatch, be, _FakeApp(answers=["0", "0", "10", "7"]))
    r = be.db_delete_records("db", "t", start_record=0, end_record=2)
    assert r["success"] is True
    delete = _calls(fake, "DBRecordsDelete")
    assert len(delete) == 1
    # start and end are passed straight through - 0..2 means three records
    assert "DBRecordsDelete(0, 0, 0, 2)" in delete[0], delete[0]


# ---------------------------------------------------------------------------
# db_table_info / db_create
# ---------------------------------------------------------------------------

def test_db_table_info_maps_field_type_codes_to_names(monkeypatch):
    be = _load_backend()
    # resolve -> 0,0 | records=5 | fields=2 | name,type | name,type
    _use(monkeypatch, be, _FakeApp(
        answers=["0", "0", "5", "2", "qty", "1", "label", "2"]))
    r = be.db_table_info("db", "t")
    assert r["success"] is True
    assert r["records"] == 5 and r["fieldCount"] == 2
    names = [f["name"] for f in r["fields"]]
    assert names == ["qty", "label"]
    types = [f["type"] for f in r["fields"]]
    assert types[0] != types[1], types


def test_db_create_reuses_an_existing_database_instead_of_recreating(monkeypatch):
    be = _load_backend()
    fake = _use(monkeypatch, be, _FakeApp(answers=["4"]))  # already exists
    r = be.db_create("existing")
    assert r["success"] is True
    assert _calls(fake, "DBDatabaseCreate") == [], "must not recreate an existing database"


def test_db_create_escapes_the_database_name(monkeypatch):
    be = _load_backend()
    fake = _use(monkeypatch, be, _FakeApp(answers=["-1", "0"]))
    be.db_create('d" ; Evil("')
    for cmd in fake.executed:
        assert '" ; Evil("' not in cmd, cmd


def test_db_create_refuses_when_no_model_is_open(monkeypatch):
    be = _load_backend()
    fake = _use(monkeypatch, be, _FakeApp(), model_open=False)
    r = be.db_create("db")
    assert r["success"] is False
    assert fake.executed == []
