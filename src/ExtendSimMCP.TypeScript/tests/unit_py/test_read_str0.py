"""_read_str0 - every string read from ExtendSim goes through it.

Live, 2026-09-23: a COM Request of a 128-character string global crashed ExtendSim
2024.1 (APPCRASH in VCRUNTIME140.dll); 127 was fine. A ModL string holds up to 255.
So _read_str0 never Requests globalStr0 itself: it copies it out through globalStr9 in
StrPart pieces (0-based, clamping at the end - both measured live), and a short piece
ends the read. The fake below implements exactly those StrPart semantics and fails the
test if anything ever Requests more than 127 characters at once.
"""
import os
import re
import sys

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


def _load_backend():
    import importlib
    try:
        return importlib.import_module("simulation_backend")
    except Exception:
        pytest.skip("simulation_backend not importable (no pywin32 in this env)")


class _StrApp:
    """globalStr0 holds a value; StrPart copies into globalStr9, as ModL does."""

    def __init__(self, value):
        self.globalStr0 = value
        self.globalStr9 = ""
        self.requests = []

    def Execute(self, cmd):
        m = re.fullmatch(r"globalStr9 = StrPart\(globalStr0, (\d+), (\d+)\);", cmd)
        assert m, f"unexpected command: {cmd}"
        start, n = int(m.group(1)), int(m.group(2))
        self.globalStr9 = self.globalStr0[start:start + n]

    def Request(self, _system, key):
        value = getattr(self, key.split("+")[0])
        assert len(value) <= 127, f"Requested {len(value)} characters at once - crashes ExtendSim"
        self.requests.append(key)
        return value


@pytest.mark.parametrize("n", [0, 1, 99, 100, 101, 127, 128, 199, 200, 201, 254, 255])
def test_reads_every_length_up_to_255_exactly(n):
    be = _load_backend()
    value = "".join(chr(65 + i % 26) for i in range(n))
    app = _StrApp(value)
    assert be._read_str0(app) == value


def test_a_short_string_costs_one_request_as_before():
    be = _load_backend()
    app = _StrApp("Queue")
    assert be._read_str0(app) == "Queue"
    assert len(app.requests) == 1


def test_never_requests_globalstr0_directly():
    be = _load_backend()
    app = _StrApp("x" * 255)
    be._read_str0(app)
    assert all(k.startswith("globalStr9") for k in app.requests), app.requests


def test_stops_at_the_255_limit_even_if_pieces_keep_coming():
    """A fake that always returns a full piece must not loop forever."""
    be = _load_backend()

    class Endless(_StrApp):
        def Execute(self, cmd):
            self.globalStr9 = "y" * 100

    app = Endless("")
    be._read_str0(app)
    assert len(app.requests) == 3, "pieces at 0, 100 and 200 cover 255 - then stop"


def test_no_call_site_reads_globalstr0_directly():
    """Every string read must go through _read_str0 - pin it at the source."""
    be = _load_backend()
    with open(be.__file__, encoding="utf-8") as f:
        src = f.read()
    direct = [line.strip() for line in src.splitlines()
              if '.Request("System", "globalStr0' in line and not line.lstrip().startswith("#")]
    assert direct == [], direct
