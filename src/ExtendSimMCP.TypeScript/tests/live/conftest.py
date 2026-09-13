"""
Live COM test fixtures for ExtendSim integration testing.

These tests require a running ExtendSim instance on Windows.
All tests are automatically skipped if ExtendSim is not available.
"""

import pytest
import sys
import os
import tempfile

# Add src/ to path so we can import simulation_backend helpers
_SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)


@pytest.fixture(scope="session")
def app():
    """Get ExtendSim COM application. Skip ALL tests if unavailable."""
    try:
        import win32com.client
        es_app = win32com.client.GetActiveObject("ExtendSim.Application")
        # Verify it's responsive
        es_app.Request("System", "global0+:0:0:0")
        return es_app
    except Exception as e:
        pytest.skip(f"ExtendSim not available: {e}")


@pytest.fixture(scope="function")
def fresh_model(app):
    """Create a fresh empty model for each test. Saves to temp file, closes after.

    The temp save is required because block_configure for Activity/Create
    calls _persist_popup_change (save/close/reopen). Without a saved file,
    ExtendSim shows a "Save As" dialog that blocks COM.
    """
    app.Execute("ExecuteMenuCommand(2)")
    # Save to temp file immediately so _persist_popup_change works
    temp_dir = tempfile.gettempdir().replace("\\", "/")
    temp_path = f"{temp_dir}/pytest_live_test.mox"
    app.Execute(f'SaveModelAs("{temp_path}")')
    yield app
    try:
        app.Execute("SetDirty(False);")
        app.Execute("ExecuteMenuCommand(4);")
    except Exception:
        pass


def add_block(app, library: str, block_type: str, label: str = "") -> int:
    """Helper: add a block and return its ID."""
    from simulation_backend import parse_float

    before = set()
    current = -1
    while True:
        app.Execute(f"global0 = objectIDNext({current}, 0);")
        nxt = int(parse_float(app.Request("System", "global0+:0:0:0")))
        if nxt == -1:
            break
        before.add(nxt)
        current = nxt

    app.Execute(f'PlaceBlock("{block_type}", "{library}", 100, 100, -1, 2);')

    current = -1
    while True:
        app.Execute(f"global0 = objectIDNext({current}, 0);")
        nxt = int(parse_float(app.Request("System", "global0+:0:0:0")))
        if nxt == -1:
            break
        if nxt not in before:
            if label:
                app.Execute(f'SetBlockLabel({nxt}, "{label}");')
            return nxt
        current = nxt

    raise RuntimeError(f"Failed to add {block_type} from {library}")


def get_popup_label(app, block_id: int, popup_var: str, index: int) -> str:
    """Helper: read a popup menu item label by index."""
    app.Execute(f'globalStr0 = GetDialogItemLabel({block_id}, "{popup_var}", {index});')
    return app.Request("System", "globalStr0+:0:0:0").strip()
