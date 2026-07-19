# tests/unit_py/test_block_introspect_logic.py
import os, sys
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
from simulation_backend import _enumerate_dialog_items


class FakeApp:
    """Scripts DIGetName / GetDialogItemInfo / GetDialogVariable results.
    dialog[i] = (name, typeCode, readOnly, enabled, value). Empty name = 'Unused'."""
    def __init__(self, dialog):
        self.dialog = dialog
        self._last = None
    def Execute(self, cmd):
        self._last = cmd  # remember which query is pending
    def Request(self, _system, _q):
        cmd = self._last
        # DIGetName({bid}, {dialog_id})
        if "DIGetName" in cmd:
            did = int(cmd.split(",")[1].split(")")[0])
            if did < len(self.dialog) and self.dialog[did][0]:
                return self.dialog[did][0]
            return "Unused"
        # GetDialogItemInfo({bid}, "name", which)
        if "GetDialogItemInfo" in cmd:
            name = cmd.split('"')[1]
            which = int(cmd.rsplit(",", 1)[1].split(")")[0])
            rec = next(d for d in self.dialog if d[0] == name)
            # Request always returns a string over COM (matches the
            # str(self.global0) convention used by other fake COM apps in
            # this suite, e.g. test_connection_list_array_slots.py).
            return str({4: rec[1], 3: 1 if rec[2] else 0, 2: 1 if rec[3] else 0}[which])
        if "GetDialogVariable" in cmd:
            name = cmd.split('"')[1]
            rec = next(d for d in self.dialog if d[0] == name)
            return rec[4]
        return ""


def test_enumerate_returns_named_items_and_stops_after_unused_run():
    # one parameter with a value, then all Unused
    dialog = [("Speed", 5, False, True, "42")] + [("", 0, False, False, "")] * 30
    items = _enumerate_dialog_items(FakeApp(dialog), block_id=7, max_dialog_id=200)
    assert [i["name"] for i in items] == ["Speed"]
    assert items[0]["typeCode"] == 5
    assert items[0]["value"] == "42"
    assert items[0]["readOnly"] is False


def test_enumerate_dedupes_repeated_names():
    dialog = [("A", 5, False, True, "1"), ("A", 5, False, True, "1")] + [("", 0, False, False, "")] * 30
    items = _enumerate_dialog_items(FakeApp(dialog), block_id=7)
    assert [i["name"] for i in items] == ["A"]


def test_enumerate_captures_enabled_flag():
    dialog = [("Locked", 5, False, False, "9")] + [("", 0, False, False, "")] * 30
    items = _enumerate_dialog_items(FakeApp(dialog), block_id=7)
    assert items[0]["enabled"] is False


def test_enumerate_unknown_typecode_falls_back_and_reads_no_value():
    # typeCode 99 is not a known dialog-item type and not in the value-read set (5, 8, 21)
    dialog = [("Mystery", 99, False, True, "")] + [("", 0, False, False, "")] * 30
    items = _enumerate_dialog_items(FakeApp(dialog), block_id=7)
    assert items[0]["type"] == "unknown(99)"
    assert items[0]["typeCode"] == 99
    assert "value" not in items[0]


from simulation_backend import _select_scalar_reads
from lbr_stat import StatVar

def _sv(name, is_scalar, dim_count):
    return StatVar(name=name, data_type=3 if is_scalar else 7,
                   data_type_label="numeric" if is_scalar else "string",
                   is_scalar=is_scalar, dim_count=dim_count, dim_sizes=(0, 0))

def test_only_scalars_are_selected_for_value_reads():
    stat = [_sv("siMe", True, 0), _sv("dsPythonCode", False, 1), _sv("weird", False, 0),
            _sv("dimScalar", True, 2)]
    assert _select_scalar_reads(stat) == ["siMe"]
