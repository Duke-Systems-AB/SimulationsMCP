"""Offline unit tests must never reach a real ExtendSim.

simulation_backend has five direct COM entry points - GetActiveObject in
get_extendsim_app, extendsim_status, extendsim_start and simulation_run's background
thread, and Dispatch in get_extendsim_app and extendsim_start. Dispatch LAUNCHES
ExtendSim when none is running.

A unit test that forgets to patch get_extendsim_app therefore connects to - or starts -
the ExtendSim on whatever machine runs it. On 2026-09-23 exactly that happened: a test
patched the wrong function and read the model name from the developer's live ExtendSim.
It only read, but the same slip in a test that writes would drive a real model, and a
second COM driver is how ExtendSim gets wedged (see CLAUDE.md).

This fixture replaces both entry points with ones that fail loudly, for every test. A
test that needs COM behaviour patches get_extendsim_app (the normal pattern) or, where
the code calls GetActiveObject directly, patches that itself - its monkeypatch runs
after this one and wins.
"""
import os
import sys

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


def _refuse(*_args, **_kwargs):
    raise RuntimeError(
        "Unit test tried to reach a real ExtendSim over COM. Patch get_extendsim_app "
        "(or win32com.client.GetActiveObject where the code calls it directly) with a fake.")


@pytest.fixture(autouse=True)
def _no_real_com(monkeypatch):
    try:
        import win32com.client
    except Exception:
        yield
        return
    monkeypatch.setattr(win32com.client, "GetActiveObject", _refuse)
    monkeypatch.setattr(win32com.client, "Dispatch", _refuse)
    try:
        import simulation_backend as be
        # A cached app object from any earlier call must not leak into a test.
        monkeypatch.setattr(be, "_es_app", None, raising=False)
    except Exception:
        pass
    yield
