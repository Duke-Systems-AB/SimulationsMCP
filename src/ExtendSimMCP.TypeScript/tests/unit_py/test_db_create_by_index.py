"""Creating and deleting database structure goes through the ...ByIndex ModL forms.

Found in the live check of 2026-09-23. ExtendSim's CodeCompletion/Application.ini
documents the signatures:

    DBTableCreate(databaseName, tableName)      DBTableCreateByIndex(databaseIndex, tableName)
    DBFieldCreate(databaseName, tableName, ...) DBFieldCreateByIndex(databaseIndex, tableIndex, ...)
    DBTableDelete(databaseName, tableName)      DBTableDeleteByIndex(databaseIndex, tableIndex)
    DBDatabaseDelete(databaseName)              DBDatabaseDeleteByIndex(databaseIndex)

The server passed indices to the NAME forms. Live, db_create made the database but no
table ("Failed to create table") and still answered success; the AI context tables
could never have been created; context_clear could not delete anything.

Field types were wrong too: the DB_FIELDTYPE_* constants read live are 4096 integer,
4097 boolean, 8192 real, 16384 string (and more), not 0-3 - and the context tables
asked for format 4, which is no type at all.
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
        self.answers = list(answers or [])

    def Execute(self, cmd):
        self.executed.append(cmd)

    def Request(self, _system, _query):
        return self.answers.pop(0) if self.answers else "0"


def _use(monkeypatch, be, fake):
    monkeypatch.setattr(be, "get_extendsim_app", lambda create_if_missing=False: fake)
    monkeypatch.setattr(be, "_validate_model_open", lambda app: {"success": True})
    return fake


def _calls(fake, needle):
    return [c for c in fake.executed if needle in c]


def test_the_field_type_map_holds_the_live_constant_values():
    be = _load_backend()
    assert be.DB_FIELD_TYPE_MAP[4096] == "integer"
    assert be.DB_FIELD_TYPE_MAP[4097] == "boolean"
    assert be.DB_FIELD_TYPE_MAP[8192] == "real"
    assert be.DB_FIELD_TYPE_MAP[16384] == "string"
    assert set(be.DB_FIELD_TYPE_REVERSE) == {"real", "integer", "string", "boolean"}


def test_db_create_uses_the_by_index_forms_and_type_constants(monkeypatch):
    be = _load_backend()
    # DBDatabaseGetIndex -1 | DBDatabaseCreate 3 | DBTableGetIndex -1 | TableCreateByIndex 1
    # | Name: GetIndex -1, create 1 | Qty: GetIndex -1, create 2
    fake = _use(monkeypatch, be, _FakeApp(answers=["-1", "3", "-1", "1", "-1", "1", "-1", "2"]))
    r = be.db_create("Plant", [{"name": "T", "fields": [{"name": "Name", "type": "string"},
                                                         {"name": "Qty", "type": "real"}]}])
    assert r["success"] is True, r
    assert _calls(fake, 'DBTableCreateByIndex(3, "T")')
    assert not [c for c in fake.executed if "DBTableCreate(" in c]
    fields = _calls(fake, "DBFieldCreateByIndex")
    assert 'DBFieldCreateByIndex(3, 1, "Name", DB_FIELDTYPE_STRING_VALUE, 0, 0, 0, 0)' in fields[0]
    assert 'DBFieldCreateByIndex(3, 1, "Qty", DB_FIELDTYPE_REAL_GENERAL, 5, 0, 0, 0)' in fields[1]


def test_db_create_fails_closed_when_a_table_is_not_created(monkeypatch):
    """Live, this came back success with the table's error buried in the payload."""
    be = _load_backend()
    _use(monkeypatch, be, _FakeApp(answers=["-1", "3", "-1", "-5"]))
    r = be.db_create("Plant", [{"name": "T"}])
    assert r["success"] is False
    assert r["errorCode"] == be.ErrorCode.DB_OPERATION_FAILED
    assert "table 'T'" in r["error"]
    assert r["createdDatabase"] is True, "the client must learn the database itself exists"


def test_db_create_fails_closed_when_a_field_is_not_created(monkeypatch):
    be = _load_backend()
    _use(monkeypatch, be, _FakeApp(answers=["3", "1", "-1", "-2"]))
    r = be.db_create("Plant", [{"name": "T", "fields": [{"name": "Qty"}]}])
    assert r["success"] is False and "field 'T.Qty'" in r["error"]


def test_context_tables_are_created_by_index_as_string_fields():
    be = _load_backend()
    # DB exists (2) | context table missing -> 1 | key 1, value 2
    # | history missing -> 2 | timestamp 1, summary 2, details 3
    fake = _FakeApp(answers=["2", "-1", "1", "1", "2", "-1", "2", "1", "2", "3"])
    assert be._ensure_context_db(fake) == (2, 1, 2)
    creates = _calls(fake, "DBFieldCreateByIndex")
    assert len(creates) == 5
    assert all("DB_FIELDTYPE_STRING_VALUE" in c for c in creates), creates
    assert not [c for c in fake.executed if "DBFieldCreate(" in c]


def test_context_setup_raises_when_a_field_is_not_created():
    be = _load_backend()
    import pytest
    with pytest.raises(RuntimeError, match="key"):
        be._ensure_context_db(_FakeApp(answers=["2", "-1", "1", "-3"]))


def test_context_clear_deletes_by_index_and_verifies(monkeypatch):
    be = _load_backend()
    # db 4 | hist 2 | db 4 | ctx 1 | db 4 | after delete: -1
    fake = _use(monkeypatch, be, _FakeApp(answers=["4", "2", "4", "1", "4", "-1"]))
    r = be.context_clear(confirm=True)
    assert r["success"] is True, r
    assert _calls(fake, "DBTableDeleteByIndex(4, 2)") and _calls(fake, "DBTableDeleteByIndex(4, 1)")
    assert _calls(fake, "DBDatabaseDeleteByIndex(4)")


def test_context_clear_fails_closed_when_the_database_survives(monkeypatch):
    be = _load_backend()
    _use(monkeypatch, be, _FakeApp(answers=["4", "-1", "4", "-1", "4", "4"]))
    r = be.context_clear(confirm=True)
    assert r["success"] is False
    assert r["errorCode"] == be.ErrorCode.DB_OPERATION_FAILED


def test_context_set_refuses_a_value_longer_than_a_modl_string_and_writes_nothing(monkeypatch):
    """Live 2026-09-23: a 397-character note was lost while context_set said success.
    A ModL string holds at most 255 characters, however the literal is built."""
    be = _load_backend()
    fake = _use(monkeypatch, be, _FakeApp())
    r = be.context_set(purpose="ok", notes="n" * 256)
    assert r["success"] is False
    assert r["errorCode"] == be.ErrorCode.INVALID_PARAMETER
    assert "'notes' (256 characters)" in r["error"]
    assert fake.executed == [], "nothing may be written - not even the valid key"


def test_context_set_accepts_exactly_255_characters(monkeypatch):
    be = _load_backend()
    monkeypatch.setattr(be, "_ensure_context_db", lambda app: (1, 1, 2))
    written = []
    monkeypatch.setattr(be, "_context_upsert",
                        lambda app, db, tbl, key, val: written.append((key, len(val))))
    _use(monkeypatch, be, _FakeApp())
    r = be.context_set(notes="n" * 255)
    assert r["success"] is True and written == [("notes", 255)]
