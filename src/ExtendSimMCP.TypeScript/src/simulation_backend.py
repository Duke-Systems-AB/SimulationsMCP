"""
ExtendSim COM Bridge - Python Backend

Handles COM communication with ExtendSim.
Receives JSON commands via stdin, returns JSON results via stdout.
"""

import sys
import json
import os
import win32com.client
from typing import Any, Optional

# Startup log - written immediately on import to verify correct file is running
_log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..", "temp")
if not os.path.isdir(_log_dir):
    _log_dir = os.path.dirname(os.path.abspath(__file__))
_startup_log = os.path.join(_log_dir, "python_startup.log")
try:
    with open(_startup_log, "w", encoding="utf-8") as _f:
        _f.write(f"Python started\n")
        _f.write(f"__file__ = {__file__}\n")
        _f.write(f"cwd = {os.getcwd()}\n")
        _f.write(f"sys.argv = {sys.argv}\n")
except Exception:
    pass  # Non-critical: logging failure shouldn't prevent startup


def parse_float(value: str) -> float:
    """Converts string to float, handles Swedish decimal separator.
    Also handles NaN/Infinity values for safe JSON serialization.
    Returns 0.0 for NaN/Infinity (safe for int() callers)."""
    if not value:
        return 0.0
    cleaned = value.strip().replace(",", ".")
    # Handle Windows-specific NaN formats (e.g. "-nan(ind)") before float()
    lower = cleaned.lower()
    if 'nan' in lower or cleaned in ('', '-'):
        return 0.0
    if 'inf' in lower:
        return 0.0
    result = float(cleaned)
    # Double-check: Python float NaN/Inf
    if result != result:  # NaN check
        return 0.0
    if result == float('inf') or result == float('-inf'):
        return 0.0
    return result


def parse_float_nullable(value: str) -> Optional[float]:
    """Like parse_float but returns None for NaN/Infinity/empty values.
    Use for statistics where null is more meaningful than 0."""
    if not value:
        return None
    cleaned = value.strip().replace(",", ".")
    lower = cleaned.lower()
    if 'nan' in lower or cleaned in ('', '-'):
        return None
    if 'inf' in lower:
        return None
    try:
        result = float(cleaned)
    except (ValueError, TypeError):
        return None
    if result != result:  # NaN check
        return None
    if result == float('inf') or result == float('-inf'):
        return None
    return result


def _escape_modl_string(s: str) -> str:
    """Escapes a string for safe use inside ModL f-string commands.

    Handles backslashes, double quotes, and parentheses that could
    break or inject into ModL command strings.
    """
    if s is None:
        return ""
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


# ============================================================================
# VARIABLE API ROUTING
# ============================================================================
# ExtendSim has TWO variable APIs that access DIFFERENT internal storage:
# - SetVariableNumeric/GetVariableNumeric → REAL variables (used by simulation)
# - SetDialogVariable/GetDialogVariable → SHADOW for numeric, REAL for tables/text
#
# Suffix rule determines which API to use:
# _prm, _pop, _chk, _rdo, no suffix → SetVariableNumeric (real)
# _dtbl, _ttbl → SetDialogVariable (table variables)
# _dtxt → SetDialogVariable (dynamic text, numeric value only)

_TABLE_SUFFIXES = ("_dtbl", "_ttbl")
_TEXT_SUFFIXES = ("_dtxt",)
_DIALOG_VAR_SUFFIXES = _TABLE_SUFFIXES + _TEXT_SUFFIXES


def _set_var(app, block_id: int, var_name: str, value, row: int = 0, col: int = 0, msg: int = 1):
    """Set a block variable using the correct API based on suffix.

    _prm/_pop/_chk/_rdo and no-suffix → SetVariableNumeric (real variables)
    _dtbl/_ttbl → SetDialogVariable (table variables)
    _dtxt → SetDialogVariable with numeric value only

    Args:
        msg: Message flag for SetVariableNumeric (1=notify block, 0=silent)
    """
    if isinstance(var_name, str) and var_name.endswith(_TABLE_SUFFIXES):
        # Table variables — must use SetDialogVariable
        if isinstance(value, str):
            app.Execute(f'SetDialogVariable({block_id}, "{var_name}", "{value}", {row}, {col});')
        else:
            app.Execute(f'SetDialogVariable({block_id}, "{var_name}", {value}, {row}, {col});')
    elif isinstance(var_name, str) and var_name.endswith(_TEXT_SUFFIXES):
        # Dynamic text — SetDialogVariable (string value OK for _dtxt)
        if isinstance(value, str):
            app.Execute(f'SetDialogVariable({block_id}, "{var_name}", "{value}", {row}, {col});')
        else:
            app.Execute(f'SetDialogVariable({block_id}, "{var_name}", {value}, {row}, {col});')
    else:
        # Numeric variables (_prm, _pop, _chk, _rdo, no suffix) → SetVariableNumeric
        app.Execute(f'SetVariableNumeric({block_id}, "{var_name}", {value}, {row}, {col}, {msg});')


def _get_var(app, block_id: int, var_name: str, row: int = 0, col: int = 0):
    """Get a block variable using the correct API based on suffix.

    Returns: raw string value from COM
    """
    if isinstance(var_name, str) and var_name.endswith(_DIALOG_VAR_SUFFIXES):
        app.Execute(f'globalStr0 = GetDialogVariable({block_id}, "{var_name}", {row}, {col});')
        return app.Request("System", "globalStr0+:0:0:0")
    else:
        app.Execute(f'global0 = GetVariableNumeric({block_id}, "{var_name}", {row}, {col});')
        return app.Request("System", "global0+:0:0:0")


def _set_var_string(app, block_id: int, var_name: str, value: str, row: int = 0, col: int = 0):
    """Set a string value on a block variable. Always uses SetDialogVariable.

    Use this for variables that hold string values (attribute names, pool names)
    regardless of their suffix, since SetVariableNumeric cannot handle strings.
    """
    app.Execute(f'SetDialogVariable({block_id}, "{var_name}", "{_escape_modl_string(value)}", {row}, {col});')


def _set_dialog_var(app, block_id: int, var_name: str, value, row: int = 0, col: int = 0):
    """Write a dialog cell via SetDialogVariable regardless of suffix.

    Needed for string-tables named without a _ttbl suffix (e.g. Queue
    'ResourceTable') and edittext fields ('ResourcePoolName'), where the
    suffix-based _set_var routing would wrongly use SetVariableNumeric (a silent
    no-op on string cells). String values are quoted; numbers written bare.
    """
    if isinstance(value, str):
        app.Execute(f'SetDialogVariable({block_id}, "{var_name}", "{_escape_modl_string(value)}", {row}, {col});')
    else:
        app.Execute(f'SetDialogVariable({block_id}, "{var_name}", {value}, {row}, {col});')


def _get_dialog_string(app, block_id: int, var_name: str, row: int = 0, col: int = 0) -> str:
    """Read a dialog cell via GetDialogVariable as a string regardless of suffix.

    Suffix-less string/popup vars (ResourcePoolName, ResourceTable, Serverblocks_pop)
    read as '-nan(ind)' through GetVariableNumeric; GetDialogVariable returns the
    text (or the popup's index as text)."""
    app.Execute(f'globalStr0 = GetDialogVariable({block_id}, "{var_name}", {row}, {col});')
    return app.Request("System", "globalStr0+:0:0:0")


# ============================================================================
# ERROR CODES
# ============================================================================

class ErrorCode:
    """Structured error codes for MCP responses."""
    # Connection errors
    COM_ERROR = "COM_ERROR"
    COM_CONNECTION_LOST = "COM_CONNECTION_LOST"
    EXTENDSIM_NOT_RUNNING = "EXTENDSIM_NOT_RUNNING"
    EXTENDSIM_START_FAILED = "EXTENDSIM_START_FAILED"

    # Model errors
    MODEL_NOT_FOUND = "MODEL_NOT_FOUND"
    MODEL_NOT_OPEN = "MODEL_NOT_OPEN"
    MODEL_OPEN_FAILED = "MODEL_OPEN_FAILED"
    MODEL_SAVE_FAILED = "MODEL_SAVE_FAILED"

    # Block errors
    BLOCK_NOT_FOUND = "BLOCK_NOT_FOUND"
    WRONG_BLOCK_TYPE = "WRONG_BLOCK_TYPE"
    BLOCK_ADD_FAILED = "BLOCK_ADD_FAILED"
    BLOCK_REMOVE_FAILED = "BLOCK_REMOVE_FAILED"

    # Connector errors
    INVALID_CONNECTOR = "INVALID_CONNECTOR"
    CONNECTOR_NOT_FOUND = "CONNECTOR_NOT_FOUND"
    CONNECTION_FAILED = "CONNECTION_FAILED"

    # Simulation errors
    SIMULATION_RUN_FAILED = "SIMULATION_RUN_FAILED"
    SIMULATION_TIMEOUT = "SIMULATION_TIMEOUT"

    # Parameter errors
    INVALID_PARAMETER = "INVALID_PARAMETER"
    SET_VALUE_FAILED = "SET_VALUE_FAILED"
    GET_VALUE_FAILED = "GET_VALUE_FAILED"

    # Command errors
    UNKNOWN_COMMAND = "UNKNOWN_COMMAND"
    COMMAND_FAILED = "COMMAND_FAILED"
    INVALID_JSON = "INVALID_JSON"

    # Template errors
    TEMPLATE_NOT_FOUND = "TEMPLATE_NOT_FOUND"

    # Multi-run errors
    MULTI_RUN_FAILED = "MULTI_RUN_FAILED"

    # Database errors
    DATABASE_NOT_FOUND = "DATABASE_NOT_FOUND"
    TABLE_NOT_FOUND = "TABLE_NOT_FOUND"
    FIELD_NOT_FOUND = "FIELD_NOT_FOUND"
    DB_OPERATION_FAILED = "DB_OPERATION_FAILED"

    # Hierarchy errors
    NOT_AN_HBLOCK = "NOT_AN_HBLOCK"

    # Optimizer/Scenario errors
    OPTIMIZER_TIMEOUT = "OPTIMIZER_TIMEOUT"
    OPTIMIZER_FAILED = "OPTIMIZER_FAILED"

    # General errors
    NOT_CONNECTED = "NOT_CONNECTED"
    MISSING_PARAMETER = "MISSING_PARAMETER"

    # License errors
    LICENSE_DETECTION_FAILED = "LICENSE_DETECTION_FAILED"


def _error(code: str, message: str, **extra) -> dict:
    """Creates a standardized error response.

    Args:
        code: ErrorCode constant
        message: Human-readable error description
        **extra: Additional fields to include in the response

    Returns:
        Dictionary with success=False, errorCode, and error message
    """
    result = {"success": False, "errorCode": code, "error": message}
    result.update(extra)
    return result


def _com_error(e: Exception, operation: str = "") -> dict:
    """Creates a COM error response with context."""
    msg = f"COM error during {operation}: {e}" if operation else f"COM error: {e}"
    return _error(ErrorCode.COM_ERROR, msg)


# Simulation phase name map (used by simulation_status, simulation_step, simulation_get_state)
SIMULATION_PHASE_NAMES = {
    0: "idle",
    1: "checkData",
    2: "initSim",
    3: "initSim",
    4: "simulate",
    5: "finalCalc",
    6: "endSim",
    7: "endSim",
    8: "abortSim",
    9: "cleanUp",
}

# DB field type map for DBFieldGetProperties which=1
DB_FIELD_TYPE_MAP = {
    0: "real",
    1: "integer",
    2: "string",
    3: "boolean"
}

# Reverse map for db_create
DB_FIELD_TYPE_REVERSE = {
    "real": 0,
    "integer": 1,
    "string": 2,
    "boolean": 3
}

# Global ExtendSim application reference
_es_app: Optional[Any] = None

# Track used array connector slots per block to avoid reusing slots in same session
# Key: (block_id, connector_name), Value: set of used slot indices
_used_array_slots: dict = {}


def _clear_array_slot_tracking():
    """Clear the session tracking for array connector slots.

    Should be called when a new model is opened/created to reset tracking.
    """
    global _used_array_slots
    _used_array_slots = {}


_com_log_path = os.path.join(_log_dir, "com_debug.log")
_debug_logging = os.environ.get("EXTENDSIM_DEBUG", "").lower() in ("1", "true", "yes")


def _com_log(msg):
    if not _debug_logging:
        return
    try:
        with open(_com_log_path, "a", encoding="utf-8") as f:
            f.write(f"{msg}\n")
    except Exception:
        sys.stderr.write(f"[COM_LOG FAIL] {msg}\n")


def get_extendsim_app(create_if_missing: bool = False):
    """Gets existing ExtendSim COM instance. Does NOT create new instance if create_if_missing=False."""
    global _es_app

    _com_log(f"get_extendsim_app called, create_if_missing={create_if_missing}, _es_app={_es_app}")

    # Verify cached connection is still alive
    if _es_app is not None:
        _com_log("Checking if cached connection is alive...")
        try:
            _es_app.Request("System", "global0+:0:0:0")
            _com_log("Cached connection OK")
        except Exception as e:
            _com_log(f"Cached connection STALE: {type(e).__name__}: {e}")
            _es_app = None

    if _es_app is None:
        _com_log("No cached app, trying GetActiveObject...")
        try:
            _es_app = win32com.client.GetActiveObject("ExtendSim.Application")
            _com_log(f"GetActiveObject OK: type={type(_es_app).__name__}")
        except Exception as e:
            _com_log(f"GetActiveObject FAILED: {type(e).__name__}: {e}")
            if create_if_missing:
                _com_log("Calling Dispatch (create_if_missing=True)...")
                _es_app = win32com.client.Dispatch("ExtendSim.Application")
                _com_log(f"Dispatch OK: type={type(_es_app).__name__}")
            else:
                _com_log("Returning None (create_if_missing=False)")
                return None

    _com_log(f"Returning app: type={type(_es_app).__name__}")
    return _es_app


# Block reference data (loaded from JSON file)
_block_reference: Optional[dict] = None

# Templates data (loaded from JSON file)
_templates: Optional[dict] = None


def _load_templates() -> dict:
    """Loads template definitions from templates.json."""
    global _templates
    if _templates is None:
        tpl_path = os.path.join(os.path.dirname(__file__), "templates.json")
        try:
            with open(tpl_path, "r", encoding="utf-8") as f:
                _templates = json.load(f)
        except Exception:
            _templates = {}
    return _templates


def _load_block_reference() -> dict:
    """Loads block reference data from block_reference.json."""
    global _block_reference
    if _block_reference is None:
        ref_path = os.path.join(os.path.dirname(__file__), "block_reference.json")
        try:
            with open(ref_path, "r", encoding="utf-8") as f:
                _block_reference = json.load(f)
        except Exception:
            _block_reference = {"libraries": {}}
    return _block_reference


def _get_block_description(block_data) -> str:
    """Gets description text regardless of whether block_data is a string or object."""
    if isinstance(block_data, str):
        return block_data
    if isinstance(block_data, dict):
        return block_data.get("description", "")
    return ""


def _build_block_match(block_name, block_data, lib_data, lib_key, cat_name) -> dict:
    """Builds a match result for block_info reference query.

    Handles both old format (string) and new format (object with guide data).
    """
    entry = {
        "blockName": block_name,
        "library": lib_data.get("name", lib_key),
        "libraryFile": lib_key,
        "category": cat_name,
        "description": _get_block_description(block_data)
    }
    # If new format: include all guide data
    if isinstance(block_data, dict):
        for key in ("connectors", "modes", "usage", "patterns", "notes"):
            if key in block_data:
                entry[key] = block_data[key]
    return entry


def extendsim_status() -> dict:
    """Checks if ExtendSim is running and returns status."""
    global _es_app

    try:
        # Try to connect to existing instance (without creating new)
        app = win32com.client.GetActiveObject("ExtendSim.Application")
        _es_app = app  # Save reference

        # ExtendSim is running - get more info
        result = {
            "running": True,
            "connected": True
        }

        # Try to get model name
        try:
            app.Execute("globalStr0 = GetModelName();")
            model_name = app.Request("System", "globalStr0+:0:0:0")
            result["modelOpen"] = bool(model_name)
            result["modelName"] = model_name or ""
        except Exception:
            result["modelOpen"] = False
            result["modelName"] = ""

        return result

    except Exception as e:
        # ExtendSim is not running or not available
        _es_app = None
        return {
            "running": False,
            "connected": False,
            "modelOpen": False,
            "modelName": "",
            "errorCode": ErrorCode.EXTENDSIM_NOT_RUNNING,
            "error": str(e)
        }


def extendsim_start() -> dict:
    """Starts ExtendSim if not already running."""
    global _es_app

    try:
        # Check if ExtendSim is already running
        try:
            app = win32com.client.GetActiveObject("ExtendSim.Application")
            _es_app = app
            return {
                "success": True,
                "alreadyRunning": True,
                "message": "ExtendSim was already running"
            }
        except Exception:
            pass

        # Start ExtendSim
        app = win32com.client.Dispatch("ExtendSim.Application")
        _es_app = app

        # Make window visible
        try:
            app.Visible = True
        except Exception:
            pass

        return {
            "success": True,
            "alreadyRunning": False,
            "message": "ExtendSim started successfully"
        }

    except Exception as e:
        return _error(ErrorCode.EXTENDSIM_START_FAILED, str(e))


def detect_license(model_id: Optional[str] = None) -> dict:
    """Detects the ExtendSim license level (CP/DE/Pro) by checking library availability.

    Uses IsLibEnabled(17) for Item library (DE+) and IsLibEnabled(18) for Rate library (Pro).
    """
    try:
        app = get_extendsim_app()
        if app is None:
            return _error(ErrorCode.EXTENDSIM_NOT_RUNNING, "ExtendSim is not running",
                         suggestion="Start ExtendSim first with extendsim_start()")

        # Check Item library (DE or Pro)
        app.Execute("global0 = IsLibEnabled(17);")
        has_item = int(parse_float(app.Request("System", "global0+:0:0:0")))

        # Check Rate/Reliability library (Pro only)
        app.Execute("global0 = IsLibEnabled(18);")
        has_rate = int(parse_float(app.Request("System", "global0+:0:0:0")))

        if has_rate:
            license_type = "Pro"
            libraries = ["Value", "Item", "Rate"]
            block_libraries = ["Value.lbr", "Item.lbr", "Rate.lbr"]
            simulation_types = ["continuous", "discrete", "flow", "rbd"]
        elif has_item:
            license_type = "DE"
            libraries = ["Value", "Item"]
            block_libraries = ["Value.lbr", "Item.lbr"]
            simulation_types = ["continuous", "discrete"]
        else:
            license_type = "CP"
            libraries = ["Value"]
            block_libraries = ["Value.lbr"]
            simulation_types = ["continuous"]

        return {
            "success": True,
            "license": license_type,
            "libraries": libraries,
            "blockLibraries": block_libraries,
            "simulationTypes": simulation_types
        }

    except Exception as e:
        return _error(ErrorCode.LICENSE_DETECTION_FAILED,
                     f"Failed to detect license: {e}")


def model_open(file_path: str, read_only: bool = False) -> dict:
    """Opens an ExtendSim model."""
    try:
        # Clear array slot tracking for new model
        _clear_array_slot_tracking()

        app = get_extendsim_app()
        file_path_normalized = file_path.replace("\\", "/")
        expected_model_name = file_path.split("\\")[-1].split("/")[-1]

        # Check if model is already open
        app.Execute("globalStr0 = GetModelName();")
        current_model_name = app.Request("System", "globalStr0+:0:0:0")

        if current_model_name and current_model_name.lower() == expected_model_name.lower():
            result = {
                "success": True,
                "modelId": "model_1",
                "name": current_model_name,
                "filePath": file_path,
                "alreadyOpen": True,
                "blockCount": 0
            }
            # Auto-load AI context if present
            try:
                ctx = _context_read_all(app)
                if ctx is not None:
                    result["context"] = ctx
            except Exception:
                pass  # Don't fail model_open if context read fails
            return result

        # Open the model
        cmd = f'OpenExtendFile("{_escape_modl_string(file_path_normalized)}")'
        app.Execute(cmd)

        # Get model name
        app.Execute("globalStr0 = GetModelName();")
        model_name = app.Request("System", "globalStr0+:0:0:0")

        result = {
            "success": True,
            "modelId": "model_1",
            "name": model_name,
            "filePath": file_path,
            "alreadyOpen": False,
            "blockCount": 0
        }
        # Auto-load AI context if present
        try:
            ctx = _context_read_all(app)
            if ctx is not None:
                result["context"] = ctx
        except Exception:
            pass  # Don't fail model_open if context read fails
        return result
    except Exception as e:
        return _error(ErrorCode.MODEL_OPEN_FAILED, str(e), filePath=file_path)


def model_save(model_id: Optional[str] = None, file_path: Optional[str] = None) -> dict:
    """Saves the model."""
    try:
        app = get_extendsim_app()

        if file_path:
            file_path_normalized = file_path.replace("\\", "/")
            app.Execute(f'SaveModelAs("{_escape_modl_string(file_path_normalized)}")')
        else:
            app.Execute("SaveModel()")

        # Get actual model name
        app.Execute("globalStr0 = GetModelName();")
        model_name = app.Request("System", "globalStr0+:0:0:0")

        return {"success": True, "filePath": file_path or model_name or "unknown"}
    except Exception as e:
        return _error(ErrorCode.MODEL_SAVE_FAILED, str(e))


def model_list() -> dict:
    """Lists open models."""
    # ExtendSim typically has only one model open at a time
    try:
        app = get_extendsim_app()
        app.Execute("globalStr0 = GetModelName();")
        model_name = app.Request("System", "globalStr0+:0:0:0")

        if model_name:
            return {"models": [{"modelId": "model_1", "name": model_name}]}
        return {"models": []}
    except Exception as e:
        return {"models": [], "errorCode": ErrorCode.COM_ERROR, "error": str(e)}


def model_info(model_id: Optional[str] = None, include_statistics: bool = False) -> dict:
    """Gets information about the model.

    Args:
        model_id: Optional model identifier
        include_statistics: If True, includes simulation statistics from simulation_get_results
    """
    try:
        app = get_extendsim_app()

        # Get model name
        app.Execute("globalStr0 = GetModelName();")
        model_name = app.Request("System", "globalStr0+:0:0:0")

        # Get simulation times
        app.Execute("global0 = endTime;")
        end_time = parse_float(app.Request("System", "global0+:0:0:0"))

        app.Execute("global0 = currentTime;")
        current_time = parse_float(app.Request("System", "global0+:0:0:0"))

        # Get simulation phase
        app.Execute("globalInt0 = GetSimulationPhase();")
        sim_phase = int(app.Request("System", "globalInt0+:0:0:0") or 0)

        result = {
            "success": True,
            "modelId": model_id or "model_1",
            "name": model_name or "",
            "endTime": end_time,
            "currentTime": current_time,
            "simulationPhase": sim_phase,
            "isRunning": sim_phase > 0
        }

        # Include statistics if requested
        if include_statistics:
            stats = simulation_get_results(model_id)
            if stats.get("success"):
                result["exitStatistics"] = stats.get("exitStatistics", [])
                result["queueStatistics"] = stats.get("queueStatistics", [])
                result["activityStatistics"] = stats.get("activityStatistics", [])
                result["createStatistics"] = stats.get("createStatistics", [])
                result["summary"] = stats.get("summary", {})

        return result
    except Exception as e:
        return _com_error(e, "model_info")


def model_close(model_id: Optional[str] = None, save_first: bool = False) -> dict:
    """Closes the model."""
    try:
        app = get_extendsim_app()

        if save_first:
            app.Execute("SaveModel();")
        else:
            app.Execute("SetDirty(False);")

        # Close active window
        app.Execute("ExecuteMenuCommand(4)")

        return {"success": True, "wasSaved": save_first}
    except Exception as e:
        return _com_error(e, "model_close")


def model_new(save_path: Optional[str] = None) -> dict:
    """Creates a new blank model.

    Args:
        save_path: Optional path to save the new model to

    Returns:
        Dictionary with success status and model info
    """
    try:
        # Clear array slot tracking for new model
        _clear_array_slot_tracking()

        app = get_extendsim_app(create_if_missing=True)

        # File > New via menu command 2
        app.Execute("ExecuteMenuCommand(2)")

        # Get model name
        app.Execute("globalStr0 = GetModelName();")
        model_name = app.Request("System", "globalStr0+:0:0:0")

        # Save if path provided
        if save_path:
            save_path_normalized = save_path.replace("\\", "/")
            app.Execute(f'SaveModelAs("{save_path_normalized}")')
            # Re-get model name after save
            app.Execute("globalStr0 = GetModelName();")
            model_name = app.Request("System", "globalStr0+:0:0:0")

        return {
            "success": True,
            "modelId": "model_1",
            "name": model_name or "Untitled",
            "filePath": save_path or ""
        }
    except Exception as e:
        return _com_error(e, "model_new")


def block_add(library_name: str, block_name: str, x: int = 100, y: int = 100,
              neighbor: int = -1, side: int = 2,
              label: Optional[str] = None, model_id: Optional[str] = None) -> dict:
    """Adds a block to the model using PlaceBlock.

    PlaceBlock(blockName, libName, xPixel, yPixel, neighbor, side)
    - If neighbor=-1: x,y are absolute coordinates
    - If neighbor=blockId: x,y are relative to neighbor block
    - side: 0=left, 1=top, 2=right, 3=bottom
    """
    try:
        app = get_extendsim_app()
        model_check = _validate_model_open(app)
        if not model_check.get("success"):
            return model_check

        # Collect all block IDs BEFORE
        before_ids = set()
        current_id = -1
        while True:
            app.Execute(f"global0 = objectIDNext({current_id}, 0);")
            next_id = int(parse_float(app.Request("System", "global0+:0:0:0")))
            if next_id == -1:
                break
            before_ids.add(next_id)
            current_id = next_id

        # When using neighbor placement, calculate proper relative offset
        # to place blocks in a horizontal line (not diagonal)
        place_x = x
        place_y = y
        if neighbor != -1:
            # Default offsets for horizontal/vertical alignment
            HORIZONTAL_OFFSET = 120  # pixels between blocks horizontally
            VERTICAL_OFFSET = 80     # pixels between blocks vertically
            if side == 0:    # left
                place_x = -HORIZONTAL_OFFSET
                place_y = 0
            elif side == 1:  # top
                place_x = 0
                place_y = -VERTICAL_OFFSET
            elif side == 2:  # right
                place_x = HORIZONTAL_OFFSET
                place_y = 0
            elif side == 3:  # bottom
                place_x = 0
                place_y = VERTICAL_OFFSET

        cmd = f'PlaceBlock("{block_name}", "{library_name}", {place_x}, {place_y}, {neighbor}, {side});'
        app.Execute(cmd)

        # Collect all block IDs AFTER and find the new one
        block_id = -1
        current_id = -1
        while True:
            app.Execute(f"global0 = objectIDNext({current_id}, 0);")
            next_id = int(parse_float(app.Request("System", "global0+:0:0:0")))
            if next_id == -1:
                break
            if next_id not in before_ids:
                block_id = next_id
            current_id = next_id

        if block_id < 0:
            return _error(ErrorCode.BLOCK_ADD_FAILED,
                         f"Failed to place block '{block_name}' from '{library_name}' - "
                         f"block was not created. Check library and block names with block_search().",
                         blockName=block_name, library=library_name)

        if label:
            app.Execute(f'SetBlockLabel({block_id}, "{_escape_modl_string(label)}");')

        return {
            "success": True,
            "blockId": block_id,
            "blockName": block_name,
            "library": library_name,
            "label": label or "",
            "position": {"x": x, "y": y},
            "neighbor": neighbor,
            "side": side
        }
    except Exception as e:
        return _error(ErrorCode.BLOCK_ADD_FAILED, str(e),
                      blockName=block_name, library=library_name)


def block_add_batch(blocks: list, model_id: Optional[str] = None) -> dict:
    """Adds multiple blocks to the model in one call.

    Each block in the list is a dict with keys:
      libraryName, blockName, x, y, label (optional), neighbor (optional), side (optional)

    Returns list of results, one per block.
    """
    try:
        app = get_extendsim_app()
        model_check = _validate_model_open(app)
        if not model_check.get("success"):
            return model_check

        # Suppress redraw for performance (H13)
        app.Execute("SuppressWorksheetRedraw(1);")

        results = []
        for b in blocks:
            lib = b.get("libraryName", "Item.lbr")
            name = b.get("blockName", "")
            x = b.get("x", 100)
            y = b.get("y", 100)
            neighbor = b.get("neighbor", -1)
            side = b.get("side", 2)
            label = b.get("label", None)

            # Collect IDs before
            before_ids = set()
            current_id = -1
            while True:
                app.Execute(f"global0 = objectIDNext({current_id}, 0);")
                next_id = int(parse_float(app.Request("System", "global0+:0:0:0")))
                if next_id == -1:
                    break
                before_ids.add(next_id)
                current_id = next_id

            # Calculate placement coords
            place_x = x
            place_y = y
            if neighbor != -1:
                HORIZONTAL_OFFSET = 120
                VERTICAL_OFFSET = 80
                if side == 0:
                    place_x, place_y = -HORIZONTAL_OFFSET, 0
                elif side == 1:
                    place_x, place_y = 0, -VERTICAL_OFFSET
                elif side == 2:
                    place_x, place_y = HORIZONTAL_OFFSET, 0
                elif side == 3:
                    place_x, place_y = 0, VERTICAL_OFFSET

            cmd = f'PlaceBlock("{name}", "{lib}", {place_x}, {place_y}, {neighbor}, {side});'
            app.Execute(cmd)

            # Find new block ID
            block_id = -1
            current_id = -1
            while True:
                app.Execute(f"global0 = objectIDNext({current_id}, 0);")
                next_id = int(parse_float(app.Request("System", "global0+:0:0:0")))
                if next_id == -1:
                    break
                if next_id not in before_ids:
                    block_id = next_id
                current_id = next_id

            if block_id < 0:
                results.append({
                    "success": False,
                    "error": f"Failed to place '{name}' from '{lib}'",
                    "blockName": name, "library": lib
                })
                continue

            if label:
                app.Execute(f'SetBlockLabel({block_id}, "{_escape_modl_string(label)}");')

            results.append({
                "success": True,
                "blockId": block_id,
                "blockName": name,
                "library": lib,
                "label": label or "",
                "position": {"x": x, "y": y}
            })

        # Restore redraw (H13)
        app.Execute("SuppressWorksheetRedraw(0);")

        return {
            "success": True,
            "blocks": results,
            "count": len(results),
            "successCount": sum(1 for r in results if r.get("success"))
        }
    except Exception as e:
        try:
            app = get_extendsim_app()
            app.Execute("SuppressWorksheetRedraw(0);")
        except Exception:
            pass
        return _error(ErrorCode.BLOCK_ADD_FAILED, str(e))


def _is_array_connector(app, block_id, con_name, log=None):
    """Check if a connector is an array connector.

    Calls ConArrayGetNumCons - returns number of open connectors (>= 0) for array, -1 for non-array.
    """
    if log is None:
        log = lambda msg: None  # No-op if no log passed
    try:
        cmd = f'global0 = ConArrayGetNumCons({block_id}, "{con_name}");'
        log(f"[_is_array_connector] BEFORE Execute: {cmd}")
        app.Execute(cmd)
        log(f"[_is_array_connector] AFTER Execute - OK")
        req_result = app.Request("System", "global0+:0:0:0")
        log(f"[_is_array_connector] Request result: '{req_result}'")
        result = int(parse_float(req_result))
        log(f"[_is_array_connector] Parsed result: {result}, returning {result >= 0}")
        return result >= 0
    except Exception as e:
        log(f"[_is_array_connector] EXCEPTION: {type(e).__name__}: {e}")
        return False


def _get_array_info(app, block_id, con_name):
    """Get info about an array connector: number of open connectors."""
    try:
        # Use ConArrayGetNumCons to get number of open connectors
        app.Execute(f'global0 = ConArrayGetNumCons({block_id}, "{con_name}");')
        num_cons = int(parse_float(app.Request("System", "global0+:0:0:0")))
        if num_cons < 0:
            num_cons = 0
        return {"arraySize": num_cons}
    except Exception:
        return {"arraySize": 0}


def _log_debug(msg: str):
    """Append debug message to log file."""
    log_path = r"C:\Dev\CluadeCode\ES_Extractor\temp\connector_debug.log"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"{msg}\n")


def _get_block_connectors(app, block_id: int) -> list:
    """Get all connectors from a block using GetNumCons and GetConName.

    Returns list of dicts: [{"index": 0, "name": "ItemIn"}, {"index": 1, "name": "ItemsOut"}, ...]
    """
    _log_debug(f"=== _get_block_connectors START ===")
    _log_debug(f"  block_id={block_id}")

    # Get number of connectors
    app.Execute(f'global0 = GetNumCons({block_id});')
    num_cons = int(parse_float(app.Request("System", "global0+:0:0:0")))
    _log_debug(f"  num_cons={num_cons}")

    connectors = []
    for i in range(num_cons):
        # Get connector name
        name = app.Request("System", f'GetConName({block_id}, {i})')
        name = name.strip() if name else f"conn_{i}"
        connectors.append({"index": i, "name": name})
        _log_debug(f"  connector[{i}] = '{name}'")

    _log_debug(f"=== _get_block_connectors END ===")
    return connectors


def _find_connector_by_direction_dynamic(app, block_id: int, direction: str) -> dict:
    """Find a connector on a block by direction (in/out) by querying the block directly.

    Args:
        app: ExtendSim application object
        block_id: Block ID
        direction: 'in' or 'out'

    Returns:
        Dict with 'index' and 'name', or None if not found
    """
    _log_debug(f"=== _find_connector_by_direction_dynamic START ===")
    _log_debug(f"  block_id={block_id}, direction='{direction}'")

    connectors = _get_block_connectors(app, block_id)

    # Search for connector matching direction
    # Priority: "ItemIn"/"ItemOut"/"ItemsOut" first, then any with "In"/"Out"
    direction_lower = direction.lower()

    # First pass: look for Item-prefixed connectors
    for con in connectors:
        name_lower = con["name"].lower()
        if direction_lower == "out" and ("itemout" in name_lower or "itemsout" in name_lower):
            _log_debug(f"  Found Item output: {con}")
            return con
        if direction_lower == "in" and "itemin" in name_lower:
            _log_debug(f"  Found Item input: {con}")
            return con

    # Second pass: any connector with In/Out in name
    for con in connectors:
        name_lower = con["name"].lower()
        if direction_lower == "out" and "out" in name_lower and "in" not in name_lower:
            _log_debug(f"  Found output: {con}")
            return con
        if direction_lower == "in" and "in" in name_lower and "out" not in name_lower:
            _log_debug(f"  Found input: {con}")
            return con

    _log_debug(f"  No connector found for direction '{direction}'")
    _log_debug(f"=== _find_connector_by_direction_dynamic END ===")
    return None


def _get_array_connector_index(slot: int, base_con: int) -> int:
    """Calculate connector index for array slot.

    Array connector indexing scheme:
    - Slot 0 = base connector index (usually 1)
    - Slot 1 = 255
    - Slot 2 = 254
    - Slot N (N > 0) = 256 - N

    Formula: connector_index = base_con if slot == 0 else 256 - slot
    """
    if slot == 0:
        return base_con
    else:
        return 256 - slot


def _find_free_array_slot(app, block_id, con_name):
    """Find next available slot in array connector.

    Uses both NodeGetIDIndex AND session tracking (_used_array_slots) to determine
    if a slot is free. This handles cases where NodeGetIDIndex doesn't update
    immediately after MakeConnection.

    Strategy:
    1. Get base connector index with getConNumber
    2. Get number of slots with ConArrayGetNumCons
    3. Calculate connector index for each slot: base_con if slot==0 else 256-slot
    4. Check if connected using NodeGetIDIndex AND session tracking
    5. Return first free connector index, or expand array if all slots are used
    6. Mark the slot as used in session tracking
    """
    global _used_array_slots

    _log_debug(f"=== _find_free_array_slot START ===")
    _log_debug(f"  block_id={block_id}, con_name='{con_name}'")

    # Get or create session tracking for this block/connector
    tracking_key = (block_id, con_name)
    if tracking_key not in _used_array_slots:
        _used_array_slots[tracking_key] = set()
    used_slots = _used_array_slots[tracking_key]
    _log_debug(f"  Session tracking: already used slots = {used_slots}")

    # Step 1: Get base connector index
    cmd1 = f'global0 = getConNumber({block_id}, "{con_name}");'
    _log_debug(f"  CMD1: {cmd1}")
    app.Execute(cmd1)
    base_con = int(parse_float(app.Request("System", "global0+:0:0:0")))
    _log_debug(f"  base_con={base_con}")

    # Step 2: Get number of slots in array
    cmd2 = f'global0 = ConArrayGetNumCons({block_id}, "{con_name}");'
    _log_debug(f"  CMD2: {cmd2}")
    app.Execute(cmd2)
    num_slots = int(parse_float(app.Request("System", "global0+:0:0:0")))
    _log_debug(f"  num_slots={num_slots}")

    if num_slots < 1:
        num_slots = 1  # At least one slot (the base connector)
        _log_debug(f"  num_slots was < 1, set to 1")

    # Step 3 & 4: Find a free slot by checking NodeGetIDIndex AND session tracking
    free_con_idx = None
    free_slot = None
    for slot in range(num_slots):
        # Skip if already used in this session
        if slot in used_slots:
            _log_debug(f"  Slot {slot} already used in this session, skipping")
            continue

        con_idx = _get_array_connector_index(slot, base_con)
        cmd3 = f'global0 = NodeGetIDIndex({block_id}, {con_idx});'
        _log_debug(f"  Checking slot {slot} (connector {con_idx}): {cmd3}")
        app.Execute(cmd3)
        node_id = int(parse_float(app.Request("System", "global0+:0:0:0")))
        _log_debug(f"    NodeGetIDIndex returned {node_id}")

        if node_id == 0:
            # Found a free slot
            free_con_idx = con_idx
            free_slot = slot
            _log_debug(f"  Found FREE slot {slot} (connector index {con_idx})")
            break
        else:
            _log_debug(f"  Slot {slot} is CONNECTED (nodeId={node_id})")

    # Step 5: If no free slot found, expand the array
    if free_con_idx is None:
        _log_debug(f"  No free slot found, expanding array...")
        new_size = num_slots + 1
        cmd4 = f'ConArraySetNumCons({block_id}, "{con_name}", {new_size}, 0);'
        _log_debug(f"  CMD4: {cmd4}")
        app.Execute(cmd4)
        _log_debug(f"  Array expanded to {new_size} slots")

        # New slot is at index num_slots
        free_slot = num_slots
        free_con_idx = _get_array_connector_index(free_slot, base_con)
        _log_debug(f"  New slot {free_slot} has connector index {free_con_idx}")

    # Step 6: Mark slot as used in session tracking
    if free_slot is not None:
        used_slots.add(free_slot)
        _log_debug(f"  Marked slot {free_slot} as used. Updated tracking: {used_slots}")

    _log_debug(f"  Returning connector index: {free_con_idx}")
    _log_debug(f"=== _find_free_array_slot END ===")
    return free_con_idx


def _resolve_connector(app, block_id: int, connector, auto_expand_array: bool = True) -> int:
    """Resolves connector to index. Handles array connectors automatically.

    Accepts int (index) or str (name).
    If auto_expand_array=True and connector is an array connector,
    automatically finds a free slot (or expands if needed).

    If the connector name doesn't exist on the block, tries to find a matching
    connector by direction (in/out) based on the name pattern.
    """
    _log_debug(f"=== _resolve_connector START ===")
    _log_debug(f"  block_id={block_id}, connector='{connector}', auto_expand_array={auto_expand_array}")

    if isinstance(connector, int):
        _log_debug(f"  connector is int, returning {connector}")
        return connector

    # Connector is a name - first try exact match with getConNumber
    cmd = f'global0 = getConNumber({block_id}, "{connector}");'
    _log_debug(f"  CMD: {cmd}")
    app.Execute(cmd)
    base_con = int(parse_float(app.Request("System", "global0+:0:0:0")))
    _log_debug(f"  base_con={base_con}")

    # If connector not found (-1), try to find by direction dynamically
    actual_connector_name = connector
    if base_con < 0:
        _log_debug(f"  Connector '{connector}' not found, trying dynamic lookup...")
        # Determine direction from connector name
        connector_lower = connector.lower()
        if "out" in connector_lower:
            direction = "out"
        elif "in" in connector_lower:
            direction = "in"
        else:
            direction = None

        if direction:
            found = _find_connector_by_direction_dynamic(app, block_id, direction)
            if found:
                actual_connector_name = found["name"]
                base_con = found["index"]
                _log_debug(f"  Found connector dynamically: name='{actual_connector_name}', index={base_con}")
            else:
                _log_debug(f"  No connector found for direction '{direction}'")
        else:
            _log_debug(f"  Could not determine direction from connector name '{connector}'")

    if base_con < 0:
        _log_debug(f"  ERROR: connector '{connector}' not found on block {block_id}")
        raise ValueError(
            f"Connector '{connector}' not found on block {block_id}. "
            f"Use block_info(blockId={block_id}) to see available connectors."
        )

    is_array = _is_array_connector(app, block_id, actual_connector_name)
    _log_debug(f"  is_array_connector={is_array}")

    if auto_expand_array and is_array:
        # Array connector: find free slot (expand if needed)
        _log_debug(f"  Calling _find_free_array_slot with name='{actual_connector_name}'...")
        return _find_free_array_slot(app, block_id, actual_connector_name)

    _log_debug(f"  returning base_con={base_con}")
    return base_con


def block_connect(source_block_id: int, source_connector,
                  target_block_id: int, target_connector,
                  model_id: Optional[str] = None) -> dict:
    """Connects two blocks.

    source_connector/target_connector can be:
    - int: connector index (0-based)
    - str: connector name (e.g. "ItemOut", "ItemIn")
    """
    _log_debug(f"")
    _log_debug(f"########## block_connect START ##########")
    _log_debug(f"  source_block_id={source_block_id}, source_connector='{source_connector}'")
    _log_debug(f"  target_block_id={target_block_id}, target_connector='{target_connector}'")
    try:
        app = get_extendsim_app()
        _log_debug(f"  Got ExtendSim app")

        model_check = _validate_model_open(app)
        if not model_check.get("success"):
            return model_check

        _log_debug(f"  Resolving source connector...")
        from_con = _resolve_connector(app, source_block_id, source_connector)
        _log_debug(f"  from_con={from_con}")

        _log_debug(f"  Resolving target connector...")
        to_con = _resolve_connector(app, target_block_id, target_connector)
        _log_debug(f"  to_con={to_con}")

        cmd = f'global0 = MakeConnection({source_block_id}, {from_con}, {target_block_id}, {to_con});'
        _log_debug(f"  CMD: {cmd}")
        app.Execute(cmd)
        result = int(parse_float(app.Request("System", "global0+:0:0:0")))
        _log_debug(f"  MakeConnection result={result}")

        if result != 1:
            _log_debug(f"  MakeConnection FAILED (result={result})")
            _log_debug(f"########## block_connect END (with error) ##########")

            # Check for Queue → Workstation pattern (known limitation)
            suggestion = "Use block_info(blockId) to verify connector indices and directions."
            try:
                app.Execute(f"globalStr0 = BlockName({source_block_id});")
                src_name = app.Request("System", "globalStr0+:0:0:0")
                app.Execute(f"globalStr0 = BlockName({target_block_id});")
                tgt_name = app.Request("System", "globalStr0+:0:0:0")

                if src_name == "Queue" and tgt_name == "Workstation":
                    suggestion = ("Workstation blocks have a built-in internal queue. "
                                  "Connecting an external Queue to a Workstation's ItemIn fails "
                                  "because the internal queue already uses that connector. "
                                  "Solution: Use Workstation alone (it includes queue functionality) "
                                  "or use Queue + Activity instead of Queue + Workstation.")
                elif tgt_name == "Workstation" and to_con == 0:
                    suggestion = ("Workstation ItemIn (connector 0) may already be used by its "
                                  "internal queue. Try connecting to a different connector or "
                                  "use Queue + Activity instead.")
            except Exception:
                pass

            return _error(ErrorCode.CONNECTION_FAILED,
                         f"MakeConnection failed: block {source_block_id} connector {from_con} "
                         f"→ block {target_block_id} connector {to_con}.",
                         sourceBlockId=source_block_id, sourceConnector=from_con,
                         targetBlockId=target_block_id, targetConnector=to_con,
                         suggestion=suggestion)

        _log_debug(f"  MakeConnection executed successfully")
        _log_debug(f"########## block_connect END ##########")

        return {
            "success": True,
            "from": {"blockId": source_block_id, "connector": from_con,
                     "name": source_connector if isinstance(source_connector, str) else None},
            "to": {"blockId": target_block_id, "connector": to_con,
                   "name": target_connector if isinstance(target_connector, str) else None}
        }
    except Exception as e:
        _log_debug(f"  EXCEPTION: {e}")
        _log_debug(f"########## block_connect END (with error) ##########")
        return _error(ErrorCode.CONNECTION_FAILED, str(e),
                      sourceBlockId=source_block_id, targetBlockId=target_block_id,
                      suggestion="Use block_info(blockId) to check available connectors and their directions (in/out).")


def block_disconnect(source_block_id: int, source_connector,
                     target_block_id: int, target_connector,
                     model_id: Optional[str] = None) -> dict:
    """Disconnects two blocks.

    Uses ClearConnection(blockFrom, conFrom, blockTo, conTo).
    Returns TRUE (1) if successful.

    source_connector/target_connector can be:
    - int: connector index (0-based)
    - str: connector name (e.g. "ItemOut", "ItemIn")
    """
    try:
        app = get_extendsim_app()

        # Resolve connectors without auto-expanding arrays
        from_con = _resolve_connector(app, source_block_id, source_connector,
                                       auto_expand_array=False)
        to_con = _resolve_connector(app, target_block_id, target_connector,
                                     auto_expand_array=False)

        cmd = f'global0 = ClearConnection({source_block_id}, {from_con}, {target_block_id}, {to_con});'
        app.Execute(cmd)
        result = int(parse_float(app.Request("System", "global0+:0:0:0")))

        return {
            "success": result == 1,
            "from": {"blockId": source_block_id, "connector": from_con},
            "to": {"blockId": target_block_id, "connector": to_con}
        }
    except Exception as e:
        return _error(ErrorCode.CONNECTION_FAILED, str(e),
                      sourceBlockId=source_block_id, targetBlockId=target_block_id)


def _get_connector_direction(con_name: str) -> str:
    """Determines connector direction based on name.

    Returns 'in', 'out', or 'unknown'.
    """
    name_lower = con_name.lower()
    if "in" in name_lower and "out" not in name_lower:
        return "in"
    elif "out" in name_lower:
        return "out"
    return "unknown"


def _find_connector_by_direction(app, block_id: int, direction: str, preferred_name: str = None) -> dict:
    """Finds a connector on a block by direction (in/out).

    First tries the preferred_name if provided. If that doesn't exist or has wrong direction,
    scans all connectors to find one with the correct direction.

    Args:
        app: ExtendSim application object
        block_id: Block ID
        direction: 'in' or 'out'
        preferred_name: Preferred connector name to try first (e.g., 'ItemOut', 'ItemIn')

    Returns:
        Dict with 'index', 'name', 'direction'

    Raises:
        ValueError: If no connector with the specified direction is found
    """
    # First, try the preferred name if provided
    if preferred_name:
        try:
            app.Execute(f'global0 = getConNumber({block_id}, "{preferred_name}");')
            con_index = int(parse_float(app.Request("System", "global0+:0:0:0")))
            if con_index >= 0:
                con_dir = _get_connector_direction(preferred_name)
                if con_dir == direction:
                    return {"index": con_index, "name": preferred_name, "direction": con_dir}
        except Exception:
            pass

    # Preferred name didn't work - scan all connectors
    app.Execute(f'global0 = GetNumCons({block_id});')
    num_connectors = int(parse_float(app.Request("System", "global0+:0:0:0")))

    for con_idx in range(num_connectors):
        try:
            app.Execute(f'globalStr0 = getConName({block_id}, {con_idx});')
            con_name = app.Request("System", "globalStr0+:0:0:0") or ""
            con_dir = _get_connector_direction(con_name)

            if con_dir == direction:
                return {"index": con_idx, "name": con_name, "direction": con_dir}
        except Exception:
            continue

    raise ValueError(f"No {direction.upper()} connector found on block {block_id}")


def connect_chain(block_ids: list,
                  source_connector: str = "ItemOut",
                  target_connector: str = "ItemIn",
                  model_id: Optional[str] = None) -> dict:
    """Connects blocks in sequence: block_ids[0] -> block_ids[1] -> ... -> block_ids[n].

    Automatically finds the correct OUTPUT connector on each source block and
    INPUT connector on each target block. The source_connector and target_connector
    parameters are used as preferred names but the function will find alternatives
    if they don't exist or have the wrong direction.

    Args:
        block_ids: List of block IDs to connect in order
        source_connector: Preferred output connector name (default "ItemOut")
        target_connector: Preferred input connector name (default "ItemIn")
        model_id: Optional model ID

    Returns:
        Dictionary with success status and list of connections made
    """
    if len(block_ids) < 2:
        return {"success": False, "error": "Need at least 2 block IDs to connect"}

    try:
        app = get_extendsim_app()
        connections = []
        errors = []

        for i in range(len(block_ids) - 1):
            src_id = block_ids[i]
            tgt_id = block_ids[i + 1]

            try:
                # Find OUTPUT connector on source block
                src_info = _find_connector_by_direction(app, src_id, "out", source_connector)

                # Find INPUT connector on target block
                tgt_info = _find_connector_by_direction(app, tgt_id, "in", target_connector)

                from_con = src_info["index"]
                to_con = tgt_info["index"]

                cmd = f'global0 = MakeConnection({src_id}, {from_con}, {tgt_id}, {to_con});'
                app.Execute(cmd)
                conn_result = int(parse_float(app.Request("System", "global0+:0:0:0")))

                if conn_result != 1:
                    err_msg = f"MakeConnection failed (result={conn_result})"
                    # Detect Queue → Workstation limitation
                    try:
                        app.Execute(f"globalStr0 = BlockName({src_id});")
                        sn = app.Request("System", "globalStr0+:0:0:0")
                        app.Execute(f"globalStr0 = BlockName({tgt_id});")
                        tn = app.Request("System", "globalStr0+:0:0:0")
                        if sn == "Queue" and tn == "Workstation":
                            err_msg += ". Workstation has built-in queue; use Workstation alone or Queue+Activity instead."
                    except Exception:
                        pass
                    errors.append({"from": src_id, "to": tgt_id, "error": err_msg})
                else:
                    connections.append({
                        "from": {"blockId": src_id, "connector": from_con, "name": src_info["name"]},
                        "to": {"blockId": tgt_id, "connector": to_con, "name": tgt_info["name"]}
                    })
            except Exception as e:
                errors.append({
                    "from": src_id,
                    "to": tgt_id,
                    "error": str(e)
                })

        result = {
            "success": len(errors) == 0,
            "connections": connections,
            "connectionCount": len(connections)
        }

        if errors:
            result["errors"] = errors

        return result
    except Exception as e:
        return _com_error(e, "connect_chain")


def connect_graph(connections: list, model_id: Optional[str] = None) -> dict:
    """Connects multiple arbitrary block pairs in one call.

    Each connection is a dict with:
        sourceBlockId (int): Source block ID
        targetBlockId (int): Target block ID
        sourceConnector (str|int, optional): Output connector name or index (default "ItemOut")
        targetConnector (str|int, optional): Input connector name or index (default "ItemIn")

    Returns:
        Dictionary with success status and list of connections made/errors
    """
    if not connections or len(connections) == 0:
        return _error(ErrorCode.MISSING_PARAMETER, "connections array is required and must not be empty")

    try:
        app = get_extendsim_app()
        model_check = _validate_model_open(app)
        if not model_check.get("success"):
            return model_check

        # Suppress redraw for performance (H13)
        app.Execute("SuppressWorksheetRedraw(1);")

        made = []
        errors = []

        for idx, conn in enumerate(connections):
            src_id = conn.get("sourceBlockId")
            tgt_id = conn.get("targetBlockId")
            src_con = conn.get("sourceConnector", "ItemOut")
            tgt_con = conn.get("targetConnector", "ItemIn")

            if src_id is None or tgt_id is None:
                errors.append({"index": idx, "error": "sourceBlockId and targetBlockId are required"})
                continue

            try:
                from_con = _resolve_connector(app, src_id, src_con)
                to_con = _resolve_connector(app, tgt_id, tgt_con)

                cmd = f'global0 = MakeConnection({src_id}, {from_con}, {tgt_id}, {to_con});'
                app.Execute(cmd)
                result = int(parse_float(app.Request("System", "global0+:0:0:0")))

                if result != 1:
                    errors.append({
                        "index": idx,
                        "from": src_id, "to": tgt_id,
                        "error": f"MakeConnection failed (result={result})"
                    })
                else:
                    made.append({
                        "from": {"blockId": src_id, "connector": from_con,
                                 "name": src_con if isinstance(src_con, str) else None},
                        "to": {"blockId": tgt_id, "connector": to_con,
                                "name": tgt_con if isinstance(tgt_con, str) else None}
                    })
            except Exception as e:
                errors.append({"index": idx, "from": src_id, "to": tgt_id, "error": str(e)})

        # Restore redraw (H13)
        app.Execute("SuppressWorksheetRedraw(0);")

        result = {
            "success": len(errors) == 0,
            "connections": made,
            "connectionCount": len(made)
        }
        if errors:
            result["errors"] = errors
        return result
    except Exception as e:
        try:
            app = get_extendsim_app()
            app.Execute("SuppressWorksheetRedraw(0);")
        except Exception:
            pass
        return _com_error(e, "connect_graph")


def block_remove(block_id: int, allow_undo: bool = False,
                 model_id: Optional[str] = None) -> dict:
    """Removes a block.

    - allow_undo=False: ClearBlock (permanent)
    - allow_undo=True: ClearBlockUndo (can be undone with undo)
    """
    try:
        app = get_extendsim_app()

        # Protect Executive block (block 0) - required for discrete event simulation
        if block_id == 0:
            return _error(ErrorCode.BLOCK_REMOVE_FAILED,
                          "Cannot remove the Executive block (block 0). It is required for discrete event simulation.",
                          blockId=block_id,
                          suggestion="The Executive block must exist in all discrete event models.")

        # Verify block exists by getting its name
        app.Execute(f"globalStr0 = BlockName({block_id});")
        block_name = app.Request("System", "globalStr0+:0:0:0") or ""

        if not block_name:
            return _error(ErrorCode.BLOCK_NOT_FOUND,
                          f"Block {block_id} does not exist or has no type name.",
                          blockId=block_id,
                          suggestion="Use block_list to see valid block IDs.")

        if allow_undo:
            app.Execute(f"ClearBlockUndo({block_id});")
        else:
            app.Execute(f"ClearBlock({block_id});")

        return {
            "success": True,
            "blockId": block_id,
            "blockName": block_name,
            "undoable": allow_undo
        }
    except Exception as e:
        return _error(ErrorCode.BLOCK_REMOVE_FAILED, str(e), blockId=block_id)


def block_list(model_id: Optional[str] = None, detail: str = "summary") -> dict:
    """Lists all blocks in the model.

    Args:
        model_id: Model ID (optional)
        detail: "summary" (default) or "full"
            - summary: Only blockId, blockName, blockType, library, label
            - full: Includes connectors (use connection_list for connections)
    """
    try:
        app = get_extendsim_app()
        blocks = []

        # Start with -1 to get first block
        current_id = -1

        while True:
            # Get next block ID
            app.Execute(f"global0 = objectIDNext({current_id}, 0);")
            next_id = int(parse_float(app.Request("System", "global0+:0:0:0")))

            if next_id == -1:
                # No more blocks
                break

            # Get block info
            block_info_data = {"blockId": next_id}

            # Get label with getBlockLabel()
            try:
                app.Execute(f"globalStr0 = getBlockLabel({next_id});")
                label = app.Request("System", "globalStr0+:0:0:0")
                block_info_data["label"] = label if label else ""
            except Exception:
                block_info_data["label"] = ""

            # Get block type (category) with GetBlockType()
            try:
                app.Execute(f"globalStr0 = GetBlockType({next_id});")
                block_type = app.Request("System", "globalStr0+:0:0:0")
                block_info_data["blockType"] = block_type if block_type else ""
            except Exception:
                block_info_data["blockType"] = ""

            # Get specific block name with BlockName()
            try:
                app.Execute(f"globalStr0 = BlockName({next_id});")
                block_name = app.Request("System", "globalStr0+:0:0:0")
                block_info_data["blockName"] = block_name if block_name else ""
            except Exception:
                block_info_data["blockName"] = ""

            # Get library with GetLibraryPathName()
            try:
                app.Execute(f"globalStr0 = GetLibraryPathName({next_id}, 2);")
                library = app.Request("System", "globalStr0+:0:0:0")
                block_info_data["library"] = library if library else ""
            except Exception:
                block_info_data["library"] = ""

            # Get connectors only if detail="full"
            if detail == "full":
                connectors = []
                try:
                    app.Execute(f"global0 = GetNumCons({next_id});")
                    num_cons = int(parse_float(app.Request("System", "global0+:0:0:0")))

                    for conn_idx in range(num_cons):
                        app.Execute(f"global0 = NodeGetIDIndex({next_id}, {conn_idx});")
                        node_index = int(parse_float(app.Request("System", "global0+:0:0:0")))

                        # Get connector name to determine in/out
                        try:
                            app.Execute(f'globalStr0 = GetConName({next_id}, {conn_idx});')
                            con_name = app.Request("System", "globalStr0+:0:0:0") or ""
                        except Exception:
                            con_name = ""

                        # Determine direction based on name
                        direction = "unknown"
                        if "in" in con_name.lower():
                            direction = "in"
                        elif "out" in con_name.lower():
                            direction = "out"

                        connector_entry = {
                            "connectorIndex": conn_idx,
                            "nodeIndex": node_index,
                            "name": con_name,
                            "direction": direction
                        }

                        connectors.append(connector_entry)
                except Exception:
                    pass
                block_info_data["connectors"] = connectors

            blocks.append(block_info_data)
            current_id = next_id

        return {"blocks": blocks, "count": len(blocks)}
    except Exception as e:
        return {"blocks": [], "errorCode": ErrorCode.COM_ERROR, "error": str(e)}


def connection_list(model_id: Optional[str] = None) -> dict:
    """Lists all connections in the model.

    Returns connections based on matching nodeIndex between block connectors.
    """
    try:
        app = get_extendsim_app()

        # Collect all connectors with their nodeIndex
        node_map = {}  # nodeIndex -> [(blockId, connectorIndex, direction, connectorName)]
        current_id = -1

        while True:
            app.Execute(f"global0 = objectIDNext({current_id}, 0);")
            next_id = int(parse_float(app.Request("System", "global0+:0:0:0")))

            if next_id == -1:
                break

            try:
                app.Execute(f"global0 = GetNumCons({next_id});")
                num_cons = int(parse_float(app.Request("System", "global0+:0:0:0")))

                for conn_idx in range(num_cons):
                    # Get connector name (needed for array detection and direction)
                    try:
                        app.Execute(f'globalStr0 = GetConName({next_id}, {conn_idx});')
                        con_name = app.Request("System", "globalStr0+:0:0:0") or ""
                    except Exception:
                        con_name = ""

                    # Array connectors expose additional connections on extra slots
                    # (slot N>0 lives at connector index 256-N). The base loop only
                    # visits range(num_cons), so without this a 2nd+ connection into
                    # an array input (e.g. Queue ItemIn) would be silently dropped.
                    slot_con_indices = [conn_idx]
                    if con_name:
                        try:
                            app.Execute(f'global0 = ConArrayGetNumCons({next_id}, "{con_name}");')
                            num_slots = int(parse_float(app.Request("System", "global0+:0:0:0")))
                        except Exception:
                            num_slots = -1
                        if num_slots > 1:
                            slot_con_indices += [_get_array_connector_index(s, conn_idx)
                                                 for s in range(1, num_slots)]

                    # Determine direction (same for every slot of this connector)
                    direction = "unknown"
                    if "in" in con_name.lower():
                        direction = "in"
                    elif "out" in con_name.lower():
                        direction = "out"

                    for slot_con_idx in slot_con_indices:
                        app.Execute(f"global0 = NodeGetIDIndex({next_id}, {slot_con_idx});")
                        node_index = int(parse_float(app.Request("System", "global0+:0:0:0")))

                        # Skip unconnected (nodeIndex = 0)
                        if node_index == 0:
                            continue

                        if node_index not in node_map:
                            node_map[node_index] = []
                        node_map[node_index].append((next_id, slot_con_idx, direction, con_name))
            except Exception:
                pass

            current_id = next_id

        # Build connections from node_map
        connections = []
        dangling = []
        for ni, endpoints in node_map.items():
            if len(endpoints) == 2:
                ep0, ep1 = endpoints[0], endpoints[1]
                # Set "out" as from and "in" as to
                if ep0[2] == "in" and ep1[2] == "out":
                    ep0, ep1 = ep1, ep0
                connections.append({
                    "nodeIndex": ni,
                    "from": {"blockId": ep0[0], "connector": ep0[1], "name": ep0[3]},
                    "to": {"blockId": ep1[0], "connector": ep1[1], "name": ep1[3]}
                })
            elif len(endpoints) > 2:
                # Multiple endpoints on same node (e.g. shared node)
                connections.append({
                    "nodeIndex": ni,
                    "type": "shared",
                    "endpoints": [{"blockId": ep[0], "connector": ep[1], "name": ep[3], "direction": ep[2]} for ep in endpoints]
                })
            else:
                # Single endpoint: a node is wired here but its other end is not
                # enumerable at this level (e.g. a line into a hierarchical block).
                # Surface it instead of dropping it silently.
                ep = endpoints[0]
                dangling.append({
                    "nodeIndex": ni,
                    "endpoint": {"blockId": ep[0], "connector": ep[1], "name": ep[3], "direction": ep[2]}
                })

        result = {"connections": connections, "count": len(connections)}
        if dangling:
            result["danglingNodes"] = dangling
        return result
    except Exception as e:
        return {"connections": [], "errorCode": ErrorCode.COM_ERROR, "error": str(e)}


def block_info(query: Optional[str] = None, block_id: Optional[int] = None,
               model_id: Optional[str] = None) -> dict:
    """Gets block information.

    Two modes:
    - query="all": Lists all available block types from reference
    - query="Activity": Gets description for a specific block
    - block_id=47: Gets live info about a block in the model
    """
    # Mode 1: Reference query (all or block name)
    if query is not None:
        ref = _load_block_reference()
        libraries = ref.get("libraries", {})

        if query.lower() == "all":
            # List all available blocks per library
            result = {"success": True, "mode": "reference", "libraries": []}
            for lib_key, lib_data in libraries.items():
                lib_entry = {
                    "library": lib_data.get("name", lib_key),
                    "fileName": lib_key,
                    "categories": []
                }
                for cat_name, cat_data in lib_data.get("categories", {}).items():
                    blocks = list(cat_data.get("blocks", {}).keys())
                    lib_entry["categories"].append({
                        "category": cat_name,
                        "blocks": blocks
                    })
                result["libraries"].append(lib_entry)
            return result
        else:
            # Search for a specific block by name
            query_lower = query.lower()
            matches = []
            for lib_key, lib_data in libraries.items():
                for cat_name, cat_data in lib_data.get("categories", {}).items():
                    for block_name, block_data in cat_data.get("blocks", {}).items():
                        if block_name.lower() == query_lower:
                            matches.append(_build_block_match(
                                block_name, block_data, lib_data, lib_key, cat_name))
            if matches:
                return {"success": True, "mode": "reference", "results": matches}
            else:
                # Fuzzy search: block name containing query
                for lib_key, lib_data in libraries.items():
                    for cat_name, cat_data in lib_data.get("categories", {}).items():
                        for block_name, block_data in cat_data.get("blocks", {}).items():
                            if query_lower in block_name.lower():
                                matches.append(_build_block_match(
                                    block_name, block_data, lib_data, lib_key, cat_name))
                if matches:
                    return {"success": True, "mode": "reference", "partial_matches": matches}
                return _error(ErrorCode.BLOCK_NOT_FOUND, f"No block found matching '{query}'",
                             suggestion="Use block_search(query) to find valid block names, or block_list() to see blocks in the model.")

    # Mode 2: Live query on a block in the model
    if block_id is not None:
        try:
            app = get_extendsim_app()
            result = {"blockId": block_id, "success": True, "mode": "model"}

            # Label
            try:
                app.Execute(f"globalStr0 = getBlockLabel({block_id});")
                result["label"] = app.Request("System", "globalStr0+:0:0:0") or ""
            except Exception:
                result["label"] = ""

            # Block type (category)
            try:
                app.Execute(f"globalStr0 = GetBlockType({block_id});")
                result["blockType"] = app.Request("System", "globalStr0+:0:0:0") or ""
            except Exception:
                result["blockType"] = ""

            # Specific block name
            try:
                app.Execute(f"globalStr0 = BlockName({block_id});")
                block_name = app.Request("System", "globalStr0+:0:0:0") or ""
                result["blockName"] = block_name
            except Exception:
                block_name = ""
                result["blockName"] = ""

            # Library
            try:
                app.Execute(f"globalStr0 = GetLibraryPathName({block_id}, 2);")
                result["library"] = app.Request("System", "globalStr0+:0:0:0") or ""
            except Exception:
                result["library"] = ""

            # Connectors
            connectors = []
            try:
                # H10 fix: GetNumCons and NodeGetIDIndex return numbers → use global0
                app.Execute(f"global0 = GetNumCons({block_id});")
                num_cons = int(parse_float(app.Request("System", "global0+:0:0:0")))

                for conn_idx in range(num_cons):
                    app.Execute(f"global0 = NodeGetIDIndex({block_id}, {conn_idx});")
                    node_index = int(parse_float(app.Request("System", "global0+:0:0:0")))

                    try:
                        app.Execute(f'globalStr0 = GetConName({block_id}, {conn_idx});')
                        con_name = app.Request("System", "globalStr0+:0:0:0") or ""
                    except Exception:
                        con_name = ""

                    direction = "unknown"
                    if "in" in con_name.lower():
                        direction = "in"
                    elif "out" in con_name.lower():
                        direction = "out"

                    connector_entry = {
                        "connectorIndex": conn_idx,
                        "nodeIndex": node_index,
                        "name": con_name,
                        "direction": direction
                    }

                    connectors.append(connector_entry)
            except Exception:
                pass
            result["connectors"] = connectors

            # Get description and guide data from reference if possible
            if block_name:
                ref = _load_block_reference()
                for lib_key, lib_data in ref.get("libraries", {}).items():
                    for cat_name, cat_data in lib_data.get("categories", {}).items():
                        for ref_name, block_data in cat_data.get("blocks", {}).items():
                            if ref_name.lower() == block_name.lower():
                                result["description"] = _get_block_description(block_data)
                                # Include guide data if available
                                if isinstance(block_data, dict):
                                    guide = {}
                                    for key in ("connectors", "modes", "usage", "patterns", "notes"):
                                        if key in block_data:
                                            guide[key] = block_data[key]
                                    if guide:
                                        result["guide"] = guide
                                break

            return result
        except Exception as e:
            return _com_error(e, "block_info")

    return _error(ErrorCode.INVALID_PARAMETER, "Either 'query' or 'blockId' must be provided")


def block_discover(library_name: str, block_name: str, model_id: Optional[str] = None) -> dict:
    """Places a block temporarily, reads all connectors (including array info), removes it.

    Returns structured connector data that can be used for block_reference.json.
    """
    log_path = r"C:\Dev\CluadeCode\ES_Extractor\temp\block_discover_debug.log"
    step = 0

    def log(msg):
        nonlocal step
        step += 1
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{step}] {msg}\n")

    log(f"START block_discover('{library_name}', '{block_name}', model_id={model_id})")

    try:
        app = get_extendsim_app()
        log(f"get_extendsim_app() returned: type={type(app).__name__}, app={app}")

        if app is None:
            log("ERROR: app is None - ExtendSim is not running")
            return _error(ErrorCode.EXTENDSIM_NOT_RUNNING, "ExtendSim is not running")

        # Place block temporarily (outside visible area)
        log("Calling block_add()")
        add_result = block_add(library_name, block_name, x=2000, y=2000)
        log(f"block_add() returned: {add_result}")

        if not add_result.get("success"):
            log(f"ERROR: block_add failed: {add_result.get('error', 'unknown')}")
            return _error(ErrorCode.BLOCK_ADD_FAILED,
                         f"Could not place block: {add_result.get('error', 'unknown')}")

        temp_block_id = add_result["blockId"]
        log(f"temp_block_id = {temp_block_id}")

        # Verify block
        log(f"Executing: globalStr0 = BlockName({temp_block_id})")
        app.Execute(f"globalStr0 = BlockName({temp_block_id});")
        verify_name = app.Request("System", "globalStr0+:0:0:0")
        log(f"BlockName result: '{verify_name}'")

        # Read all connectors
        connectors = []
        try:
            log(f"Executing: globalStr0 = GetNumCons({temp_block_id})")
            app.Execute(f"globalStr0 = GetNumCons({temp_block_id});")
            num_cons_raw = app.Request("System", "globalStr0+:0:0:0")
            log(f"GetNumCons raw result: '{num_cons_raw}'")
            num_cons = int(parse_float(num_cons_raw))
            log(f"GetNumCons parsed: {num_cons}")

            seen_names = set()

            for conn_idx in range(num_cons):
                log(f"--- Connector {conn_idx} ---")

                # Get connector name
                try:
                    cmd = f'globalStr0 = GetConName({temp_block_id}, {conn_idx});'
                    log(f"Executing: {cmd}")
                    app.Execute(cmd)
                    log(f"Execute done for conn {conn_idx}")

                    con_name_raw = app.Request("System", "globalStr0+:0:0:0")
                    log(f"Request raw result: '{con_name_raw}' (type={type(con_name_raw).__name__})")
                    con_name = con_name_raw or ""
                    log(f"con_name = '{con_name}'")
                except Exception as e:
                    con_name = ""
                    log(f"EXCEPTION getting connector name: {type(e).__name__}: {e}")

                # Direction
                direction = "unknown"
                if con_name:
                    if "in" in con_name.lower():
                        direction = "in"
                    elif "out" in con_name.lower():
                        direction = "out"
                log(f"direction = '{direction}'")

                # Array connector?
                is_array = False
                array_size = 0
                if con_name and con_name not in seen_names:
                    log(f"Checking if array connector: '{con_name}'")
                    is_array = _is_array_connector(app, temp_block_id, con_name, log)
                    log(f"is_array = {is_array}")
                    if is_array:
                        array_info = _get_array_info(app, temp_block_id, con_name)
                        array_size = array_info["arraySize"]
                        log(f"array_size = {array_size}")

                # Get connector type (H6 enhancement)
                con_type_num = -999
                con_type_name = "unknown"
                try:
                    app.Execute(f"globalInt0 = GetConnectorType({temp_block_id}, {conn_idx});")
                    con_type_num = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))
                    con_type_map = {13: "Value", 14: "Item", 15: "Universal", 25: "Flow", 308: "Reliability", -1: "Array"}
                    con_type_name = con_type_map.get(con_type_num, f"unknown({con_type_num})")
                    log(f"connector type: {con_type_num} -> {con_type_name}")
                except Exception as ct_err:
                    log(f"GetConnectorType failed: {ct_err}")

                connector_entry = {
                    "name": con_name,
                    "direction": direction,
                    "connectorIndex": conn_idx,
                    "connectorType": con_type_name,
                    "connectorTypeId": con_type_num,
                    "isArray": is_array
                }
                if is_array:
                    connector_entry["defaultArraySize"] = array_size

                if con_name not in seen_names:
                    connectors.append(connector_entry)
                    if con_name:
                        seen_names.add(con_name)
                    log(f"Added connector entry: {connector_entry}")
                else:
                    log(f"Skipped duplicate: '{con_name}'")

        except Exception as e:
            log(f"OUTER EXCEPTION: {type(e).__name__}: {e}")
            connectors = [{"error": str(e)}]

        # Remove temporary block
        log(f"Removing temp block {temp_block_id}")
        block_remove(temp_block_id, allow_undo=False)
        log("block_remove done")

        result = {
            "success": True,
            "blockName": block_name,
            "library": library_name,
            "connectors": connectors,
            "totalConnectors": len(connectors)
        }
        log(f"DONE - returning {len(connectors)} connectors")
        return result

    except Exception as e:
        log(f"FATAL EXCEPTION: {type(e).__name__}: {e}")
        return _com_error(e, "block_discover")


# Dialog item type constants
DIALOG_ITEM_TYPES = {
    1: "button",
    2: "checkbox",
    3: "radiobutton",
    4: "meter",
    5: "parameter",
    6: "slider",
    7: "datatable",
    8: "edittext",
    9: "statictext",
    12: "switch",
    13: "stringtable",
    16: "popupmenu",
    18: "dynamictext",
    19: "textframe",
    20: "calendar",
    21: "edittext31"
}


def block_discover_variables(block_id: Optional[int] = None,
                              library_name: Optional[str] = None,
                              block_name: Optional[str] = None,
                              max_dialog_id: int = 200,
                              model_id: Optional[str] = None) -> dict:
    """Discovers all dialog variables on a block.

    Can either use an existing block (block_id) or temporarily place a new block
    (library_name + block_name) to discover its variables.

    Uses DIGetName to enumerate variables and GetDialogItemInfo to get properties.

    Args:
        block_id: Existing block ID in the model
        library_name: Library name (e.g. 'Item.lbr') - used with block_name
        block_name: Block type (e.g. 'Activity') - used with library_name
        max_dialog_id: Maximum dialogID to iterate through (default 200)

    Returns:
        Dictionary with variables categorized by type and read-only status
    """
    try:
        app = get_extendsim_app()

        if app is None:
            return _error(ErrorCode.EXTENDSIM_NOT_RUNNING, "ExtendSim is not running")

        temp_block = False
        target_block_id = block_id

        # If no block_id provided, place a temporary block
        if target_block_id is None:
            if not library_name or not block_name:
                return _error(ErrorCode.INVALID_PARAMETER,
                             "Either block_id or (library_name + block_name) required")

            add_result = block_add(library_name, block_name, x=2000, y=2000)
            if not add_result.get("success"):
                return _error(ErrorCode.BLOCK_ADD_FAILED,
                             f"Could not place block: {add_result.get('error')}")

            target_block_id = add_result["blockId"]
            temp_block = True

        variables = []
        seen_names = set()
        unused_count = 0

        # Iterate through dialogIDs
        for dialog_id in range(max_dialog_id):
            try:
                # Get variable name using DIGetName
                app.Execute(f"globalStr0 = DIGetName({target_block_id}, {dialog_id});")
                var_name = app.Request("System", "globalStr0+:0:0:0") or ""

                # Skip empty, "Unused", or already seen names
                if not var_name or var_name == "Unused":
                    unused_count += 1
                    # Stop if we've seen too many unused in a row
                    if unused_count > 20:
                        break
                    continue

                unused_count = 0  # Reset counter

                if var_name in seen_names:
                    continue
                seen_names.add(var_name)

                # Get dialog item type (which=4)
                app.Execute(f'global0 = GetDialogItemInfo({target_block_id}, "{var_name}", 4);')
                item_type = int(parse_float(app.Request("System", "global0+:0:0:0")))

                # Get display only / read-only status (which=3)
                app.Execute(f'global0 = GetDialogItemInfo({target_block_id}, "{var_name}", 3);')
                read_only = int(parse_float(app.Request("System", "global0+:0:0:0"))) == 1

                # Get enabled status (which=2)
                app.Execute(f'global0 = GetDialogItemInfo({target_block_id}, "{var_name}", 2);')
                enabled = int(parse_float(app.Request("System", "global0+:0:0:0"))) == 1

                # Get current value for parameters and editable text
                value = None
                if item_type in (5, 8, 21):  # parameter, edittext, edittext31
                    try:
                        app.Execute(f'globalStr0 = GetDialogVariable({target_block_id}, "{var_name}", 0, 0);')
                        value = app.Request("System", "globalStr0+:0:0:0")
                    except Exception:
                        pass

                type_name = DIALOG_ITEM_TYPES.get(item_type, f"unknown({item_type})")

                var_info = {
                    "dialogId": dialog_id,
                    "name": var_name,
                    "type": type_name,
                    "typeCode": item_type,
                    "readOnly": read_only,
                    "enabled": enabled
                }

                if value is not None:
                    var_info["value"] = value

                variables.append(var_info)

            except Exception as e:
                # Skip errors for individual variables
                continue

        # Remove temporary block
        if temp_block:
            block_remove(target_block_id, allow_undo=False)

        # Categorize variables
        inputs = [v for v in variables if not v["readOnly"] and v["typeCode"] in (5, 6, 8, 21, 2, 12, 16)]
        outputs = [v for v in variables if v["readOnly"] and v["typeCode"] in (5, 4, 8, 21)]
        other = [v for v in variables if v not in inputs and v not in outputs]

        return {
            "success": True,
            "blockId": target_block_id,
            "wasTemporary": temp_block,
            "totalVariables": len(variables),
            "inputs": inputs,
            "outputs": outputs,
            "other": other,
            "allVariables": variables
        }

    except Exception as e:
        return _com_error(e, "block_discover_variables")


def simulation_run(model_id: Optional[str] = None, end_time: Optional[float] = None,
                   run_mode: str = "normal", reset_first: bool = True,
                   wait_for_completion: bool = True,
                   include_stats: bool = False,
                   stats_block_ids: Optional[list] = None) -> dict:
    """Runs the simulation. Optionally collects results inline.

    Args:
        wait_for_completion: If True (default), blocks until simulation completes.
            If False, starts simulation in a background thread and returns immediately.
            Use simulation_status to poll progress and simulation_get_results for results.
    """
    try:
        app = get_extendsim_app()

        if end_time is not None:
            # endTime = X does NOT set the run end time (stays at the model default);
            # SetRunParameter is the effective API (see test_distribution_roundtrip.py).
            app.Execute(f"SetRunParameter({end_time}, 1);")

        # For fast mode, turn off 2D animation (toggle command 2020)
        # NOTE: This is a toggle, so we need to be careful
        if run_mode == "fast":
            # Faster animation via ExecuteMenuCommand(30005)
            app.Execute("ExecuteMenuCommand(30005)")

        if not wait_for_completion:
            # Fire-and-forget mode: run simulation in background thread
            # ExecuteMenuCommand(6000) blocks the calling thread, so we need
            # a separate thread with its own COM connection.
            import threading
            import pythoncom

            # Read endTime before starting thread (for response)
            app.Execute("global0 = endTime;")
            planned_end_time = parse_float(app.Request("System", "global0+:0:0:0"))

            def _run_sim_background():
                pythoncom.CoInitialize()
                try:
                    bg_app = win32com.client.GetActiveObject("ExtendSim.Application")
                    bg_app.Execute("ExecuteMenuCommand(6000)")
                except Exception:
                    pass  # Errors caught via simulation_status polling
                finally:
                    pythoncom.CoUninitialize()

            thread = threading.Thread(target=_run_sim_background, daemon=True)
            thread.start()

            return {
                "success": True,
                "status": "started",
                "endTime": planned_end_time,
                "message": "Simulation started in background. Use simulation_status to poll progress, "
                           "then simulation_get_results to collect results."
            }

        # Blocking mode (default): run synchronously
        app.Execute("ExecuteMenuCommand(6000)")

        # Get results
        app.Execute("global0 = currentTime;")
        current_time = parse_float(app.Request("System", "global0+:0:0:0"))

        app.Execute("global0 = endTime;")
        actual_end_time = parse_float(app.Request("System", "global0+:0:0:0"))

        result = {
            "success": True,
            "currentTime": current_time,
            "endTime": actual_end_time,
            "status": "completed"
        }

        if include_stats:
            stats = simulation_get_results(model_id, block_ids=stats_block_ids)
            if stats.get("success"):
                result["statistics"] = {
                    "exitStatistics": stats.get("exitStatistics", []),
                    "queueStatistics": stats.get("queueStatistics", []),
                    "activityStatistics": stats.get("activityStatistics", []),
                    "createStatistics": stats.get("createStatistics", []),
                    "summary": stats.get("summary", {})
                }

        return result
    except Exception as e:
        return _error(ErrorCode.SIMULATION_RUN_FAILED, str(e))


def simulation_stop(model_id: Optional[str] = None) -> dict:
    """Stops the simulation by setting endTime = currentTime + 5."""
    try:
        app = get_extendsim_app()

        # Save original endTime
        app.Execute("global0 = endTime;")
        original_end_time = parse_float(app.Request("System", "global0+:0:0:0"))

        # Get currentTime
        app.Execute("global0 = currentTime;")
        current_time = parse_float(app.Request("System", "global0+:0:0:0"))

        # Set endTime = currentTime + 5 to stop soon
        app.Execute("endTime = currentTime + 5;")

        # Wait until simulation stops (GetSimulationPhase() == 0)
        import time
        for _ in range(20):
            time.sleep(0.5)
            app.Execute("globalInt0 = GetSimulationPhase();")
            phase = int(app.Request("System", "globalInt0+:0:0:0") or 0)
            if phase == 0:
                break

        # Get time it stopped at
        app.Execute("global0 = currentTime;")
        stopped_at = parse_float(app.Request("System", "global0+:0:0:0"))

        # Verify simulation is stopped
        app.Execute("globalInt0 = GetSimulationPhase();")
        still_running = int(app.Request("System", "globalInt0+:0:0:0") or 0) > 0

        # Restore original endTime now that simulation is stopped
        app.Execute(f"endTime = {original_end_time};")

        if still_running:
            return _error(ErrorCode.SIMULATION_TIMEOUT,
                         "Simulation did not stop within timeout",
                         currentTime=stopped_at, originalEndTime=original_end_time)

        return {
            "success": True,
            "stoppedAtTime": stopped_at,
            "originalEndTime": original_end_time
        }
    except Exception as e:
        return _com_error(e, "simulation_stop")


def simulation_pause(model_id: Optional[str] = None) -> dict:
    """Pauses the simulation."""
    try:
        app = get_extendsim_app()
        app.Execute("ExecuteMenuCommand(30001)")

        app.Execute("global0 = currentTime;")
        current_time = parse_float(app.Request("System", "global0+:0:0:0"))

        return {"success": True, "pausedAtTime": current_time}
    except Exception as e:
        return _com_error(e, "simulation_pause")


def simulation_resume(model_id: Optional[str] = None) -> dict:
    """Resumes the simulation."""
    try:
        app = get_extendsim_app()
        app.Execute("ExecuteMenuCommand(30002)")
        return {"success": True, "message": "Simulation resumed"}
    except Exception as e:
        return _com_error(e, "simulation_resume")


def simulation_status(model_id: Optional[str] = None) -> dict:
    """Gets simulation status."""
    try:
        app = get_extendsim_app()

        app.Execute("global0 = currentTime;")
        current_time = parse_float(app.Request("System", "global0+:0:0:0"))

        app.Execute("global0 = endTime;")
        end_time = parse_float(app.Request("System", "global0+:0:0:0"))

        app.Execute("globalInt0 = GetSimulationPhase();")
        sim_phase = int(app.Request("System", "globalInt0+:0:0:0") or 0)

        return {
            "isRunning": sim_phase > 0,
            "simulationPhase": sim_phase,
            "phaseName": SIMULATION_PHASE_NAMES.get(sim_phase, f"unknown({sim_phase})"),
            "currentTime": current_time,
            "endTime": end_time
        }
    except Exception as e:
        return _com_error(e, "simulation_status")


def block_set_value(block_id: int, dialog_number, value,
                    row: int = 0, col: int = 0,
                    model_id: Optional[str] = None) -> dict:
    """Sets a dialog value on a block.

    Args:
        block_id: Block ID
        dialog_number: Variable name (string like "D", "delay") or dialog item number
        value: Value to set (number or string)
        row: Row index for table cells (0-based)
        col: Column index for table cells (0-based)

    Note: Uses SetVariableNumeric for numeric variables (_prm, _pop, _chk, _rdo, no suffix)
          and SetDialogVariable for table/text variables (_dtbl, _ttbl, _dtxt).
    """
    try:
        app = get_extendsim_app()
        model_check = _validate_model_open(app)
        if not model_check.get("success"):
            return model_check

        var_name = str(dialog_number)

        # Route through suffix-based API selection
        if isinstance(value, str) and not var_name.endswith(_DIALOG_VAR_SUFFIXES):
            # String value for a numeric variable — try SetDialogVariable
            # (SetVariableNumeric cannot handle string values)
            app.Execute(f'SetDialogVariable({block_id}, "{var_name}", "{_escape_modl_string(value)}", {row}, {col});')
        else:
            _set_var(app, block_id, var_name, value, row, col)

        # Read back using the same API that was written to
        read_back = _get_var(app, block_id, var_name, row, col)

        # Validate readBack - a value of "-1" often indicates the block ID is invalid
        # or the variable doesn't exist on the block
        if read_back == "-1" or read_back == "-1,0":
            # Check if block actually exists
            app.Execute(f"globalStr0 = BlockName({block_id});")
            block_name = app.Request("System", "globalStr0+:0:0:0") or ""
            if not block_name:
                return _error(ErrorCode.BLOCK_NOT_FOUND,
                              f"Block {block_id} does not exist.",
                              blockId=block_id, variableName=dialog_number,
                              suggestion="Use block_list() to see available blocks.")

        return {
            "success": True,
            "blockId": block_id,
            "variableName": dialog_number,
            "value": value,
            "row": row,
            "col": col,
            "readBack": read_back
        }
    except Exception as e:
        return _error(ErrorCode.SET_VALUE_FAILED, str(e),
                      blockId=block_id, variableName=dialog_number,
                      suggestion="Use dialog_search(query) or block_discover_variables(blockId) to find valid variable names.")


def block_get_value(block_id: int, dialog_number,
                    row: int = 0, col: int = 0,
                    as_string: bool = False,
                    model_id: Optional[str] = None) -> dict:
    """Gets a dialog value from a block.

    Args:
        block_id: Block ID
        dialog_number: Variable name (string like "D", "delay") or dialog item number
        row: Row index for table cells (0-based)
        col: Column index for table cells (0-based)
        as_string: If True, get value as string instead of number

    Note: Uses GetVariableNumeric for numeric variables (_prm, _pop, _chk, _rdo, no suffix)
          and GetDialogVariable for table/text variables (_dtbl, _ttbl, _dtxt).
    """
    try:
        app = get_extendsim_app()
        model_check = _validate_model_open(app)
        if not model_check.get("success"):
            return model_check

        var_name = str(dialog_number)

        # Use suffix-based API routing for reads too
        raw_value = _get_var(app, block_id, var_name, row, col)
        value = raw_value if as_string else parse_float(raw_value) if raw_value else 0.0

        return {
            "success": True,
            "blockId": block_id,
            "variableName": dialog_number,
            "row": row,
            "col": col,
            "value": value
        }
    except Exception as e:
        return _error(ErrorCode.GET_VALUE_FAILED, str(e),
                      blockId=block_id, variableName=dialog_number,
                      suggestion="Use dialog_search(query) or block_discover_variables(blockId) to find valid variable names.")


# Helper: Set a popup menu variable with verification readback
def _set_popup_verified(app, block_id: int, var_name: str, value: int) -> dict:
    """Sets a popup menu dialog variable and verifies the value was applied.

    Popup menus (_pop suffix) use SetVariableNumeric with msg=1 to notify the block.
    This helper reads back the value after setting to confirm it worked.

    Returns:
        dict with success, actualValue, and optionally warning
    """
    _set_var(app, block_id, var_name, value, 0, 0, msg=1)

    # Read back to verify using the correct API
    raw = _get_var(app, block_id, var_name, 0, 0)
    actual = raw
    try:
        actual_val = int(parse_float(actual))
    except (ValueError, TypeError):
        actual_val = -1

    if actual_val == value:
        return {"success": True, "actualValue": actual_val}
    else:
        return {
            "success": False,
            "actualValue": actual_val,
            "requestedValue": value,
            "warning": f"Popup '{var_name}' on block {block_id}: set {value} but read back {actual_val}. "
                       f"Popup menus may not be settable via COM for this block type."
        }


# Helper: Save/close/reopen model to persist popup menu changes
def _persist_popup_change(app) -> dict:
    """Saves, closes, and reopens the current model to force block reinitialization.

    Some blocks (Activity, Create) require a save/close/reopen cycle after
    setting popup menus via COM. The SetDialogVariable call writes the value
    but doesn't trigger the block's internal callback. Reopening the model
    forces all blocks to reinitialize from their persisted state.

    Returns:
        dict with success status and model path
    """
    try:
        # Get current model name and path
        app.Execute("globalStr0 = GetModelName();")
        model_name = app.Request("System", "globalStr0+:0:0:0")
        if not model_name:
            return {"success": False, "error": "No model open"}

        app.Execute(f'globalStr0 = GetModelPath("{model_name}");')
        model_path = app.Request("System", "globalStr0+:0:0:0")
        full_path = (model_path + model_name).replace("\\", "/")

        # Save current state
        app.Execute("SaveModel()")

        # Close without save prompt (already saved)
        app.Execute("SetDirty(False);")
        app.Execute("ExecuteMenuCommand(4)")

        # Reopen
        app.Execute(f'OpenExtendFile("{full_path}")')

        return {"success": True, "modelPath": full_path}
    except Exception as e:
        return {"success": False, "error": str(e)}


# Activity delay option constants (0-based popup indices, verified via GetDialogItemLabel)
# 0="" (none), 1="a constant", 2="from the D connector",
# 3="an item's attribute value", 4="specified by a distribution", 5="from a lookup table"
DELAY_OPTIONS = {
    "none": 0,          # No delay (pass through)
    "fixed": 1,         # A constant value
    "connector": 2,     # From input connector
    "attribute": 3,     # From an item attribute
    "distribution": 4,  # From a distribution
    "table": 5          # From a lookup table
}

# Workstation delay option constants (different popup order than Activity)
# WARNING: These indices are UNVERIFIED — GetDialogItemLabel returns empty for
# Workstation's Delay_Options_pop (dialogId=99). Unlike Activity (dialogId=66),
# the Workstation popup cannot be verified programmatically via COM.
# If delays don't behave as expected, verify indices manually in ExtendSim GUI.
WORKSTATION_DELAY_OPTIONS = {
    "fixed": 1,       # A constant
    "attribute": 2,   # An item's attribute value
    "distribution": 3, # Specified by a distribution
    "table": 4        # From a lookup table
}

# Distribution type constants
DISTRIBUTIONS = {
    "constant": 32,
    "uniform": 33,
    "triangular": 34,
    "normal": 35,
    "exponential": 36,
    "erlang": 37,
    "gamma": 38,
    "weibull": 39,
    "lognormal": 40,
    "beta": 41,
    "pearson5": 42,
    "pearson6": 43
}


def activity_set_delay(block_id: int,
                       delay_type: str = "fixed",
                       value: Optional[float] = None,
                       distribution: Optional[str] = None,
                       arg1: Optional[float] = None,
                       arg2: Optional[float] = None,
                       arg3: Optional[float] = None,
                       max_items: Optional[int] = None,
                       preempt_enabled: Optional[bool] = None,
                       shutdown_enabled: Optional[bool] = None,
                       # v1.17.4.4 — ABC costing and shift
                       cost_per_time: Optional[float] = None,
                       cost_per_item: Optional[float] = None,
                       cost_time_unit: Optional[int] = None,
                       shift: Optional[int] = None,
                       model_id: Optional[str] = None) -> dict:
    """Sets delay configuration on an Activity block.

    Activity popup menus use 0-based indices. SetVariableNumeric with msg=1
    notifies the block of changes. Falls back to save/close/reopen cycle
    if popup verification fails.

    Args:
        block_id: Activity block ID
        delay_type: "fixed", "distribution", "connector", or "attribute"
        value: Fixed delay value (for delay_type="fixed")
        distribution: Distribution name (for delay_type="distribution"):
                     "constant", "uniform", "triangular", "normal",
                     "exponential", "erlang", "gamma", "weibull",
                     "lognormal", "beta", "pearson5", "pearson6"
        arg1: Distribution argument 1 (e.g., min, mean)
        arg2: Distribution argument 2 (e.g., max, stddev)
        arg3: Distribution argument 3 (e.g., mode for triangular)
        max_items: Maximum items in activity (parallel processing capacity)
        preempt_enabled: Enable preemption via PE connector
        shutdown_enabled: Enable shutdown via SD connector

    Returns:
        Dictionary with success status
    """
    try:
        app = get_extendsim_app()

        check = _validate_block_type(app, block_id, "Activity")
        if not check.get("success"):
            return check

        # Set delay option popup (0-based, by name)
        delay_opt = DELAY_OPTIONS.get(delay_type.lower(), 1)
        pop_result = _set_popup_verified(app, block_id, "Delay_Options_pop", delay_opt)

        if delay_type.lower() == "fixed":
            v = value if value is not None else 1
            _set_var(app, block_id, "WaitDelta_prm", v)

        elif delay_type.lower() == "distribution":
            if distribution:
                dist_code = DISTRIBUTIONS.get(distribution.lower(), 36)  # default exponential
                _set_popup_verified(app, block_id, "Delay_Distributions_pop", dist_code)

            if arg1 is not None:
                _set_var(app, block_id, "Delay_Arg1_prm", arg1)
            else:
                _set_var(app, block_id, "Delay_Arg1_prm", 1)

            if arg2 is not None:
                _set_var(app, block_id, "Delay_Arg2_prm", arg2)

            if arg3 is not None:
                _set_var(app, block_id, "Delay_Arg3_prm", arg3)

        # With SetVariableNumeric + msg=1, popup changes should take effect immediately.
        # Verify the popup readback; only fall back to save/close/reopen if it failed.
        persist = None
        if not pop_result["success"]:
            persist = _persist_popup_change(app)

        # v1.17.4 — Additional Activity parameters
        if max_items is not None:
            _set_var(app, block_id, "MaxLength_prm", max_items)
        if preempt_enabled is not None:
            _set_var(app, block_id, "PE_Enable_chk", 1 if preempt_enabled else 0)
        if shutdown_enabled is not None:
            _set_var(app, block_id, "SD_Enable_chk", 1 if shutdown_enabled else 0)

        # v1.17.4.4 — ABC costing and shift
        if cost_per_time is not None:
            _set_var(app, block_id, "CostPerTime_prm", cost_per_time)
        if cost_per_item is not None:
            _set_var(app, block_id, "CostPerItem_prm", cost_per_item)
        if cost_time_unit is not None:
            _set_var(app, block_id, "CostingTimeUnit_pop", cost_time_unit)
        if shift is not None:
            _set_var(app, block_id, "Shift_pop", shift)

        result = {
            "success": True,
            "blockId": block_id,
            "delayType": delay_type,
            "distribution": distribution,
            "value": value,
            "args": {"arg1": arg1, "arg2": arg2, "arg3": arg3},
            "maxItems": max_items,
            "preemptEnabled": preempt_enabled,
            "shutdownEnabled": shutdown_enabled,
            "costPerTime": cost_per_time,
            "costPerItem": cost_per_item,
            "costTimeUnit": cost_time_unit,
            "shift": shift,
            "persisted": persist.get("success", False) if persist else None
        }
        if not pop_result["success"]:
            result["warnings"] = [pop_result["warning"]]
            if persist and not persist.get("success"):
                result["warnings"].append(
                    f"Save/reopen fallback also failed: {persist.get('error', 'unknown')}. "
                    f"Popup change may not take effect until model is manually saved and reopened."
                )
        return result
    except Exception as e:
        return _error(ErrorCode.SET_VALUE_FAILED, str(e),
                      blockId=block_id, operation="activity_set_delay")


# Create block arrival options (0-based popup indices, confirmed via live testing)
# Variable name is CreateOptions_pop (NOT Create_Options_pop)
CREATE_ARRIVAL_OPTIONS = {
    "schedule": 0,      # Arrival Schedule table
    "distribution": 1,  # Random distribution
    "connector": 2,     # Value from connector
    "database": 3,      # From database
    "infinite": 5       # Infinite supply (pull-based, no arrivals until downstream demand)
}


def create_set_arrivals(block_id: int,
                        arrival_type: str = "distribution",
                        distribution: str = "exponential",
                        arg1: Optional[float] = None,
                        arg2: Optional[float] = None,
                        arg3: Optional[float] = None,
                        max_arrivals: Optional[int] = None,
                        # v1.17.4.4 — ABC costing
                        cost_per_time: Optional[float] = None,
                        cost_per_item: Optional[float] = None,
                        cost_time_unit: Optional[int] = None,
                        model_id: Optional[str] = None) -> dict:
    """Configures arrival pattern for Create block.

    Create popup menus use 0-based indices. SetVariableNumeric with msg=1
    notifies the block. Falls back to save/close/reopen if popup verification fails.

    Args:
        block_id: Create block ID
        arrival_type: "schedule", "distribution", "connector", "database", "infinite"
        distribution: Distribution name (for arrival_type="distribution"):
                     "constant", "uniform", "triangular", "normal",
                     "exponential", "erlang", "gamma", "weibull",
                     "lognormal", "beta", "pearson5", "pearson6"
        arg1: Distribution argument 1 (e.g., mean for exponential)
        arg2: Distribution argument 2 (e.g., max for uniform)
        arg3: Distribution argument 3 (e.g., mode for triangular)
        max_arrivals: Maximum number of items to create (None=infinite)

    Returns:
        Dictionary with success status
    """
    try:
        app = get_extendsim_app()

        check = _validate_block_type(app, block_id, "Create")
        if not check.get("success"):
            return check

        warnings = []

        # Set arrival option (CreateOptions_pop - no underscore between Create and Options)
        opt = CREATE_ARRIVAL_OPTIONS.get(arrival_type.lower(), 1)
        pop_result = _set_popup_verified(app, block_id, "CreateOptions_pop", opt)
        if not pop_result["success"]:
            warnings.append(pop_result["warning"])

        if arrival_type.lower() == "distribution":
            # Set distribution type with verification
            dist_code = DISTRIBUTIONS.get(distribution.lower(), 36)  # default exponential
            pop_result2 = _set_popup_verified(app, block_id, "Rnd_Distributions_pop", dist_code)
            if not pop_result2["success"]:
                warnings.append(pop_result2["warning"])

            # Set distribution arguments
            if arg1 is not None:
                _set_var(app, block_id, "Rnd_Arg1_prm", arg1)
            if arg2 is not None:
                _set_var(app, block_id, "Rnd_Arg2_prm", arg2)
            if arg3 is not None:
                _set_var(app, block_id, "Rnd_Arg3_prm", arg3)

        # Set max arrivals if specified (correct variable names from block_discover_variables)
        if max_arrivals is not None:
            _set_var(app, block_id, "RndI_MaxItems_chk", 1)
            _set_var(app, block_id, "RndI_MaxItems_prm", max_arrivals)

        # v1.17.4.4 — ABC costing
        if cost_per_time is not None:
            _set_var(app, block_id, "Cost_PerTime_prm", cost_per_time)
        if cost_per_item is not None:
            _set_var(app, block_id, "Cost_PerItem_prm", cost_per_item)
        if cost_time_unit is not None:
            _set_var(app, block_id, "Cost_PerTime_pop", cost_time_unit)

        # With SetVariableNumeric + msg=1, popup changes should take effect immediately.
        # Only fall back to save/close/reopen if popup verification failed.
        persist = None
        if warnings:  # popup verification failures
            persist = _persist_popup_change(app)

        result = {
            "success": True,
            "blockId": block_id,
            "arrivalType": arrival_type,
            "distribution": distribution if arrival_type.lower() == "distribution" else None,
            "args": {"arg1": arg1, "arg2": arg2, "arg3": arg3},
            "maxArrivals": max_arrivals,
            "costPerTime": cost_per_time,
            "costPerItem": cost_per_item,
            "costTimeUnit": cost_time_unit,
            "persisted": persist.get("success", False) if persist else None
        }
        if warnings:
            result["warnings"] = warnings
            if persist and not persist.get("success"):
                result["warnings"].append(
                    f"Save/reopen fallback also failed: {persist.get('error', 'unknown')}. "
                    f"Popup change may not take effect until model is manually saved and reopened."
                )
        return result
    except Exception as e:
        return _error(ErrorCode.SET_VALUE_FAILED, str(e),
                      blockId=block_id, operation="create_set_arrivals")


# Gate block demand options
GATE_DEMAND = {
    "passing": 1,   # Let items pass when condition met
    "waiting": 2,   # Hold items until condition met
    "value": 3      # Use connector value
}

# Gate initial state options
GATE_OPEN_CLOSE = {
    "opened": 1,
    "closed": 2
}


def gate_set_condition(block_id: int,
                       demand_type: str = "passing",
                       initial_state: str = "opened",
                       open_value: float = 1,
                       close_value: float = 0,
                       model_id: Optional[str] = None) -> dict:
    """Configures Gate block condition.

    Args:
        block_id: Gate block ID
        demand_type: "passing", "waiting", "value"
        initial_state: "opened" or "closed"
        open_value: Value that opens gate (for demand_type="value")
        close_value: Value that closes gate (for demand_type="value")

    Returns:
        Dictionary with success status
    """
    try:
        app = get_extendsim_app()

        check = _validate_block_type(app, block_id, "Gate")
        if not check.get("success"):
            return check

        warnings = []

        # Set demand type (GateDemand_pop) with verification
        demand = GATE_DEMAND.get(demand_type.lower(), 1)
        pop_result = _set_popup_verified(app, block_id, "GateDemand_pop", demand)
        if not pop_result["success"]:
            warnings.append(pop_result["warning"])

        # Set initial state (InitialState_pop) with verification
        state = GATE_OPEN_CLOSE.get(initial_state.lower(), 1)
        pop_result2 = _set_popup_verified(app, block_id, "InitialState_pop", state)
        if not pop_result2["success"]:
            warnings.append(pop_result2["warning"])

        # Set open/close values for value mode
        if demand_type.lower() == "value":
            _set_var(app, block_id, "OpenValue_prm", open_value)
            _set_var(app, block_id, "CloseValue_prm", close_value)

        result = {
            "success": True,
            "blockId": block_id,
            "demandType": demand_type,
            "initialState": initial_state,
            "openValue": open_value if demand_type.lower() == "value" else None,
            "closeValue": close_value if demand_type.lower() == "value" else None
        }
        if warnings:
            result["warnings"] = warnings
        return result
    except Exception as e:
        return _error(ErrorCode.SET_VALUE_FAILED, str(e),
                      blockId=block_id, operation="gate_set_condition")


# Queue ranking options
QUEUE_RANK = {
    "fifo": 1,
    "lifo": 2,
    "attribute": 3,
    "priority": 4
}


def queue_set_priority(block_id: int,
                       rank_type: str = "fifo",
                       sort_attribute: Optional[str] = None,
                       ascending: bool = True,
                       max_length: Optional[int] = None,
                       renege_enabled: Optional[bool] = None,
                       renege_time: Optional[float] = None,
                       # v1.17.4.4 — ABC costing and shift
                       calc_wait_costs: Optional[bool] = None,
                       shift: Optional[int] = None,
                       calc_delay: Optional[bool] = None,
                       model_id: Optional[str] = None) -> dict:
    """Sets ranking/priority configuration on a Queue block.

    Args:
        block_id: Queue block ID
        rank_type: "fifo", "lifo", "attribute", or "priority"
        sort_attribute: Attribute name to sort by (for rank_type="attribute")
        ascending: Sort order (True=ascending, False=descending)
        max_length: Maximum queue length (0=unlimited). Items rejected when full.
        renege_enabled: Enable reneging (items leave queue after timeout)
        renege_time: Time before item reneges (leaves queue)

    Returns:
        Dictionary with success status
    """
    try:
        app = get_extendsim_app()

        # Set queue ranking type (QueueRank_Pop — uppercase P variant)
        rank_code = QUEUE_RANK.get(rank_type.lower(), 1)
        _set_var(app, block_id, "QueueRank_Pop", rank_code)

        warnings = []

        if rank_type.lower() == "attribute" and sort_attribute:
            # Set the sort attribute (SortAttrib_Pop)
            # NOTE: SortAttrib_Pop is a popup whose indices depend on which attributes
            # exist in the model. The attribute name string may not map 1:1 to popup index.
            # Use SetDialogVariable for string value regardless of suffix.
            _set_var_string(app, block_id, "SortAttrib_Pop", sort_attribute)

            # Readback verification
            readback = _get_var(app, block_id, "SortAttrib_Pop") or ""
            if readback and readback != sort_attribute:
                warnings.append(
                    f"SortAttrib_Pop readback mismatch: set '{sort_attribute}', got '{readback}'. "
                    "The attribute popup index may not match the name. Verify in ExtendSim GUI."
                )

        # Set sort order if using attribute ranking
        if rank_type.lower() in ("attribute", "priority"):
            sort_order = 1 if ascending else 2  # 1=ascending, 2=descending
            _set_var(app, block_id, "SortOrder_Pop", sort_order)

        # v1.17.4 — Queue capacity and reneging parameters
        if max_length is not None:
            _set_var(app, block_id, "MaxQueueLength_prm", max_length)
        if renege_enabled is not None:
            _set_var(app, block_id, "Reneging_chk", 1 if renege_enabled else 0)
        if renege_time is not None:
            _set_var(app, block_id, "RenegeTime_prm", renege_time)

        # v1.17.4.4 — ABC costing, shift, delay calc
        if calc_wait_costs is not None:
            _set_var(app, block_id, "CalcWaitCosts_chk", 1 if calc_wait_costs else 0)
        if shift is not None:
            _set_var(app, block_id, "Shift_pop", shift)
        if calc_delay is not None:
            _set_var(app, block_id, "CalcDelay_chk", 1 if calc_delay else 0)

        result = {
            "success": True,
            "blockId": block_id,
            "rankType": rank_type,
            "sortAttribute": sort_attribute,
            "ascending": ascending,
            "maxLength": max_length,
            "renegeEnabled": renege_enabled,
            "renegeTime": renege_time,
            "calcWaitCosts": calc_wait_costs,
            "shift": shift,
            "calcDelay": calc_delay
        }
        if warnings:
            result["warnings"] = warnings
        return result
    except Exception as e:
        return _error(ErrorCode.SET_VALUE_FAILED, str(e),
                      blockId=block_id, operation="queue_set_priority")


# ============================================================================
# VALIDATION HELPERS
# ============================================================================

def _validate_block_exists(app, block_id: int, operation: str = "operation") -> dict:
    """Validates that a block exists before operation.

    Args:
        app: ExtendSim COM application reference
        block_id: Block ID to validate
        operation: Name of the operation (for error message)

    Returns:
        Dictionary with success=True and blockName if exists, or success=False with error
    """
    try:
        app.Execute(f"globalStr0 = BlockName({block_id});")
        name = app.Request("System", "globalStr0+:0:0:0")
        if not name:
            return {
                "success": False,
                "error": f"Block {block_id} does not exist",
                "errorCode": "INVALID_BLOCK_ID",
                "suggestion": "Use block_list() to see all blocks and their IDs."
            }
        return {"success": True, "blockName": name}
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "errorCode": "BLOCK_QUERY_FAILED"
        }


def _validate_model_open(app) -> dict:
    """Validates that a model is open.

    Args:
        app: ExtendSim COM application reference

    Returns:
        Dictionary with success=True and modelName if open, or success=False with error
    """
    try:
        app.Execute("globalStr0 = GetModelName();")
        name = app.Request("System", "globalStr0+:0:0:0")
        if not name:
            return {
                "success": False,
                "error": "No model is open",
                "errorCode": "MODEL_NOT_OPEN",
                "suggestion": "Use model_open(filePath) to open a model or model_new() to create one."
            }
        return {"success": True, "modelName": name}
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "errorCode": "MODEL_QUERY_FAILED"
        }


def _validate_block_type(app, block_id: int, expected_type: str) -> dict:
    """Validates that a block is of the expected type.

    Args:
        app: ExtendSim COM application reference
        block_id: Block ID to validate
        expected_type: Expected block name (e.g., "Create", "Activity", "Queue", "Gate")

    Returns:
        Dictionary with success=True if matches, or success=False with error
    """
    try:
        app.Execute(f"globalStr0 = BlockName({block_id});")
        actual_name = app.Request("System", "globalStr0+:0:0:0")
        if not actual_name:
            return {
                "success": False,
                "error": f"Block {block_id} does not exist",
                "errorCode": "INVALID_BLOCK_ID",
                "suggestion": "Use block_list() to see all blocks and their IDs."
            }
        if actual_name.lower() != expected_type.lower():
            return {
                "success": False,
                "error": f"Block {block_id} is '{actual_name}', not '{expected_type}'",
                "errorCode": "WRONG_BLOCK_TYPE",
                "actualType": actual_name,
                "expectedType": expected_type,
                "suggestion": f"Use block_list() to find a '{expected_type}' block, or use block_configure which auto-detects block type."
            }
        return {"success": True, "blockName": actual_name}
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "errorCode": "BLOCK_QUERY_FAILED"
        }


def model_validate(model_id: Optional[str] = None) -> dict:
    """Validates model integrity.

    Checks for:
    - Unconnected blocks (warnings)
    - Blocks with missing required connections (errors)

    Returns:
        Dictionary with validation results
    """
    try:
        app = get_extendsim_app()

        # Check if model is open
        model_check = _validate_model_open(app)
        if not model_check.get("success"):
            return model_check

        issues = []
        blocks_data = block_list()
        connections_data = connection_list()

        blocks = blocks_data.get("blocks", [])
        connections = connections_data.get("connections", [])

        # Build sets: blocks with outputs, blocks with inputs, and predecessors
        connected_blocks = set()
        blocks_with_output = set()  # blocks that send connections
        blocks_with_input = set()   # blocks that receive connections
        predecessors = {}  # block_id -> set of predecessor block IDs + names

        for conn in connections:
            from_id = conn.get("from", {}).get("blockId")
            to_id = conn.get("to", {}).get("blockId")
            if from_id is not None:
                connected_blocks.add(from_id)
                blocks_with_output.add(from_id)
            if to_id is not None:
                connected_blocks.add(to_id)
                blocks_with_input.add(to_id)
            if from_id is not None and to_id is not None:
                if to_id not in predecessors:
                    predecessors[to_id] = set()
                predecessors[to_id].add(from_id)
            # Handle shared connections
            for ep in conn.get("endpoints", []):
                ep_id = ep.get("blockId")
                if ep_id is not None:
                    connected_blocks.add(ep_id)

        # Build block name lookup
        block_name_map = {}
        for block in blocks:
            block_name_map[block.get("blockId")] = block.get("blockName", "")

        # Check each block
        for block in blocks:
            block_id = block.get("blockId")
            block_name = block.get("blockName", "")
            label = block.get("label", str(block_id))

            # Check if block has any connections
            if block_id not in connected_blocks:
                issues.append({
                    "type": "warning",
                    "blockId": block_id,
                    "blockName": block_name,
                    "label": label,
                    "message": f"Block '{label}' ({block_name}) has no connections"
                })
                continue

            # Create block must have an output
            if block_name == "Create" and block_id not in blocks_with_output:
                issues.append({
                    "type": "error",
                    "blockId": block_id,
                    "blockName": block_name,
                    "label": label,
                    "message": f"Create block '{label}' has no output connection"
                })

            # Exit block must have an input
            if block_name == "Exit" and block_id not in blocks_with_input:
                issues.append({
                    "type": "error",
                    "blockId": block_id,
                    "blockName": block_name,
                    "label": label,
                    "message": f"Exit block '{label}' has no input connection"
                })

            # Activity should have a Queue predecessor
            if block_name == "Activity" and block_id in predecessors:
                pred_names = {block_name_map.get(pid, "") for pid in predecessors[block_id]}
                if "Queue" not in pred_names and "Workstation" not in pred_names:
                    issues.append({
                        "type": "warning",
                        "blockId": block_id,
                        "blockName": block_name,
                        "label": label,
                        "message": f"Activity block '{label}' has no Queue predecessor - may cause simulation issues"
                    })

        # Count errors vs warnings
        error_count = len([i for i in issues if i["type"] == "error"])
        warning_count = len([i for i in issues if i["type"] == "warning"])

        return {
            "success": True,
            "valid": error_count == 0,
            "modelName": model_check.get("modelName", ""),
            "blockCount": len(blocks),
            "connectionCount": len(connections),
            "errorCount": error_count,
            "warningCount": warning_count,
            "issues": issues
        }
    except Exception as e:
        return _com_error(e, "model_validate")


def model_overview(model_id: Optional[str] = None) -> dict:
    """Returns a comprehensive model summary in one call.
    Combines: model name, hierarchy structure (with duplicate detection),
    database list, simulation setup, and AI context.
    Designed to work efficiently on large models (no block enumeration).
    """
    try:
        app = get_extendsim_app()
        model_check = _validate_model_open(app)
        if not model_check.get("success"):
            return model_check

        # 1. Model name
        app.Execute("globalStr0 = GetModelName();")
        model_name = app.Request("System", "globalStr0+:0:0:0") or ""

        # 2. Block count (fast — single ModL call)
        app.Execute("global0 = NumBlocks();")
        num_blocks = int(parse_float(app.Request("System", "global0+:0:0:0")) or 0)

        # 3. Hierarchy structure with duplicate detection
        hier_result = hierarchy_list(model_id)
        hierarchies = hier_result.get("hierarchies", []) if hier_result.get("success") else []

        # Group by name for duplicate detection
        top_level = {}  # name -> {count, blockIds, internalBlocks}
        nested = {}     # name -> {count, internalBlocks}
        for h in hierarchies:
            name = h.get("blockName", "")
            bid = h.get("blockId")
            depth = h.get("depth", 0)
            internal = h.get("internalBlockCount", 0)

            if depth == 0:
                if name not in top_level:
                    top_level[name] = {"count": 0, "blockIds": [], "internalBlocks": internal}
                top_level[name]["count"] += 1
                top_level[name]["blockIds"].append(bid)
            else:
                if name not in nested:
                    nested[name] = {"count": 0, "internalBlocks": internal}
                nested[name]["count"] += 1

        hierarchy_summary = []
        for name, info in top_level.items():
            entry = {
                "name": name,
                "count": info["count"],
                "internalBlocks": info["internalBlocks"],
                "depth": 0,
            }
            if info["count"] <= 5:
                entry["blockIds"] = info["blockIds"]
            else:
                entry["blockIds"] = info["blockIds"][:3]
                entry["note"] = f"{info['count']} identical copies"
            hierarchy_summary.append(entry)

        for name, info in nested.items():
            hierarchy_summary.append({
                "name": name,
                "count": info["count"],
                "internalBlocks": info["internalBlocks"],
                "depth": 1,
            })

        # 4. Database structure
        db_result = db_list(model_id)
        databases = []
        if db_result.get("success"):
            for db in db_result.get("databases", []):
                db_name = db.get("name", "")
                if db_name.startswith("_"):
                    databases.append({"name": db_name, "tables": db.get("tableCount", 0), "internal": True})
                else:
                    tables = []
                    for t in db.get("tables", []):
                        tables.append({
                            "name": t["name"],
                            "records": t.get("records", 0),
                            "fields": t.get("fields", 0)
                        })
                    databases.append({"name": db_name, "tables": tables})

        # 5. Simulation setup
        setup_result = simulation_setup_get(model_id)
        sim_setup = {}
        if setup_result.get("success"):
            sim_setup = {
                "endTime": setup_result.get("endTime"),
                "startTime": setup_result.get("startTime"),
                "numberOfRuns": setup_result.get("numberOfRuns"),
                "timeUnits": setup_result.get("timeUnits"),
            }

        # 6. AI context (if any)
        ctx_result = context_get(model_id)
        ai_context = None
        if ctx_result.get("success") and ctx_result.get("exists"):
            ai_context = {
                "purpose": ctx_result.get("purpose"),
                "assumptions": ctx_result.get("assumptions"),
                "tags": ctx_result.get("tags"),
                "notes": ctx_result.get("notes"),
            }

        return {
            "success": True,
            "name": model_name,
            "totalBlocks": num_blocks,
            "hierarchySummary": hierarchy_summary,
            "totalHierarchies": len(hierarchies),
            "databases": databases,
            "simulationSetup": sim_setup,
            "aiContext": ai_context,
        }
    except Exception as e:
        return _com_error(e, "model_overview")


def model_snapshot(model_id: Optional[str] = None) -> dict:
    """Returns a combined snapshot of the model: all blocks and all connections in one call."""
    try:
        app = get_extendsim_app()
        model_check = _validate_model_open(app)
        if not model_check.get("success"):
            return model_check

        blocks_result = block_list(model_id=model_id)
        connections_result = connection_list(model_id=model_id)

        blocks = blocks_result.get("blocks", [])
        connections = connections_result.get("connections", [])

        return {
            "success": True,
            "modelName": model_check.get("modelName", ""),
            "blockCount": len(blocks),
            "connectionCount": len(connections),
            "blocks": blocks,
            "connections": connections
        }
    except Exception as e:
        return _com_error(e, "model_snapshot")


# ============================================================================
# SIMULATION RESULTS
# ============================================================================

def _enumerate_blocks_by_name(app, target_names=None):
    """Lightweight block enumeration — only gets blockId + blockName.

    2 COM calls per block instead of 5 (no label, type, library).
    For 24k blocks: ~48K COM calls vs ~120K with block_list().

    Args:
        app: ExtendSim COM object
        target_names: Optional set of block names to filter
            (e.g. {"Exit", "Queue", "Activity", "Create"})

    Returns:
        List of (blockId, blockName) tuples
    """
    results = []
    current_id = -1
    while True:
        app.Execute(f"global0 = objectIDNext({current_id}, 0);")
        next_id = int(parse_float(app.Request("System", "global0+:0:0:0")))
        if next_id == -1:
            break
        current_id = next_id

        app.Execute(f"globalStr0 = BlockName({next_id});")
        name = app.Request("System", "globalStr0+:0:0:0") or ""

        if target_names is None or name in target_names:
            results.append((next_id, name))

    return results


def simulation_get_results(model_id: Optional[str] = None, block_ids: Optional[list] = None) -> dict:
    """Gets simulation statistics after running.

    Collects statistics from Exit, Queue, Activity, and Create blocks.
    If block_ids is provided, only collects stats for those block IDs.

    Uses lightweight block enumeration (2 COM calls/block) instead of
    full block_list (5 COM calls/block) for large model performance.

    Returns:
        Dictionary with simulation results and statistics per block type
    """
    try:
        app = get_extendsim_app()

        # Check if model is open
        model_check = _validate_model_open(app)
        if not model_check.get("success"):
            return model_check

        results = {
            "success": True,
            "modelName": model_check.get("modelName", ""),
            "simulationTime": 0,
            "exitStatistics": [],
            "queueStatistics": [],
            "activityStatistics": [],
            "createStatistics": []
        }

        # Get simulation time
        app.Execute("global0 = currentTime;")
        results["simulationTime"] = parse_float(app.Request("System", "global0+:0:0:0"))

        # Collect stats-relevant blocks using lightweight enumeration
        STATS_BLOCK_NAMES = {"Exit", "Queue", "Activity", "Create"}
        block_ids_set = set(block_ids) if block_ids else None

        if block_ids_set:
            # Filter mode: only scan specified blocks (very fast)
            blocks = []
            for bid in block_ids_set:
                app.Execute(f"globalStr0 = BlockName({bid});")
                name = app.Request("System", "globalStr0+:0:0:0") or ""
                if name in STATS_BLOCK_NAMES:
                    blocks.append((bid, name))
        else:
            # Full scan with lightweight enumeration (2 calls/block, not 5)
            blocks = _enumerate_blocks_by_name(app, STATS_BLOCK_NAMES)

        for block_id, block_name in blocks:
            # Get label only for matched blocks (not all 24k)
            try:
                app.Execute(f"globalStr0 = GetBlockLabel({block_id});")
                label = app.Request("System", "globalStr0+:0:0:0") or ""
            except Exception:
                label = ""

            if block_name == "Exit":
                # Exit block statistics (throughput)
                stats = {"blockId": block_id, "label": label}
                try:
                    stats["itemsExited"] = parse_float_nullable(_get_var(app, block_id, "TotalExited_prm"))
                except Exception:
                    stats["itemsExited"] = None
                results["exitStatistics"].append(stats)

            elif block_name == "Queue":
                # Queue block statistics
                stats = {"blockId": block_id, "label": label}

                # BUG-005: use the SAME dialog-var names as block_get_stats/BLOCK_STAT_VARS
                # (AveLength_prm / AveWait_prm / QueueLength_prm). The prior names
                # (AverageLength_prm / AverageWait_prm / L_prm) don't exist on the 2024
                # Queue block, so the summary path read None while block_get_stats had data.
                try:
                    stats["averageLength"] = parse_float_nullable(_get_var(app, block_id, "AveLength_prm"))
                except Exception:
                    stats["averageLength"] = None

                try:
                    stats["maxLength"] = parse_float_nullable(_get_var(app, block_id, "MaxLength_prm"))
                except Exception:
                    stats["maxLength"] = None

                try:
                    stats["averageWaitTime"] = parse_float_nullable(_get_var(app, block_id, "AveWait_prm"))
                except Exception:
                    stats["averageWaitTime"] = None

                try:
                    stats["currentLength"] = parse_float_nullable(_get_var(app, block_id, "QueueLength_prm"))
                except Exception:
                    stats["currentLength"] = None

                results["queueStatistics"].append(stats)

            elif block_name == "Activity":
                # Activity block statistics
                stats = {"blockId": block_id, "label": label}
                try:
                    stats["utilization"] = parse_float_nullable(_get_var(app, block_id, "Utilization_prm"))
                except Exception:
                    stats["utilization"] = None

                try:
                    stats["itemsProcessed"] = parse_float_nullable(_get_var(app, block_id, "TotalItemsExited_prm"))
                except Exception:
                    stats["itemsProcessed"] = None

                try:
                    stats["averageProcessTime"] = parse_float_nullable(_get_var(app, block_id, "AverageWait_prm"))
                except Exception:
                    stats["averageProcessTime"] = None

                results["activityStatistics"].append(stats)

            elif block_name == "Create":
                # Create block statistics
                stats = {"blockId": block_id, "label": label}
                # BUG-005: Create's total is RndI_TotalQuantity_prm (same as block_get_stats'
                # "totalCreated"); the prior ItemsCreated_prm/TotalItemsCreated_prm don't exist
                # on the 2024 Create block, so itemsCreated always read None.
                try:
                    stats["itemsCreated"] = parse_float_nullable(_get_var(app, block_id, "RndI_TotalQuantity_prm"))
                except Exception:
                    stats["itemsCreated"] = None
                results["createStatistics"].append(stats)

        # Summary statistics (filter out None values)
        total_exited = sum(s.get("itemsExited") or 0 for s in results["exitStatistics"])
        total_created = sum(s.get("itemsCreated") or 0 for s in results["createStatistics"])

        results["summary"] = {
            "totalItemsCreated": total_created,
            "totalItemsExited": total_exited,
            "throughputRate": total_exited / results["simulationTime"] if results["simulationTime"] > 0 else 0
        }

        return results
    except Exception as e:
        return _com_error(e, "simulation_get_results")


def template_list() -> dict:
    """Lists available block templates.

    Returns:
        Dictionary with list of templates and their descriptions
    """
    try:
        templates = _load_templates()
        template_info = []

        for name, tpl in templates.items():
            template_info.append({
                "name": name,
                "description": tpl.get("description", ""),
                "blockCount": len(tpl.get("blocks", [])),
                "connectionCount": len(tpl.get("connections", [])),
                "parameters": list(tpl.get("parameters", {}).keys())
            })

        return {
            "success": True,
            "templates": template_info,
            "count": len(template_info)
        }
    except Exception as e:
        return _com_error(e, "template_list")


def block_template(template_name: str,
                   start_x: int = 100,
                   start_y: int = 100,
                   spacing: int = 120,
                   parameters: Optional[dict] = None,
                   model_id: Optional[str] = None) -> dict:
    """Creates blocks from a predefined template.

    Args:
        template_name: Name of the template to use
        start_x: Starting X position (pixels)
        start_y: Starting Y position (pixels)
        spacing: Horizontal spacing between blocks (pixels)
        parameters: Optional dict of parameter values to set
                   (e.g., {"arrivalRate": 5, "processTime": 10})

    Returns:
        Dictionary with created blocks and their IDs
    """
    try:
        templates = _load_templates()

        if template_name not in templates:
            available = list(templates.keys())
            return _error(ErrorCode.TEMPLATE_NOT_FOUND,
                         f"Template not found: {template_name}",
                         availableTemplates=available)

        tpl = templates[template_name]
        created_blocks = {}
        errors = []

        # Create blocks
        for i, block_def in enumerate(tpl.get("blocks", [])):
            block_name = block_def.get("name", f"block_{i}")
            library = block_def.get("library", "Item.lbr")
            block_type = block_def.get("block", "Activity")
            label = block_def.get("label", block_name)

            x = start_x + i * spacing
            y = start_y

            result = block_add(library, block_type, x, y, label=label)

            if result.get("success"):
                created_blocks[block_name] = result["blockId"]
            else:
                errors.append({
                    "block": block_name,
                    "error": result.get("error", "Unknown error")
                })

        # Create connections
        connections_made = []
        for conn_def in tpl.get("connections", []):
            from_name = conn_def.get("from")
            to_name = conn_def.get("to")
            from_connector = conn_def.get("fromConnector", "ItemOut")
            to_connector = conn_def.get("toConnector", "ItemIn")

            from_id = created_blocks.get(from_name)
            to_id = created_blocks.get(to_name)

            if from_id is not None and to_id is not None:
                conn_result = block_connect(from_id, from_connector, to_id, to_connector)
                if conn_result.get("success"):
                    connections_made.append({
                        "from": from_name,
                        "to": to_name,
                        "fromId": from_id,
                        "toId": to_id
                    })
                else:
                    errors.append({
                        "connection": f"{from_name} -> {to_name}",
                        "error": conn_result.get("error", "Unknown error")
                    })

        # Apply parameters
        params_applied = []
        if parameters:
            param_defs = tpl.get("parameters", {})
            for param_name, param_value in parameters.items():
                if param_name in param_defs:
                    pdef = param_defs[param_name]
                    block_name = pdef.get("block")
                    var_name = pdef.get("var")

                    if block_name in created_blocks and var_name:
                        block_id = created_blocks[block_name]
                        set_result = block_set_value(block_id, var_name, param_value)
                        if set_result.get("success"):
                            params_applied.append({
                                "parameter": param_name,
                                "blockId": block_id,
                                "variable": var_name,
                                "value": param_value
                            })
                        else:
                            errors.append({
                                "parameter": param_name,
                                "error": set_result.get("error", "Unknown error")
                            })

        result = {
            "success": len(errors) == 0,
            "template": template_name,
            "blocks": created_blocks,
            "blockCount": len(created_blocks),
            "connections": connections_made,
            "connectionCount": len(connections_made),
            "parametersApplied": params_applied
        }

        if errors:
            result["errors"] = errors

        return result
    except Exception as e:
        return _com_error(e, "block_template")



# Dangerous ExecuteMenuCommand IDs that must be blocked
BLOCKED_MENU_COMMANDS = {
    1: "Quit ExtendSim",
    2: "New model (discards current without save)",
    3: "Open model dialog (discards current without save)",
    4: "Close model without save",
}

# Dangerous ModL functions that must be blocked in execute_command
BLOCKED_FUNCTIONS = [
    "AbortSilent",   # Kills ExtendSim when called outside simulation
    "QuitExtendSim", # Terminates ExtendSim application
]


def execute_command(command: str, get_result: bool = False,
                    result_type: str = "number") -> dict:
    """Executes an arbitrary ExtendSim ModL command.

    Args:
        command: The ModL command to execute (e.g., 'ConArrayGetConNumber(17, "ValuesIn", 0);')
        get_result: If True, retrieve result from global0/globalStr0 after execution
        result_type: "number" to get from global0, "string" to get from globalStr0

    Examples:
        - execute_command('global0 = ConArrayGetNumCons(17, "valuesin");', get_result=True)
        - execute_command('SetBlockLabel(5, "MyLabel");')
        - execute_command('globalStr0 = GetModelName();', get_result=True, result_type="string")
    """
    try:
        app = get_extendsim_app()

        # Block dangerous ExecuteMenuCommand calls
        import re
        menu_match = re.search(r'ExecuteMenuCommand\s*\(\s*(\d+)\s*\)', command)
        if menu_match:
            menu_id = int(menu_match.group(1))
            if menu_id in BLOCKED_MENU_COMMANDS:
                return _error(ErrorCode.COMMAND_FAILED,
                              f"ExecuteMenuCommand({menu_id}) is blocked: {BLOCKED_MENU_COMMANDS[menu_id]}",
                              command=command,
                              suggestion="This command would terminate ExtendSim or close the model. Use model_close() instead.")

        # Block dangerous ModL functions
        for blocked_fn in BLOCKED_FUNCTIONS:
            if re.search(rf'\b{blocked_fn}\b', command, re.IGNORECASE):
                return _error(ErrorCode.COMMAND_FAILED,
                              f"Function '{blocked_fn}' is blocked for safety",
                              command=command,
                              suggestion=f"{blocked_fn}() can crash or kill ExtendSim. This function cannot be called via execute_command.")

        # Execute the command
        app.Execute(command)

        result = {
            "success": True,
            "command": command
        }

        # Optionally get result
        if get_result:
            if result_type == "string":
                result["result"] = app.Request("System", "globalStr0+:0:0:0")
            else:
                raw = app.Request("System", "global0+:0:0:0")
                result["resultRaw"] = raw
                try:
                    result["result"] = parse_float(raw)
                except Exception:
                    result["result"] = raw

        return result
    except Exception as e:
        return _error(ErrorCode.COMMAND_FAILED, str(e), command=command)


# ============================================================================
# ATTRIBUTE OPERATIONS (Set/Get blocks)
# ============================================================================


def attribute_set(block_id: int,
                  attribute_name: str,
                  value_type: str = "constant",
                  value: Optional[float] = None,
                  distribution: Optional[str] = None,
                  arg1: Optional[float] = None,
                  arg2: Optional[float] = None,
                  arg3: Optional[float] = None,
                  model_id: Optional[str] = None) -> dict:
    """Configures a Set block to assign an attribute value to items.

    ExtendSim 2024's Set block stores attribute assignments in the
    AttribsTable_ttbl dialog table, not the removed name/value-type/
    constant-value dialog variables from earlier versions. Delegates to the
    effect-verified, fail-closed attribute_config core. Currently only
    value_type="constant" is supported; other types return
    ATTRIBUTE_VALUETYPE_UNSUPPORTED.
    """
    import attribute_config
    import sys as _sys
    return attribute_config.set_attribute(
        _sys.modules[__name__], block_id, attribute_name, value, value_type)


def attribute_get(block_id: int,
                  attribute_name: str,
                  model_id: Optional[str] = None) -> dict:
    """Configures a Get block to read an attribute value from items.

    The Get block in ExtendSim (Item.lbr) reads an attribute from each item passing through
    and outputs its value on the value output connector.

    Args:
        block_id: Get block ID
        attribute_name: Name of the attribute to read (e.g., 'priority', 'customer_type')

    Returns:
        Dictionary with success status
    """
    try:
        app = get_extendsim_app()

        # Validate block type
        check = _validate_block_type(app, block_id, "Get")
        if not check.get("success"):
            return check

        # Set the attribute name to read (string value — use SetDialogVariable)
        _set_var_string(app, block_id, "AttributeName_prm", attribute_name)

        return {
            "success": True,
            "blockId": block_id,
            "attributeName": attribute_name
        }
    except Exception as e:
        return _error(ErrorCode.SET_VALUE_FAILED, str(e),
                      blockId=block_id, operation="attribute_get")


# ============================================================================
# ROUTING OPERATIONS (Select Item In / Select Item Out)
# ============================================================================

# Select Item Out routing modes
SELECT_OUT_MODE = {
    "random": 1,
    "sequential": 2,
    "probability": 3,
    "attribute": 4,
    "conditional": 5,
}

# Select Item In merge modes
SELECT_IN_MODE = {
    "first_available": 1,
    "priority": 2,
    "longest_waiting": 3,
}


def select_item_out_set_mode(block_id: int,
                              mode: str = "random",
                              attribute_name: Optional[str] = None,
                              probabilities: Optional[list] = None,
                              if_blocked: Optional[str] = None,
                              predict_path: Optional[bool] = None,
                              model_id: Optional[str] = None) -> dict:
    """Configures routing logic for a Select Item Out block.

    The Select Item Out block routes items to one of multiple output connectors
    based on the selected mode.

    Args:
        block_id: Select Item Out block ID
        mode: Routing mode: "random", "sequential", "conditional", "attribute", "probability"
        attribute_name: Attribute to route by (for mode="attribute")
        probabilities: List of probabilities for each output (for mode="probability")
        if_blocked: "tryUnblocked" or "wait" — behavior when selected output is blocked
        predict_path: Enable predict item path before entry

    Returns:
        Dictionary with success status
    """
    try:
        app = get_extendsim_app()

        # Validate block type
        check = _validate_block_type(app, block_id, "Select Item Out")
        if not check.get("success"):
            return check

        # Set routing mode
        mode_code = SELECT_OUT_MODE.get(mode.lower(), 1)
        _set_var(app, block_id, "SelectType_pop", mode_code)

        # Set attribute name for attribute-based routing (string value)
        if mode.lower() == "attribute" and attribute_name:
            _set_var_string(app, block_id, "AttribName_prm", attribute_name)

        # Set probabilities for probability-based routing (_dtbl → SetDialogVariable)
        if mode.lower() == "probability" and probabilities:
            for i, prob in enumerate(probabilities):
                _set_var(app, block_id, "ConnectorProb_dtbl", prob, i, 1)

        # v1.17.4 — Blocking behavior and predict path
        if if_blocked is not None:
            blocked_code = {"tryunblocked": 1, "wait": 2}.get(if_blocked.lower(), 1)
            _set_var(app, block_id, "SelectTo_pop", blocked_code)
        if predict_path is not None:
            _set_var(app, block_id, "Predict_chk", 1 if predict_path else 0)

        return {
            "success": True,
            "blockId": block_id,
            "mode": mode,
            "attributeName": attribute_name if mode.lower() == "attribute" else None,
            "probabilities": probabilities if mode.lower() == "probability" else None,
            "ifBlocked": if_blocked,
            "predictPath": predict_path
        }
    except Exception as e:
        return _error(ErrorCode.SET_VALUE_FAILED, str(e),
                      blockId=block_id, operation="select_item_out_set_mode")


def select_item_in_set_mode(block_id: int,
                             mode: str = "first_available",
                             model_id: Optional[str] = None) -> dict:
    """Configures merge logic for a Select Item In block.

    The Select Item In block selects which input to accept an item from
    when multiple inputs have items waiting.

    Args:
        block_id: Select Item In block ID
        mode: Selection mode: "first_available", "priority", "longest_waiting"

    Returns:
        Dictionary with success status
    """
    try:
        app = get_extendsim_app()

        # Validate block type
        check = _validate_block_type(app, block_id, "Select Item In")
        if not check.get("success"):
            return check

        # Set selection mode
        mode_code = SELECT_IN_MODE.get(mode.lower(), 1)
        _set_var(app, block_id, "SelectType_pop", mode_code)

        return {
            "success": True,
            "blockId": block_id,
            "mode": mode
        }
    except Exception as e:
        return _error(ErrorCode.SET_VALUE_FAILED, str(e),
                      blockId=block_id, operation="select_item_in_set_mode")


# ============================================================================
# DATABASE OPERATIONS
# ============================================================================

def _resolve_db_indices(app, db_name: str, table_name: Optional[str] = None,
                        field_name: Optional[str] = None) -> dict:
    """Resolves database/table/field names to numeric indices.

    Uses confirmed ModL functions: DBDatabaseGetIndex, DBTableGetIndex, DBFieldGetIndex.
    Returns dict with 'success' and index values, or error on failure.
    """
    # Resolve database index
    app.Execute(f'globalInt0 = DBDatabaseGetIndex("{db_name}");')
    raw = app.Request("System", "globalInt0+:0:0:0")
    db_idx = int(parse_float(raw))
    if db_idx < 0:
        return _error(ErrorCode.DATABASE_NOT_FOUND,
                      f"Database '{db_name}' not found", databaseName=db_name)

    result = {"success": True, "dbIdx": db_idx}

    if table_name is not None:
        app.Execute(f'globalInt0 = DBTableGetIndex({db_idx}, "{table_name}");')
        raw = app.Request("System", "globalInt0+:0:0:0")
        tbl_idx = int(parse_float(raw))
        if tbl_idx < 0:
            return _error(ErrorCode.TABLE_NOT_FOUND,
                          f"Table '{table_name}' not found in database '{db_name}'",
                          databaseName=db_name, tableName=table_name)
        result["tblIdx"] = tbl_idx

    if field_name is not None and table_name is not None:
        tbl_idx = result["tblIdx"]
        app.Execute(f'globalInt0 = DBFieldGetIndex({db_idx}, {tbl_idx}, "{field_name}");')
        raw = app.Request("System", "globalInt0+:0:0:0")
        fld_idx = int(parse_float(raw))
        if fld_idx < 0:
            return _error(ErrorCode.FIELD_NOT_FOUND,
                          f"Field '{field_name}' not found in table '{table_name}'",
                          databaseName=db_name, tableName=table_name, fieldName=field_name)
        result["fldIdx"] = fld_idx

    return result


def db_list(model_id: Optional[str] = None) -> dict:
    """Lists all databases and their tables in the current model.

    Uses DBDatabasesGetNum() + DBDatabaseGetName() for proper enumeration,
    then DBTablesGetNum() + DBTableGetName() for tables per DB.
    """
    try:
        app = get_extendsim_app()

        model_check = _validate_model_open(app)
        if not model_check.get("success"):
            return model_check

        # Get total number of databases
        app.Execute("globalInt0 = DBDatabasesGetNum();")
        num_dbs = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))

        databases = []
        for db_idx in range(num_dbs):
            app.Execute(f'globalStr0 = DBDatabaseGetName({db_idx});')
            db_name = app.Request("System", "globalStr0+:0:0:0")
            if not db_name:
                continue

            # Get number of tables in this DB
            app.Execute(f'globalInt0 = DBTablesGetNum({db_idx});')
            num_tables = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))

            tables = []
            for tbl_idx in range(num_tables):
                app.Execute(f'globalStr0 = DBTableGetName({db_idx}, {tbl_idx});')
                tbl_name = app.Request("System", "globalStr0+:0:0:0")

                app.Execute(f'globalInt0 = DBFieldsGetNum({db_idx}, {tbl_idx});')
                num_fields = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))

                app.Execute(f'globalInt0 = DBRecordsGetNum({db_idx}, {tbl_idx});')
                num_records = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))

                tables.append({
                    "name": tbl_name or f"table_{tbl_idx}",
                    "index": tbl_idx,
                    "records": num_records,
                    "fields": num_fields
                })

            databases.append({
                "name": db_name,
                "index": db_idx,
                "tableCount": len(tables),
                "tables": tables
            })

        return {"success": True, "databases": databases, "count": len(databases)}
    except Exception as e:
        return _com_error(e, "db_list")


def db_table_info(database_name: str, table_name: str,
                  model_id: Optional[str] = None) -> dict:
    """Gets schema information for a database table including field names and types.

    Uses DBFieldGetName() and DBFieldGetProperties(which=1) for field type.
    Type map: 0=real, 1=integer, 2=string, 3=boolean.
    """
    try:
        app = get_extendsim_app()

        indices = _resolve_db_indices(app, database_name, table_name)
        if not indices.get("success"):
            return indices

        db_idx = indices["dbIdx"]
        tbl_idx = indices["tblIdx"]

        # Get record count
        app.Execute(f'globalInt0 = DBRecordsGetNum({db_idx}, {tbl_idx});')
        num_records = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))

        # Get field count
        app.Execute(f'globalInt0 = DBFieldsGetNum({db_idx}, {tbl_idx});')
        num_fields = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))

        # Get field names and types
        fields = []
        for fld_idx in range(num_fields):
            app.Execute(f'globalStr0 = DBFieldGetName({db_idx}, {tbl_idx}, {fld_idx});')
            fld_name = app.Request("System", "globalStr0+:0:0:0")

            app.Execute(f'globalInt0 = DBFieldGetProperties({db_idx}, {tbl_idx}, {fld_idx}, 1);')
            fld_type_num = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))
            fld_type = DB_FIELD_TYPE_MAP.get(fld_type_num, f"unknown({fld_type_num})")

            fields.append({
                "name": fld_name or f"field_{fld_idx}",
                "index": fld_idx,
                "type": fld_type
            })

        return {
            "success": True,
            "databaseName": database_name,
            "tableName": table_name,
            "records": num_records,
            "fieldCount": num_fields,
            "fields": fields,
            "tableIndex": tbl_idx,
            "databaseIndex": db_idx
        }
    except Exception as e:
        return _com_error(e, "db_table_info")


def db_get_value(database_name: str, table_name: str, field_name: str,
                 record: int, as_string: bool = False,
                 model_id: Optional[str] = None) -> dict:
    """Reads a single cell value from a database table."""
    try:
        app = get_extendsim_app()

        indices = _resolve_db_indices(app, database_name, table_name, field_name)
        if not indices.get("success"):
            return indices

        db_idx = indices["dbIdx"]
        tbl_idx = indices["tblIdx"]
        fld_idx = indices["fldIdx"]

        if as_string:
            app.Execute(f'globalStr0 = DBDataGetAsString({db_idx}, {tbl_idx}, {fld_idx}, {record});')
            value = app.Request("System", "globalStr0+:0:0:0")
        else:
            app.Execute(f'global0 = DBDataGetAsNumber({db_idx}, {tbl_idx}, {fld_idx}, {record});')
            raw = app.Request("System", "global0+:0:0:0")
            value = parse_float(raw)

        return {
            "success": True,
            "databaseName": database_name,
            "tableName": table_name,
            "fieldName": field_name,
            "record": record,
            "value": value
        }
    except Exception as e:
        return _com_error(e, "db_get_value")


def db_set_value(database_name: str, table_name: str, field_name: str,
                 record: int, value, model_id: Optional[str] = None) -> dict:
    """Writes a single cell value to a database table."""
    try:
        app = get_extendsim_app()

        indices = _resolve_db_indices(app, database_name, table_name, field_name)
        if not indices.get("success"):
            return indices

        db_idx = indices["dbIdx"]
        tbl_idx = indices["tblIdx"]
        fld_idx = indices["fldIdx"]

        # Use Poke for writing: DB:#dbIdx:tblIdx:rec:fld:rec:fld
        poke_addr = f"DB:#{db_idx}:{tbl_idx}:{record}:{fld_idx}:{record}:{fld_idx}"
        app.Poke("System", poke_addr, str(value))

        return {
            "success": True,
            "databaseName": database_name,
            "tableName": table_name,
            "fieldName": field_name,
            "record": record,
            "value": value
        }
    except Exception as e:
        return _error(ErrorCode.DB_OPERATION_FAILED, str(e),
                      operation="db_set_value")


def db_get_records(database_name: str, table_name: str,
                   start_record: int = 0, end_record: Optional[int] = None,
                   fields: Optional[list] = None, max_records: int = 1000,
                   model_id: Optional[str] = None) -> dict:
    """Reads multiple records from a database table.

    Note: endRecord is EXCLUSIVE (reads up to but not including endRecord).
    For example, start_record=0, end_record=3 reads records 0, 1, 2.

    If 'fields' parameter is provided (list of field names), only those fields
    are read. Otherwise all fields are read by index (field_0, field_1, ...).
    """
    try:
        app = get_extendsim_app()

        indices = _resolve_db_indices(app, database_name, table_name)
        if not indices.get("success"):
            return indices

        db_idx = indices["dbIdx"]
        tbl_idx = indices["tblIdx"]

        # Get total records (confirmed ModL)
        app.Execute(f'globalInt0 = DBRecordsGetNum({db_idx}, {tbl_idx});')
        total_records = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))

        if end_record is None:
            end_record = min(start_record + max_records, total_records)
        end_record = min(end_record, total_records)
        end_record = min(end_record, start_record + max_records)

        # Get field count (confirmed ModL)
        app.Execute(f'globalInt0 = DBFieldsGetNum({db_idx}, {tbl_idx});')
        num_fields = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))

        # Resolve field indices
        field_indices = []
        field_names = []
        if fields:
            # User specified field names - resolve via DBFieldGetIndex (confirmed)
            for fname in fields:
                app.Execute(f'globalInt0 = DBFieldGetIndex({db_idx}, {tbl_idx}, "{fname}");')
                fidx = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))
                if fidx >= 0:
                    field_indices.append(fidx)
                    field_names.append(fname)
        else:
            # No field names specified - use indices as names
            for fidx in range(num_fields):
                field_indices.append(fidx)
                field_names.append(f"field_{fidx}")

        # Detect field types to choose correct read method
        # DBFieldGetProperties(which=1) returns different type codes:
        #   0=real, 1=integer, 2=string, 3=boolean (documented DB_FIELD_TYPE_MAP)
        #   -1=linked/lookup field (string), 16384=string, 16385=linked string
        # Treat anything that's not clearly numeric as string to be safe
        NUMERIC_FIELD_TYPES = {0, 1, 3, 8192, 4096}  # real, integer, boolean, real(alt), integer(alt)
        field_types = {}  # fldIdx -> "string" | "numeric"
        for fidx in field_indices:
            try:
                app.Execute(f'globalInt0 = DBFieldGetProperties({db_idx}, {tbl_idx}, {fidx}, 1);')
                ftype = int(parse_float(app.Request("System", "globalInt0+:0:0:0")) or 0)
                field_types[fidx] = "numeric" if ftype in NUMERIC_FIELD_TYPES else "string"
            except Exception:
                field_types[fidx] = "numeric"  # Safe default

        # Read records using correct method per field type
        records = []
        for rec in range(start_record, end_record):
            row = {}
            for fidx, fname in zip(field_indices, field_names):
                if field_types.get(fidx) == "string":
                    app.Execute(f'globalStr0 = DBDataGetAsString({db_idx}, {tbl_idx}, {fidx}, {rec});')
                    row[fname] = app.Request("System", "globalStr0+:0:0:0")
                else:
                    app.Execute(f'global0 = DBDataGetAsNumber({db_idx}, {tbl_idx}, {fidx}, {rec});')
                    raw = app.Request("System", "global0+:0:0:0")
                    row[fname] = parse_float(raw)
            records.append(row)

        return {
            "success": True,
            "databaseName": database_name,
            "tableName": table_name,
            "startRecord": start_record,
            "endRecord": end_record,
            "totalRecords": total_records,
            "fields": field_names,
            "records": records,
            "count": len(records)
        }
    except Exception as e:
        return _com_error(e, "db_get_records")


def db_add_records(database_name: str, table_name: str,
                   count: int = 1, position: Optional[int] = None,
                   model_id: Optional[str] = None) -> dict:
    """Adds records to a database table."""
    try:
        app = get_extendsim_app()

        indices = _resolve_db_indices(app, database_name, table_name)
        if not indices.get("success"):
            return indices

        db_idx = indices["dbIdx"]
        tbl_idx = indices["tblIdx"]

        # Get current record count
        app.Execute(f'globalInt0 = DBRecordsGetNum({db_idx}, {tbl_idx});')
        before_count = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))

        insert_pos = position if position is not None else before_count
        app.Execute(f'DBRecordsInsert({db_idx}, {tbl_idx}, {insert_pos}, {count});')

        # Get new record count
        app.Execute(f'globalInt0 = DBRecordsGetNum({db_idx}, {tbl_idx});')
        after_count = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))

        return {
            "success": True,
            "databaseName": database_name,
            "tableName": table_name,
            "addedCount": count,
            "position": insert_pos,
            "previousRecordCount": before_count,
            "newRecordCount": after_count
        }
    except Exception as e:
        return _error(ErrorCode.DB_OPERATION_FAILED, str(e),
                      operation="db_add_records")


def db_delete_records(database_name: str, table_name: str,
                      start_record: int, end_record: int,
                      model_id: Optional[str] = None) -> dict:
    """Deletes records from a database table.

    Note: endRecord is INCLUSIVE (deletes through and including endRecord).
    For example, start_record=0, end_record=2 deletes records 0, 1, 2.
    This differs from db_get_records where endRecord is exclusive.
    """
    try:
        app = get_extendsim_app()

        indices = _resolve_db_indices(app, database_name, table_name)
        if not indices.get("success"):
            return indices

        db_idx = indices["dbIdx"]
        tbl_idx = indices["tblIdx"]

        # Get current record count
        app.Execute(f'globalInt0 = DBRecordsGetNum({db_idx}, {tbl_idx});')
        before_count = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))

        app.Execute(f'DBRecordsDelete({db_idx}, {tbl_idx}, {start_record}, {end_record});')

        # Get new record count
        app.Execute(f'globalInt0 = DBRecordsGetNum({db_idx}, {tbl_idx});')
        after_count = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))

        return {
            "success": True,
            "databaseName": database_name,
            "tableName": table_name,
            "startRecord": start_record,
            "endRecord": end_record,
            "deletedCount": before_count - after_count,
            "previousRecordCount": before_count,
            "newRecordCount": after_count
        }
    except Exception as e:
        return _error(ErrorCode.DB_OPERATION_FAILED, str(e),
                      operation="db_delete_records")


# ============================================================================
# BATCH / UNBATCH OPERATIONS
# ============================================================================

# Batch type constants
BATCH_TYPE = {
    "batch": 1,
    "match": 2,
}


def batch_set_config(block_id: int,
                     batch_type: Optional[str] = None,
                     batch_size: Optional[int] = None,
                     preserve_uniqueness: Optional[bool] = None,
                     match_attribute: Optional[str] = None,
                     # v1.17.4.4 — demand connector, zero batch size, batch timing
                     show_demand_connector: Optional[bool] = None,
                     demand_connector_value: Optional[int] = None,
                     allow_zero_batch_size: Optional[bool] = None,
                     batch_size_when: Optional[int] = None,
                     model_id: Optional[str] = None) -> dict:
    """Configures a Batch block.

    Args:
        block_id: Batch block ID
        batch_type: "batch" (combine items) or "match" (match by attribute)
        batch_size: Number of items to batch together
        preserve_uniqueness: Whether to preserve item uniqueness
        match_attribute: Attribute name to match by (for batch_type="match")
    """
    try:
        app = get_extendsim_app()

        check = _validate_block_type(app, block_id, "Batch")
        if not check.get("success"):
            return check

        if batch_type is not None:
            bt = BATCH_TYPE.get(batch_type.lower(), 1)
            _set_var(app, block_id, "BatchType_pop", bt)

        if batch_size is not None:
            # Batch size is in the Quantity table, row 0
            # Note: Quantity_tbl has no recognized suffix — routed to SetVariableNumeric
            _set_var(app, block_id, "Quantity_tbl", batch_size)

        if preserve_uniqueness is not None:
            val = 1 if preserve_uniqueness else 0
            _set_var(app, block_id, "Preserve_chk", val)

        if match_attribute is not None and batch_type and batch_type.lower() == "match":
            _set_var_string(app, block_id, "MatchAttrib_prm", match_attribute)

        # v1.17.4.4 — demand connector, zero batch size, batch timing
        if show_demand_connector is not None:
            _set_var(app, block_id, "ShowDemandCon_chk", 1 if show_demand_connector else 0)
        if demand_connector_value is not None:
            _set_var(app, block_id, "ValueDemandConnector_pop", demand_connector_value)
        if allow_zero_batch_size is not None:
            _set_var(app, block_id, "AllowZeroBatchSize_chk", 1 if allow_zero_batch_size else 0)
        if batch_size_when is not None:
            _set_var(app, block_id, "BatchSizeWhen_pop", batch_size_when)

        return {
            "success": True,
            "blockId": block_id,
            "batchType": batch_type,
            "batchSize": batch_size,
            "preserveUniqueness": preserve_uniqueness,
            "matchAttribute": match_attribute,
            "showDemandConnector": show_demand_connector,
            "demandConnectorValue": demand_connector_value,
            "allowZeroBatchSize": allow_zero_batch_size,
            "batchSizeWhen": batch_size_when
        }
    except Exception as e:
        return _error(ErrorCode.SET_VALUE_FAILED, str(e),
                      blockId=block_id, operation="batch_set_config")


def unbatch_set_config(block_id: int,
                       preserve_uniqueness: Optional[bool] = None,
                       quantity_per_output: Optional[int] = None,
                       # v1.17.4.4 — cost type, preserved quantity, duplicate, qty output
                       cost_type: Optional[int] = None,
                       use_preserved_quantity: Optional[bool] = None,
                       duplicate_preserved: Optional[bool] = None,
                       quantity_out: Optional[bool] = None,
                       model_id: Optional[str] = None) -> dict:
    """Configures an Unbatch block.

    Args:
        block_id: Unbatch block ID
        preserve_uniqueness: Whether to preserve item uniqueness
        quantity_per_output: Number of items per output
    """
    try:
        app = get_extendsim_app()

        check = _validate_block_type(app, block_id, "Unbatch")
        if not check.get("success"):
            return check

        if preserve_uniqueness is not None:
            val = 1 if preserve_uniqueness else 0
            _set_var(app, block_id, "Preserve_chk", val)

        if quantity_per_output is not None:
            _set_var(app, block_id, "UnbatchQty_tbl", quantity_per_output)

        # v1.17.4.4 — cost type, preserved quantity, duplicate, qty output
        if cost_type is not None:
            _set_var(app, block_id, "UnbatchCostType_pop", cost_type)
        if use_preserved_quantity is not None:
            _set_var(app, block_id, "UsePreservedQuantity_chk", 1 if use_preserved_quantity else 0)
        if duplicate_preserved is not None:
            _set_var(app, block_id, "DuplicatePreserved_chk", 1 if duplicate_preserved else 0)
        if quantity_out is not None:
            _set_var(app, block_id, "UnbatchQtyOut_chk", 1 if quantity_out else 0)

        return {
            "success": True,
            "blockId": block_id,
            "preserveUniqueness": preserve_uniqueness,
            "quantityPerOutput": quantity_per_output,
            "costType": cost_type,
            "usePreservedQuantity": use_preserved_quantity,
            "duplicatePreserved": duplicate_preserved,
            "quantityOut": quantity_out
        }
    except Exception as e:
        return _error(ErrorCode.SET_VALUE_FAILED, str(e),
                      blockId=block_id, operation="unbatch_set_config")


# ============================================================================
# RESOURCE POOL OPERATIONS
# ============================================================================

# Resource allocation rule constants
RESOURCE_ALLOC_RULE = {
    "random": 1,
    "priority": 2,
    "cyclical": 3,
    "longest_idle": 4,
}


def resource_pool_set_config(block_id: int, pool_name=None, initial_resources=None,
                             allocation_rule=None, model_id=None) -> dict:
    """Configure a Resource Pool block (name + capacity), effect-verified.

    Delegates to the fail-closed resource_pool_config core. allocation_rule is
    applied directly (AllocRule popup) after the verified name/capacity write."""
    import resource_pool_config, sys as _sys
    name = pool_name if pool_name is not None else ""
    cap = initial_resources if initial_resources is not None else 1
    res = resource_pool_config.configure_pool(_sys.modules[__name__], block_id, name, cap)
    if res.get("success") and allocation_rule is not None:
        try:
            app = get_extendsim_app()
            _set_var(app, block_id, "AllocRule", RESOURCE_ALLOC_RULE.get(allocation_rule.lower(), 1))
        except Exception as e:
            # Surface the failure instead of swallowing it (no silent false-success).
            res["allocationRuleWarning"] = f"allocation_rule not applied: {e}"
    return res


def resource_pool_get_stats(block_id: int,
                            model_id: Optional[str] = None) -> dict:
    """Reads statistics from a Resource Pool block.

    Returns utilization, available count, and in-use count.
    """
    try:
        app = get_extendsim_app()

        check = _validate_block_type(app, block_id, "Resource Pool")
        if not check.get("success"):
            return check

        # Read statistics using GetVariableNumeric (real values, not shadow)
        utilization = parse_float(_get_var(app, block_id, "Utilization_prm"))
        available = parse_float(_get_var(app, block_id, "NumAvailable_prm"))
        in_use = parse_float(_get_var(app, block_id, "NumInUse_prm"))
        total = parse_float(_get_var(app, block_id, "NumServ"))

        return {
            "success": True,
            "blockId": block_id,
            "utilization": utilization,
            "available": int(available),
            "inUse": int(in_use),
            "totalResources": int(total)
        }
    except Exception as e:
        return _com_error(e, "resource_pool_get_stats")


def resource_pool_release_set_config(block_id: int, pool_name: Optional[str] = None,
                                     release_quantity: Optional[int] = None,
                                     model_id=None) -> dict:
    """Configure a Resource Pool Release block: link it to its pool + release qty.

    Setting the pool is REQUIRED — without it ExtendSim aborts the simulation at
    t=0 (CHECKDATA). The pool block is resolved by name (find_resource_pool).
    Delegates to the fail-closed core."""
    import resource_pool_config, sys as _sys
    qty = release_quantity if release_quantity is not None else 1
    if pool_name is None:
        return _error(ErrorCode.SET_VALUE_FAILED,
                      "pool_name is required for a Resource Pool Release block",
                      blockId=block_id, operation="resource_pool_release_set_config")
    return resource_pool_config.configure_release(
        _sys.modules[__name__], block_id, pool_name, pool_block_id=None, qty=qty)


def find_resource_pool(app, pool_name: str) -> int:
    """Return the block id of the Resource Pool whose ResourcePoolName == pool_name,
    or -1 if none. Mirrors the block's own FindRPBlock (scan all blocks by name)."""
    try:
        blocks = block_list(detail="summary").get("blocks", [])
    except Exception:
        return -1
    for b in blocks:
        if b.get("blockName") == "Resource Pool":
            bid = b.get("blockId")
            try:
                if str(_get_dialog_string(app, bid, "ResourcePoolName")) == str(pool_name):
                    return bid
            except Exception:
                continue
    return -1


def queue_set_resource_pool(block_id: int, resource_pool_block_id: int,
                            resources_needed: int = 1, model_id=None) -> dict:
    """Put a Queue in Resource Pool mode and point it at the given Resource Pool.

    The Queue references the pool by NAME (read off the pool block), written into
    the ResourceTable dialog string-table. Delegates to the fail-closed core."""
    import resource_pool_config, sys as _sys
    app = get_extendsim_app()
    pool_name = _get_dialog_string(app, resource_pool_block_id, "ResourcePoolName")
    if not pool_name or str(pool_name) in ("", "-nan(ind)"):
        return _error(ErrorCode.SET_VALUE_FAILED,
                      f"Resource Pool block {resource_pool_block_id} has no name to reference",
                      blockId=block_id, operation="queue_set_resource_pool")
    return resource_pool_config.configure_queue_pool(_sys.modules[__name__], block_id,
                                                     str(pool_name), resources_needed)


# ============================================================================
# SIMULATION SETUP OPERATIONS
# ============================================================================


def simulation_setup_get(model_id: Optional[str] = None) -> dict:
    """Reads simulation setup parameters.

    Uses GetRunParameter(which) for all parameters (GetRunParameterLong
    does not work reliably via COM).
    which: 1=endTime, 2=startTime, 3=numSims, 4=numSteps, 5=seed,
           6=seedControl, 8=timeUnits
    """
    try:
        app = get_extendsim_app()

        model_check = _validate_model_open(app)
        if not model_check.get("success"):
            return model_check

        app.Execute("global0 = GetRunParameter(1);")
        end_time = parse_float(app.Request("System", "global0+:0:0:0"))

        app.Execute("global0 = GetRunParameter(2);")
        start_time = parse_float(app.Request("System", "global0+:0:0:0"))

        app.Execute("global0 = GetRunParameter(3);")
        num_sims = int(parse_float(app.Request("System", "global0+:0:0:0")))

        app.Execute("global0 = GetRunParameter(4);")
        num_steps = int(parse_float(app.Request("System", "global0+:0:0:0")))

        app.Execute("global0 = GetRunParameter(5);")
        seed = int(parse_float(app.Request("System", "global0+:0:0:0")))

        app.Execute("global0 = GetRunParameter(6);")
        seed_control = int(parse_float(app.Request("System", "global0+:0:0:0")))

        app.Execute("global0 = GetRunParameter(8);")
        time_units = int(parse_float(app.Request("System", "global0+:0:0:0")))

        return {
            "success": True,
            "endTime": end_time,
            "startTime": start_time,
            "numberOfRuns": num_sims,
            "numberOfSteps": num_steps,
            "randomSeed": seed,
            "seedControl": seed_control,
            "timeUnits": time_units
        }
    except Exception as e:
        return _com_error(e, "simulation_setup_get")


def simulation_setup_set(end_time: Optional[float] = None,
                         start_time: Optional[float] = None,
                         number_of_runs: Optional[int] = None,
                         random_seed: Optional[int] = None,
                         seed_control: Optional[int] = None,
                         time_units: Optional[int] = None,
                         delta_time: Optional[float] = None,
                         num_steps: Optional[int] = None,
                         simulation_order: Optional[int] = None,
                         model_id: Optional[str] = None) -> dict:
    """Sets simulation setup parameters.

    Uses SetRunParameter(value, which) for each parameter.
    which: 1=endTime, 2=startTime, 3=numSims, 5=seed, 6=seedControl
    Also supports: timeUnits (via SetTimeUnits), deltaTime, numSteps (via SetRunParameter),
    simulationOrder (via SetModelSimulationOrder: 0=left-to-right, 2=flow, 3=custom).
    """
    try:
        app = get_extendsim_app()

        model_check = _validate_model_open(app)
        if not model_check.get("success"):
            return model_check

        changed = {}

        if end_time is not None:
            app.Execute(f"SetRunParameter({end_time}, 1);")
            changed["endTime"] = end_time

        if start_time is not None:
            app.Execute(f"SetRunParameter({start_time}, 2);")
            changed["startTime"] = start_time

        if number_of_runs is not None:
            app.Execute(f"SetRunParameter({number_of_runs}, 3);")
            changed["numberOfRuns"] = number_of_runs

        if random_seed is not None:
            app.Execute(f"SetRunParameter({random_seed}, 5);")
            changed["randomSeed"] = random_seed

        if seed_control is not None:
            app.Execute(f"SetRunParameter({seed_control}, 6);")
            changed["seedControl"] = seed_control

        if time_units is not None:
            app.Execute(f"SetTimeUnits({time_units});")
            changed["timeUnits"] = time_units

        if delta_time is not None:
            app.Execute(f"SetRunParameter({delta_time}, 4);")
            changed["deltaTime"] = delta_time

        if num_steps is not None:
            app.Execute(f"SetRunParameter({num_steps}, 7);")
            changed["numSteps"] = num_steps

        # v1.17.4.5 — simulation order for continuous models
        if simulation_order is not None:
            app.Execute(f"SetModelSimulationOrder({simulation_order});")
            changed["simulationOrder"] = simulation_order

        # Validate StartTime < EndTime when both are provided or changed
        effective_start = start_time
        effective_end = end_time
        if effective_start is None or effective_end is None:
            # Read current values for validation
            app.Execute("global0 = GetRunParameter(2);")
            current_start = parse_float(app.Request("System", "global0+:0:0:0"))
            app.Execute("global0 = GetRunParameter(1);")
            current_end = parse_float(app.Request("System", "global0+:0:0:0"))
            if effective_start is None:
                effective_start = current_start
            if effective_end is None:
                effective_end = current_end

        if effective_end is not None and effective_end < 0:
            return _error(ErrorCode.INVALID_PARAMETER,
                          f"End time cannot be negative (got {effective_end}).",
                          suggestion="Use a positive end time value.")

        if (effective_start is not None and effective_end is not None
                and effective_start >= effective_end):
            return _error(ErrorCode.INVALID_PARAMETER,
                          f"Start time ({effective_start}) must be less than end time ({effective_end}).",
                          suggestion="Set start_time < end_time. Typical: startTime=0, endTime=1000.")

        return {
            "success": True,
            "changed": changed
        }
    except Exception as e:
        return _com_error(e, "simulation_setup_set")


# ============================================================================
# BLOCK STATISTICS OPERATIONS
# ============================================================================

# Mapping of block type -> {friendly_name: dialog_variable_name}
BLOCK_STAT_VARS = {
    "Activity": {
        "utilization": "Utilization_prm",
        "averageLength": "AverageLength_prm",
        "averageWait": "AverageWait_prm",
        "totalItemsEntered": "TotalItemsEntered_prm",
        "totalItemsExited": "TotalItemsExited_prm",
        "currentLength": "Length_prm",
        "maxLength": "MaxLengthObserved_prm",
        "currentWait": "Wait_prm",
        "maxWait": "MaxWait_prm",
        "totalCost": "TotalBlockCosts_prm",
        "percentBusy": "ASS_PercentBusy_prm",
        "percentIdle": "ASS_PercentIdle_prm",
        "percentBlocked": "ASS_PercentBlocked_prm",
        "percentDown": "ASS_PercentDown_prm",
        "percentOffshift": "ASS_PercentOffshift_prm",
        "totalPreempted": "PE_TotalItemsPreempted_prm",
        "totalShutdown": "SD_TotalItemsShutdown_prm",
    },
    "Queue": {
        "currentLength": "QueueLength_prm",
        "averageLength": "AveLength_prm",
        "averageWait": "AveWait_prm",
        "maxLength": "MaxLength_prm",
        "maxWait": "MaxWait_prm",
        "arrivals": "Arrivals_prm",
        "departures": "Departures_prm",
        "utilization": "Utilization_prm",
        "totalCost": "TotalBlockCosts_prm",
        "currentWait": "CurrentWait_prm",
    },
    "Exit": {
        "totalExited": "TotalExited_prm",
    },
    "Create": {
        "totalCreated": "RndI_TotalQuantity_prm",
    },
    "Resource Pool": {
        "utilization": "Utilization_prm",
        "numInUse": "NumInUse_prm",
        "numAvailable": "NumAvailable_prm",
        "queueLength": "QueueLength_prm",
        "queueLengthAverage": "QueueLengthAve_prm",
        "averageWait": "AveWait_prm",
    },
    "Workstation": {
        "utilization": "Utilization_Prm",
        "numInService": "NumInService_Prm",
        "queueLength": "QueueLength_Prm",
        "queueAveLength": "QueueAveLength_Prm",
        "queueMaxLength": "QueueMaxLength_Prm",
        "queueAveWait": "QueueAveWait_Prm",
        "queueMaxWait": "QueueMaxWait_Prm",
        "activityCost": "ActivityCost_prm",
        "queueCost": "QueueCost_prm",
    },
    "Tank": {
        "contents": "Contents_prm",
        "contentsAverage": "ContentsAverage_prm",
        "contentsMin": "ContentsMin_prm",
        "contentsMax": "ContentsMax_prm",
        "effectiveInputRate": "EffectiveInputRate_prm",
        "effectiveOutputRate": "EffectiveOutputRate_prm",
        "contentsByPercentage": "ContentsByPercentage_prm",
    },
    "Valve": {
        "utilization": "Utilization_prm",
        "effectiveRate": "EffectiveRate_prm",
        "quantity": "Quantity_prm",
        "percentBusy": "VSS_PercentBusy_prm",
        "percentIdle": "VSS_PercentIdle_prm",
        "percentDown": "VSS_PercentDown_prm",
        "percentOffshift": "VSS_PercentOffshift_prm",
    },
    "Merge": {
        "effectiveRate": "EffectiveRate_prm",
        "quantity": "Quantity_prm",
    },
    "Diverge": {
        "effectiveRate": "EffectiveRate_prm",
        "quantity": "Quantity_prm",
    },
    "Interchange": {
        "contents": "Contents_prm",
        "effectiveInputRate": "EffectiveInputRate_prm",
        "effectiveOutputRate": "EffectiveOutputRate_prm",
        "arrivals": "Arrivals_prm",
        "departures": "Departures_prm",
    },
    "Convey Flow": {
        "contents": "Contents_prm",
        "effectiveInputRate": "EffectiveInputRate_prm",
        "effectiveOutputRate": "EffectiveOutputRate_prm",
        "effectiveSpeed": "EffectiveSpeed_prm",
        "accumulatedQuantity": "AccumulatedQtity_prm",
    },
    "Change Units": {
        "effectiveRate": "EffectiveRate_prm",
        "quantity": "Quantity_prm",
    },
    "Bias": {
        "effectiveRate": "EffectiveRate_prm",
        "quantity": "Quantity_prm",
    },
    "Information": {
        "aveCycleTime": "AveCycleTime_prm",
        "aveTimeBetween": "AveTimeBetween_prm",
        "count": "Count_prm",
        "throughputRate": "ThroughputRate_prm",
        "cycleTime": "CycleTime_prm",
        "minCycleTime": "MinCycleTime_prm",
        "maxCycleTime": "MaxCycleTime_prm",
        "timeBetween": "TimeBetween_prm",
        "minTimeBetween": "MinTimeBetween_prm",
        "maxTimeBetween": "MaxTimeBetween_prm",
    },
    "Mean & Variance": {
        "mean": "MeanVal",
        "variance": "VarVal",
        "stdDev": "SDev",
        "confidenceInterval": "CI_prm",
        "ciError": "CIerror_prm",
    },
}


def block_get_stats(block_id: int, model_id: Optional[str] = None) -> dict:
    """Reads all statistics variables for a block based on its type.

    Uses BLOCK_STAT_VARS to determine which variables to read.
    Individual variable read failures return null (not error).
    """
    try:
        app = get_extendsim_app()

        # Get block type
        app.Execute(f"globalStr0 = BlockName({block_id});")
        block_name = app.Request("System", "globalStr0+:0:0:0")
        if not block_name:
            return _error(ErrorCode.BLOCK_NOT_FOUND,
                         f"Block {block_id} not found", blockId=block_id)

        stat_vars = BLOCK_STAT_VARS.get(block_name)
        if stat_vars is None:
            return _error(ErrorCode.WRONG_BLOCK_TYPE,
                         f"No statistics defined for block type '{block_name}'",
                         blockId=block_id, blockType=block_name,
                         supportedTypes=list(BLOCK_STAT_VARS.keys()))

        # Get block label
        app.Execute(f'globalStr0 = GetBlockLabel({block_id});')
        label = app.Request("System", "globalStr0+:0:0:0") or str(block_id)

        stats = {}
        for friendly_name, dialog_var in stat_vars.items():
            try:
                val = parse_float_nullable(_get_var(app, block_id, dialog_var))
                stats[friendly_name] = val
            except Exception:
                stats[friendly_name] = None

        return {
            "success": True,
            "blockId": block_id,
            "blockType": block_name,
            "label": label,
            "statistics": stats
        }
    except Exception as e:
        return _com_error(e, "block_get_stats")


def simulation_get_block_stats(block_ids: list,
                                model_id: Optional[str] = None) -> dict:
    """Reads statistics for multiple blocks at once.

    Calls block_get_stats for each blockId. Returns results for all blocks,
    even if individual blocks fail.
    """
    try:
        results = []
        for bid in block_ids:
            result = block_get_stats(bid, model_id)
            results.append(result)

        return {
            "success": True,
            "blocks": results,
            "count": len(results)
        }
    except Exception as e:
        return _com_error(e, "simulation_get_block_stats")


# ============================================================================
# MULTI-RUN AND SCENARIO OPERATIONS
# ============================================================================

import math


def _compute_stats(values: list) -> dict:
    """Computes summary statistics for a list of numeric values.

    Returns mean, min, max, stdDev, count.
    """
    n = len(values)
    if n == 0:
        return {"mean": 0, "min": 0, "max": 0, "stdDev": 0, "count": 0}

    mean = sum(values) / n
    min_val = min(values)
    max_val = max(values)

    if n > 1:
        variance = sum((x - mean) ** 2 for x in values) / (n - 1)
        std_dev = math.sqrt(variance)
    else:
        std_dev = 0.0

    return {
        "mean": round(mean, 6),
        "min": round(min_val, 6),
        "max": round(max_val, 6),
        "stdDev": round(std_dev, 6),
        "count": n
    }


def _aggregate_results(all_results: list) -> dict:
    """Aggregates per-run simulation results into summary statistics.

    Takes a list of simulation_get_results() outputs and computes
    statistics across runs for each block.
    """
    if not all_results:
        return {}

    aggregated = {
        "exitStatistics": {},
        "queueStatistics": {},
        "activityStatistics": {},
        "createStatistics": {}
    }

    for run_result in all_results:
        # Aggregate exit statistics
        for stat in run_result.get("exitStatistics", []):
            bid = stat.get("blockId")
            if bid not in aggregated["exitStatistics"]:
                aggregated["exitStatistics"][bid] = {
                    "blockId": bid, "label": stat.get("label", ""),
                    "itemsExited": []
                }
            val = stat.get("itemsExited", 0)
            if val is not None and val != -1:
                aggregated["exitStatistics"][bid]["itemsExited"].append(val)

        # Aggregate queue statistics
        for stat in run_result.get("queueStatistics", []):
            bid = stat.get("blockId")
            if bid not in aggregated["queueStatistics"]:
                aggregated["queueStatistics"][bid] = {
                    "blockId": bid, "label": stat.get("label", ""),
                    "averageLength": [], "maxLength": [],
                    "averageWaitTime": [], "currentLength": []
                }
            for key in ["averageLength", "maxLength", "averageWaitTime", "currentLength"]:
                val = stat.get(key, 0)
                if val is not None and val != -1:
                    aggregated["queueStatistics"][bid][key].append(val)

        # Aggregate activity statistics
        for stat in run_result.get("activityStatistics", []):
            bid = stat.get("blockId")
            if bid not in aggregated["activityStatistics"]:
                aggregated["activityStatistics"][bid] = {
                    "blockId": bid, "label": stat.get("label", ""),
                    "utilization": [], "averageWait": [],
                    "averageLength": [], "itemsEntered": []
                }
            for key, src_key in [("utilization", "utilization"),
                                  ("averageWait", "averageWait"),
                                  ("averageLength", "averageLength"),
                                  ("itemsEntered", "itemsEntered")]:
                val = stat.get(src_key, 0)
                if val is not None and val != -1:
                    aggregated["activityStatistics"][bid][key].append(val)

        # Aggregate create statistics
        for stat in run_result.get("createStatistics", []):
            bid = stat.get("blockId")
            if bid not in aggregated["createStatistics"]:
                aggregated["createStatistics"][bid] = {
                    "blockId": bid, "label": stat.get("label", ""),
                    "itemsCreated": []
                }
            val = stat.get("itemsCreated", 0)
            if val is not None and val != -1:
                aggregated["createStatistics"][bid]["itemsCreated"].append(val)

    # Compute statistics for each collected metric
    summary = {}
    for category, blocks in aggregated.items():
        summary[category] = []
        for bid, data in blocks.items():
            block_summary = {"blockId": data["blockId"], "label": data["label"]}
            for key, values in data.items():
                if isinstance(values, list):
                    block_summary[key] = _compute_stats(values)
            summary[category].append(block_summary)

    return summary


def simulation_run_multi(number_of_runs: int,
                         model_id: Optional[str] = None,
                         end_time: Optional[float] = None,
                         random_seed: Optional[int] = None,
                         run_mode: str = "normal",
                         collect_per_run: bool = True,
                         block_ids: Optional[list] = None) -> dict:
    """Runs N simulation replications and aggregates results.

    Does NOT use ExtendSim's built-in multi-run. Instead loops:
    1. Set single-run mode (SetRunParameter(1, 3))
    2. For each run: ExecuteMenuCommand(6000) -> collect results
    3. Aggregate with _compute_stats
    """
    try:
        app = get_extendsim_app()

        model_check = _validate_model_open(app)
        if not model_check.get("success"):
            return model_check

        if number_of_runs < 1 or number_of_runs > 100:
            return _error(ErrorCode.INVALID_PARAMETER,
                         "numberOfRuns must be between 1 and 100",
                         numberOfRuns=number_of_runs)

        # Ensure single-run mode
        app.Execute("SetRunParameter(1, 3);")

        if end_time is not None:
            app.Execute(f"endTime = {end_time};")

        if random_seed is not None:
            app.Execute(f"SetRunParameter({random_seed}, 5);")

        per_run_results = []
        per_run_block_stats = []
        errors = []

        for i in range(number_of_runs):
            try:
                # Run simulation
                app.Execute("ExecuteMenuCommand(6000)")

                # Collect standard results
                run_result = simulation_get_results(model_id)
                if run_result.get("success"):
                    per_run_results.append(run_result)
                else:
                    errors.append({"run": i + 1, "error": run_result.get("error", "Unknown")})

                # Collect detailed block stats if blockIds specified
                if block_ids and collect_per_run:
                    block_stats = simulation_get_block_stats(block_ids, model_id)
                    per_run_block_stats.append(block_stats)

            except Exception as e:
                errors.append({"run": i + 1, "error": str(e)})

        # Aggregate results
        aggregated = _aggregate_results(per_run_results) if per_run_results else {}

        result = {
            "success": True,
            "numberOfRuns": number_of_runs,
            "completedRuns": len(per_run_results),
            "aggregated": aggregated
        }

        if collect_per_run:
            result["perRunResults"] = per_run_results

        if per_run_block_stats:
            result["perRunBlockStats"] = per_run_block_stats

        if errors:
            result["errors"] = errors

        return result
    except Exception as e:
        return _error(ErrorCode.MULTI_RUN_FAILED, str(e))


def simulation_run_scenarios(block_id: int,
                              dialog_variable: str,
                              values: list,
                              model_id: Optional[str] = None,
                              end_time: Optional[float] = None,
                              run_mode: str = "normal") -> dict:
    """Runs simulation with different parameter values for comparison.

    For each value in values[]:
    1. Set variable value (using suffix-based API routing)
    2. Run simulation
    3. Collect results

    Max 20 scenarios to prevent excessive runtime.
    """
    try:
        app = get_extendsim_app()

        model_check = _validate_model_open(app)
        if not model_check.get("success"):
            return model_check

        if len(values) > 20:
            return _error(ErrorCode.INVALID_PARAMETER,
                         "Maximum 20 scenarios allowed",
                         requestedCount=len(values))

        if len(values) == 0:
            return _error(ErrorCode.INVALID_PARAMETER,
                         "At least one value is required")

        # Ensure single-run mode
        app.Execute("SetRunParameter(1, 3);")

        if end_time is not None:
            app.Execute(f"endTime = {end_time};")

        scenarios = []
        errors = []

        for i, value in enumerate(values):
            try:
                # Set the parameter value using suffix-based API routing
                _set_var(app, block_id, dialog_variable, value)

                # Run simulation
                app.Execute("ExecuteMenuCommand(6000)")

                # Collect results
                run_result = simulation_get_results(model_id)
                scenarios.append({
                    "scenarioIndex": i,
                    "parameterValue": value,
                    "results": run_result
                })

            except Exception as e:
                errors.append({
                    "scenarioIndex": i,
                    "parameterValue": value,
                    "error": str(e)
                })

        result = {
            "success": True,
            "blockId": block_id,
            "dialogVariable": dialog_variable,
            "scenarioCount": len(values),
            "completedScenarios": len(scenarios),
            "scenarios": scenarios
        }

        if errors:
            result["errors"] = errors

        return result
    except Exception as e:
        return _error(ErrorCode.MULTI_RUN_FAILED, str(e),
                      blockId=block_id, dialogVariable=dialog_variable)


# ============================================================================
# v1.3 TOOLS: WORKSTATION, EQUATION, SHIFT, TRANSPORT, CONVEY ITEM, SHUTDOWN
# ============================================================================

def workstation_set_config(block_id: int,
                           max_servers: Optional[int] = None,
                           max_queue_length: Optional[int] = None,
                           delay_type: str = "fixed",
                           distribution: Optional[str] = None,
                           arg1: Optional[float] = None,
                           arg2: Optional[float] = None,
                           arg3: Optional[float] = None,
                           value: Optional[float] = None,
                           cost_per_time: Optional[float] = None,
                           cost_per_item: Optional[float] = None,
                           model_id: Optional[str] = None) -> dict:
    """Configures a Workstation block (combined Queue + Activity).

    Args:
        block_id: Workstation block ID
        max_servers: Number of parallel servers
        max_queue_length: Maximum queue capacity
        delay_type: "fixed", "distribution", "connector", or "attribute"
        distribution: Distribution name for process time
        arg1-arg3: Distribution arguments
        value: Fixed delay value (for delay_type="fixed")
        cost_per_time: Cost per time unit
        cost_per_item: Cost per item processed
    """
    try:
        app = get_extendsim_app()

        warnings = []

        if max_servers is not None:
            _set_var(app, block_id, "MaxLength_prm", max_servers)

        if max_queue_length is not None:
            _set_var(app, block_id, "MaxQueueLength_prm", max_queue_length)

        # Set delay option with verification (Workstation has different popup indices than Activity)
        delay_opt = WORKSTATION_DELAY_OPTIONS.get(delay_type.lower(), 1)
        pop_result = _set_popup_verified(app, block_id, "Delay_Options_pop", delay_opt)
        if not pop_result["success"]:
            warnings.append(pop_result["warning"])

        if delay_type.lower() == "fixed":
            if value is not None:
                _set_var(app, block_id, "Delay_Arg1_prm", value)
            else:
                _set_var(app, block_id, "Delay_Arg1_prm", 1)

        elif delay_type.lower() == "distribution":
            if distribution:
                dist_code = DISTRIBUTIONS.get(distribution.lower(), 1)
                pop_result2 = _set_popup_verified(app, block_id, "Delay_Distributions_pop", dist_code)
                if not pop_result2["success"]:
                    warnings.append(pop_result2["warning"])

            if arg1 is not None:
                _set_var(app, block_id, "Delay_Arg1_prm", arg1)
            else:
                _set_var(app, block_id, "Delay_Arg1_prm", 1)
            if arg2 is not None:
                _set_var(app, block_id, "Delay_Arg2_prm", arg2)
            if arg3 is not None:
                _set_var(app, block_id, "Delay_Arg3_prm", arg3)

        # Cost settings
        if cost_per_time is not None or cost_per_item is not None:
            _set_var(app, block_id, "CalcCosts", 1)
            if cost_per_time is not None:
                _set_var(app, block_id, "CostPerTime_prm", cost_per_time)
            if cost_per_item is not None:
                _set_var(app, block_id, "CostPerItem_prm", cost_per_item)

        result = {
            "success": True,
            "blockId": block_id,
            "maxServers": max_servers,
            "maxQueueLength": max_queue_length,
            "delayType": delay_type,
            "distribution": distribution,
            "value": value,
            "args": {"arg1": arg1, "arg2": arg2, "arg3": arg3},
            "costPerTime": cost_per_time,
            "costPerItem": cost_per_item
        }
        if warnings:
            result["warnings"] = warnings
        return result
    except Exception as e:
        return _error(ErrorCode.SET_VALUE_FAILED, str(e),
                      blockId=block_id, operation="workstation_set_config")


def equation_set_formula(block_id: int,
                         equation: str = "",
                         model_id: Optional[str] = None) -> dict:
    """Sets equation text in an Equation block.

    Args:
        block_id: Equation block ID
        equation: Equation text (e.g. "o0 = i0 * 2 + i1")
    """
    try:
        app = get_extendsim_app()

        # Escape backslashes and quotes in the equation string for ModL
        # _dtxt suffix routes through SetDialogVariable correctly
        _set_var(app, block_id, "Equation_dtxt", _escape_modl_string(equation))

        return {
            "success": True,
            "blockId": block_id,
            "equation": equation
        }
    except Exception as e:
        return _error(ErrorCode.SET_VALUE_FAILED, str(e),
                      blockId=block_id, operation="equation_set_formula")


def equation_i_set_formula(block_id: int,
                            equation: str = "",
                            # v1.17.4.5 — I/O configuration
                            show_input_names: Optional[bool] = None,
                            show_input_values: Optional[bool] = None,
                            show_output_names: Optional[bool] = None,
                            show_output_values: Optional[bool] = None,
                            output_init_value: Optional[float] = None,
                            include_enabled: Optional[bool] = None,
                            expand_records: Optional[bool] = None,
                            model_id: Optional[str] = None) -> dict:
    """Sets equation text on an Equation(I) block (Item.lbr).

    The Equation(I) block allows ModL equations that reference item attributes.

    Args:
        block_id: Equation(I) block ID
        equation: Equation text (e.g. "priority = 1;")
        show_input_names: Show input connector names on block
        show_input_values: Show input connector values on block
        show_output_names: Show output connector names on block
        show_output_values: Show output connector values on block
        output_init_value: Initial value for output variables
        include_enabled: Enable include files
        expand_records: Expand records checkbox
    """
    try:
        app = get_extendsim_app()

        check = _validate_block_type(app, block_id, "Equation(I)")
        if not check.get("success"):
            return check

        if equation:
            _set_var(app, block_id, "Equation_dtxt", _escape_modl_string(equation))

        # v1.17.4.5 — I/O configuration
        if show_input_names is not None:
            _set_var(app, block_id, "IVars_ShowConNames_chk", 1 if show_input_names else 0)
        if show_input_values is not None:
            _set_var(app, block_id, "IVars_ShowConVals_chk", 1 if show_input_values else 0)
        if show_output_names is not None:
            _set_var(app, block_id, "OVars_ShowConNames_chk", 1 if show_output_names else 0)
        if show_output_values is not None:
            _set_var(app, block_id, "OVars_ShowConVals_chk", 1 if show_output_values else 0)
        if output_init_value is not None:
            _set_var(app, block_id, "OVars_Initialize_prm", output_init_value)
        if include_enabled is not None:
            _set_var(app, block_id, "Incl_chk", 1 if include_enabled else 0)
        if expand_records is not None:
            _set_var(app, block_id, "ExpandRecords_chk", 1 if expand_records else 0)

        return {
            "success": True,
            "blockId": block_id,
            "equation": equation,
            "showInputNames": show_input_names,
            "showInputValues": show_input_values,
            "showOutputNames": show_output_names,
            "showOutputValues": show_output_values,
            "outputInitValue": output_init_value,
            "includeEnabled": include_enabled,
            "expandRecords": expand_records
        }
    except Exception as e:
        return _error(ErrorCode.SET_VALUE_FAILED, str(e),
                      blockId=block_id, operation="equation_i_set_formula")


def queue_equation_set_config(block_id: int,
                               equation: Optional[str] = None,
                               release_rule: Optional[str] = None,
                               model_id: Optional[str] = None) -> dict:
    """Sets equation and release rule on a Queue Equation block (Item.lbr).

    The Queue Equation block uses equations to calculate item ranking values,
    controlling the order in which items are released from the queue.

    Args:
        block_id: Queue Equation block ID
        equation: ModL equation using QEQ variables (e.g. "QEQItemRank = DueDate - RemainingTime;")
        release_rule: "highestRank", "lowestRank", "firstTrue", or "allTrue"
    """
    try:
        app = get_extendsim_app()

        check = _validate_block_type(app, block_id, "Queue Equation")
        if not check.get("success"):
            return check

        if equation:
            _set_var(app, block_id, "Equation_dtxt", _escape_modl_string(equation))

        if release_rule:
            rule_map = {"highestrank": 1, "lowestrank": 2, "firsttrue": 3, "alltrue": 4}
            rule_code = rule_map.get(release_rule.lower(), 1)
            _set_var(app, block_id, "ReleaseRule_pop", rule_code)

        return {
            "success": True,
            "blockId": block_id,
            "equation": equation,
            "releaseRule": release_rule
        }
    except Exception as e:
        return _error(ErrorCode.SET_VALUE_FAILED, str(e),
                      blockId=block_id, operation="queue_equation_set_config")


def shift_set_schedule(block_id: int,
                       schedule: list = None,
                       # v1.17.4.4 — status type, name, repeat, time units
                       status_type: Optional[int] = None,
                       shift_name: Optional[str] = None,
                       repeat: Optional[bool] = None,
                       repeat_time: Optional[float] = None,
                       repeat_unit: Optional[int] = None,
                       time_unit: Optional[int] = None,
                       time_format: Optional[int] = None,
                       model_id: Optional[str] = None) -> dict:
    """Configures a Shift block with schedule periods.

    Args:
        block_id: Shift block ID
        schedule: List of period dicts with keys:
            - startTime: Period start time
            - endTime: Period end time
            - capacity: Capacity level (0=off, 1=on, or fractional)
    """
    try:
        app = get_extendsim_app()

        num_periods = 0

        # Set schedule entries if provided
        if schedule and len(schedule) > 0:
            # Set schedule entries in ShiftTable (stringtable with row/col indexing)
            # Columns: 0=Start Time, 1=End Time, 2=Shift Status/Capacity
            num_periods = len(schedule)
            for i, period in enumerate(schedule):
                start_time = period.get("startTime", 0)
                end_time = period.get("endTime", 8)
                capacity = period.get("capacity", 1)

                _set_var(app, block_id, "ShiftTable", start_time, i, 0)
                _set_var(app, block_id, "ShiftTable", end_time, i, 1)
                _set_var(app, block_id, "ShiftTable", capacity, i, 2)

        # v1.17.4.4 — status type, name, repeat, time units
        if status_type is not None:
            _set_var(app, block_id, "ShiftStatusTypePop", status_type)
        if shift_name is not None:
            _set_var_string(app, block_id, "ShiftName", shift_name)
        if repeat is not None:
            _set_var(app, block_id, "Repeat", 1 if repeat else 0)
        if repeat_time is not None:
            _set_var(app, block_id, "RepeatTime", repeat_time)
        if repeat_unit is not None:
            _set_var(app, block_id, "RepeatUnit", repeat_unit)
        if time_unit is not None:
            _set_var(app, block_id, "TimeUnit", time_unit)
        if time_format is not None:
            _set_var(app, block_id, "Shift_TimeFormat_pop", time_format)

        return {
            "success": True,
            "blockId": block_id,
            "periods": num_periods,
            "schedule": schedule,
            "statusType": status_type,
            "shiftName": shift_name,
            "repeat": repeat,
            "repeatTime": repeat_time,
            "repeatUnit": repeat_unit,
            "timeUnit": time_unit,
            "timeFormat": time_format
        }
    except Exception as e:
        return _error(ErrorCode.SET_VALUE_FAILED, str(e),
                      blockId=block_id, operation="shift_set_schedule")


def transport_set_config(block_id: int,
                         default_distance: Optional[float] = None,
                         default_speed: Optional[float] = None,
                         model_id: Optional[str] = None) -> dict:
    """Configures a Transport block with default distance and speed.

    Args:
        block_id: Transport block ID
        default_distance: Default transport distance
        default_speed: Default transport speed
    """
    try:
        app = get_extendsim_app()

        if default_distance is not None:
            _set_var(app, block_id, "Length_prm", default_distance)

        if default_speed is not None:
            _set_var(app, block_id, "ItemSpeed_prm", default_speed)

        return {
            "success": True,
            "blockId": block_id,
            "defaultDistance": default_distance,
            "defaultSpeed": default_speed
        }
    except Exception as e:
        return _error(ErrorCode.SET_VALUE_FAILED, str(e),
                      blockId=block_id, operation="transport_set_config")


def convey_item_set_config(block_id: int,
                           conveyor_length: Optional[float] = None,
                           default_speed: Optional[float] = None,
                           accumulating: Optional[bool] = None,
                           model_id: Optional[str] = None) -> dict:
    """Configures a Convey Item block.

    Args:
        block_id: Convey Item block ID
        conveyor_length: Total conveyor length
        default_speed: Default conveyor speed
        accumulating: True for accumulating, False for non-accumulating
    """
    try:
        app = get_extendsim_app()

        if conveyor_length is not None:
            _set_var(app, block_id, "Length_prm", conveyor_length)

        if default_speed is not None:
            _set_var(app, block_id, "Speed_prm", default_speed)

        if accumulating is not None:
            # 1 = accumulating, 2 = non-accumulating
            acc_val = 1 if accumulating else 2
            _set_var(app, block_id, "Accumulating_pop", acc_val)

        return {
            "success": True,
            "blockId": block_id,
            "conveyorLength": conveyor_length,
            "defaultSpeed": default_speed,
            "accumulating": accumulating
        }
    except Exception as e:
        return _error(ErrorCode.SET_VALUE_FAILED, str(e),
                      blockId=block_id, operation="convey_item_set_config")


def shutdown_set_config(block_id: int,
                        tbf_distribution: Optional[str] = None,
                        tbf_arg1: Optional[float] = None,
                        tbf_arg2: Optional[float] = None,
                        ttr_distribution: Optional[str] = None,
                        ttr_arg1: Optional[float] = None,
                        ttr_arg2: Optional[float] = None,
                        model_id: Optional[str] = None) -> dict:
    """Configures a Shutdown block with TBF/TTR distributions.

    Args:
        block_id: Shutdown block ID
        tbf_distribution: Time Between Failures distribution name
        tbf_arg1: TBF distribution argument 1
        tbf_arg2: TBF distribution argument 2
        ttr_distribution: Time To Repair distribution name
        ttr_arg1: TTR distribution argument 1
        ttr_arg2: TTR distribution argument 2
    """
    try:
        app = get_extendsim_app()

        warnings = []

        # TBF (Time Between Failures) configuration
        if tbf_distribution is not None:
            dist_code = DISTRIBUTIONS.get(tbf_distribution.lower(), 5)
            pop_result = _set_popup_verified(app, block_id, "SF_TBF_Distribs_pop", dist_code)
            if not pop_result["success"]:
                warnings.append(pop_result["warning"])

        if tbf_arg1 is not None:
            _set_var(app, block_id, "SF_TBF_Arg1_prm", tbf_arg1)

        if tbf_arg2 is not None:
            _set_var(app, block_id, "SF_TBF_Arg2_prm", tbf_arg2)

        # TTR (Time To Repair) configuration
        if ttr_distribution is not None:
            dist_code = DISTRIBUTIONS.get(ttr_distribution.lower(), 1)
            pop_result2 = _set_popup_verified(app, block_id, "SF_TTR_Distribs_pop", dist_code)
            if not pop_result2["success"]:
                warnings.append(pop_result2["warning"])

        if ttr_arg1 is not None:
            _set_var(app, block_id, "SF_TTR_Arg1_prm", ttr_arg1)

        if ttr_arg2 is not None:
            _set_var(app, block_id, "SF_TTR_Arg2_prm", ttr_arg2)

        result = {
            "success": True,
            "blockId": block_id,
            "tbfDistribution": tbf_distribution,
            "tbfArgs": {"arg1": tbf_arg1, "arg2": tbf_arg2},
            "ttrDistribution": ttr_distribution,
            "ttrArgs": {"arg1": ttr_arg1, "arg2": ttr_arg2}
        }
        if warnings:
            result["warnings"] = warnings
        return result
    except Exception as e:
        return _error(ErrorCode.SET_VALUE_FAILED, str(e),
                      blockId=block_id, operation="shutdown_set_config")


def tank_set_config(block_id: int,
                    capacity: Optional[float] = None,
                    initial_level: Optional[float] = None,
                    max_input_rate: Optional[float] = None,
                    max_output_rate: Optional[float] = None,
                    flow_control_enabled=None,
                    flow_control_policy=None,
                    flow_control_value=None,
                    model_id: Optional[str] = None) -> dict:
    """Configures a Tank block (Rate.lbr) for flow-based simulation.

    Args:
        block_id: Tank block ID
        capacity: Maximum tank capacity
        initial_level: Initial contents level
        max_input_rate: Maximum inflow rate (enables rate limiting)
        max_output_rate: Maximum outflow rate (enables rate limiting)
        flow_control_enabled: Enable flow control (FC connector)
        flow_control_policy: Popup index for flow control policy (ControlFlowValue_POP)
        flow_control_value: Flow control target value
    """
    try:
        app = get_extendsim_app()

        if capacity is not None:
            _set_var(app, block_id, "Capacity_prm", capacity)

        if initial_level is not None:
            _set_var(app, block_id, "Initialization_prm", initial_level)

        if max_input_rate is not None:
            _set_var(app, block_id, "DefineMaximumInputRate_chk", 1)
            _set_var(app, block_id, "MaxInputRate_prm", max_input_rate)

        if max_output_rate is not None:
            _set_var(app, block_id, "DefineMaximumOutputRate_chk", 1)
            _set_var(app, block_id, "MaxOutputRate_prm", max_output_rate)

        if flow_control_enabled is not None:
            _set_var(app, block_id, "FlowControl_chk", 1 if flow_control_enabled else 0)

        if flow_control_policy is not None:
            _set_var(app, block_id, "ControlFlowValue_POP", flow_control_policy)

        if flow_control_value is not None:
            _set_var(app, block_id, "ControlFlowValue_PRM", flow_control_value)

        return {
            "success": True,
            "blockId": block_id,
            "capacity": capacity,
            "initialLevel": initial_level,
            "maxInputRate": max_input_rate,
            "maxOutputRate": max_output_rate,
            "flowControlEnabled": flow_control_enabled,
            "flowControlPolicy": flow_control_policy,
            "flowControlValue": flow_control_value
        }
    except Exception as e:
        return _error(ErrorCode.SET_VALUE_FAILED, str(e),
                      blockId=block_id, operation="tank_set_config")


def valve_set_config(block_id: int,
                     max_rate: Optional[float] = None,
                     goal: Optional[float] = None,
                     goal_type=None,
                     goal_off_status=None,
                     control_type=None,
                     start_condition=None,
                     stop_condition=None,
                     shutdown_condition=None,
                     pull_constraint_delay=None,
                     model_id: Optional[str] = None) -> dict:
    """Configures a Valve block (Rate.lbr) for flow rate control.

    Args:
        block_id: Valve block ID
        max_rate: Maximum constraining flow rate
        goal: Goal quantity for the valve to pass
        goal_type: GoalType_pop index (1=quantity, 2=duration, 3=hysteresis)
        goal_off_status: GoalOffStatus_pop index for off behavior after goal met
        control_type: ControlType_pop index for hysteresis control type
        start_condition: Hysteresis start threshold value
        stop_condition: Hysteresis stop threshold value
        shutdown_condition: ShutdownCondition_pop index for shutdown condition
        pull_constraint_delay: Pull constraint delay value (enables delay checkbox)
    """
    try:
        app = get_extendsim_app()

        if max_rate is not None:
            _set_var(app, block_id, "ConstrainingRate_prm", max_rate)

        if goal is not None:
            _set_var(app, block_id, "Goal_prm", goal)

        if goal_type is not None:
            _set_var(app, block_id, "GoalType_pop", goal_type)

        if goal_off_status is not None:
            _set_var(app, block_id, "GoalOffStatus_pop", goal_off_status)

        if control_type is not None:
            _set_var(app, block_id, "ControlType_pop", control_type)

        if start_condition is not None:
            _set_var(app, block_id, "StartCondition_prm", start_condition)

        if stop_condition is not None:
            _set_var(app, block_id, "StopCondition_prm", stop_condition)

        if shutdown_condition is not None:
            _set_var(app, block_id, "ShutdownCondition_pop", shutdown_condition)

        if pull_constraint_delay is not None:
            _set_var(app, block_id, "PullConstraintDelay_chk", 1)
            _set_var(app, block_id, "PullConstraintDelay_prm", pull_constraint_delay)

        return {
            "success": True,
            "blockId": block_id,
            "maxRate": max_rate,
            "goal": goal,
            "goalType": goal_type,
            "goalOffStatus": goal_off_status,
            "controlType": control_type,
            "startCondition": start_condition,
            "stopCondition": stop_condition,
            "shutdownCondition": shutdown_condition,
            "pullConstraintDelay": pull_constraint_delay
        }
    except Exception as e:
        return _error(ErrorCode.SET_VALUE_FAILED, str(e),
                      blockId=block_id, operation="valve_set_config")


def merge_set_config(block_id: int,
                     mode: Optional[int] = None,
                     initial_value_selected=None,
                     initialize_selected=None,
                     param_from_connectors=None,
                     model_id: Optional[str] = None) -> dict:
    """Configures a Merge block (Rate.lbr) - combines multiple flow inputs.

    Args:
        block_id: Merge block ID
        mode: Popup index for Mode_pop
        initial_value_selected: Initial selected branch index
        initialize_selected: Initialize selected branch at start
        param_from_connectors: Get branch params from value connectors
    """
    try:
        app = get_extendsim_app()

        if mode is not None:
            _set_var(app, block_id, "Mode_pop", mode)

        if initial_value_selected is not None:
            _set_var(app, block_id, "InitialValueSelected_prm", initial_value_selected)

        if initialize_selected is not None:
            _set_var(app, block_id, "InitializeSelected_chk", 1 if initialize_selected else 0)

        if param_from_connectors is not None:
            _set_var(app, block_id, "ParamFromConnectors_chk", 1 if param_from_connectors else 0)

        return {
            "success": True,
            "blockId": block_id,
            "mode": mode,
            "initialValueSelected": initial_value_selected,
            "initializeSelected": initialize_selected,
            "paramFromConnectors": param_from_connectors
        }
    except Exception as e:
        return _error(ErrorCode.SET_VALUE_FAILED, str(e),
                      blockId=block_id, operation="merge_set_config")


def diverge_set_config(block_id: int,
                       mode: Optional[int] = None,
                       initial_value_selected=None,
                       initialize_selected=None,
                       param_from_connectors=None,
                       model_id: Optional[str] = None) -> dict:
    """Configures a Diverge block (Rate.lbr) - splits flow to multiple outputs.

    Args:
        block_id: Diverge block ID
        mode: Popup index for Mode_pop
        initial_value_selected: Initial selected branch index
        initialize_selected: Initialize selected branch at start
        param_from_connectors: Get branch params from value connectors
    """
    try:
        app = get_extendsim_app()

        if mode is not None:
            _set_var(app, block_id, "Mode_pop", mode)

        if initial_value_selected is not None:
            _set_var(app, block_id, "InitialValueSelected_prm", initial_value_selected)

        if initialize_selected is not None:
            _set_var(app, block_id, "InitializeSelected_chk", 1 if initialize_selected else 0)

        if param_from_connectors is not None:
            _set_var(app, block_id, "ParamFromConnectors_chk", 1 if param_from_connectors else 0)

        return {
            "success": True,
            "blockId": block_id,
            "mode": mode,
            "initialValueSelected": initial_value_selected,
            "initializeSelected": initialize_selected,
            "paramFromConnectors": param_from_connectors
        }
    except Exception as e:
        return _error(ErrorCode.SET_VALUE_FAILED, str(e),
                      blockId=block_id, operation="diverge_set_config")


def interchange_set_config(block_id: int,
                           capacity: Optional[float] = None,
                           initial_level: Optional[float] = None,
                           max_input_rate: Optional[float] = None,
                           max_output_rate: Optional[float] = None,
                           mode=None,
                           release_condition=None,
                           release_target=None,
                           release_interrupt=None,
                           model_id: Optional[str] = None) -> dict:
    """Configures an Interchange block (Rate.lbr) - converts between items and flow.

    Args:
        block_id: Interchange block ID
        capacity: Maximum capacity
        initial_level: Initial contents level
        max_input_rate: Maximum inflow rate (enables rate limiting)
        max_output_rate: Maximum outflow rate (enables rate limiting)
        mode: TankAndItemLink_pop index (1=tank while item, 2=tank separate)
        release_condition: ReleaseCondition_pop index for item release condition
        release_target: Target value for release (ContentsToReleaseItem_prm)
        release_interrupt: Enable interrupt release via connector
    """
    try:
        app = get_extendsim_app()

        if capacity is not None:
            _set_var(app, block_id, "Capacity_prm", capacity)

        if initial_level is not None:
            _set_var(app, block_id, "Initialization_prm", initial_level)

        if max_input_rate is not None:
            _set_var(app, block_id, "DefineMaximumInputRate_chk", 1)
            _set_var(app, block_id, "MaxInputRate_prm", max_input_rate)

        if max_output_rate is not None:
            _set_var(app, block_id, "DefineMaximumOutputRate_chk", 1)
            _set_var(app, block_id, "MaxOutputRate_prm", max_output_rate)

        if mode is not None:
            _set_var(app, block_id, "TankAndItemLink_pop", mode)

        if release_condition is not None:
            _set_var(app, block_id, "ReleaseCondition_pop", release_condition)

        if release_target is not None:
            _set_var(app, block_id, "ContentsToReleaseItem_prm", release_target)

        if release_interrupt is not None:
            _set_var(app, block_id, "ReleaseInterruptConn_chk", 1 if release_interrupt else 0)

        return {
            "success": True,
            "blockId": block_id,
            "capacity": capacity,
            "initialLevel": initial_level,
            "maxInputRate": max_input_rate,
            "maxOutputRate": max_output_rate,
            "mode": mode,
            "releaseCondition": release_condition,
            "releaseTarget": release_target,
            "releaseInterrupt": release_interrupt
        }
    except Exception as e:
        return _error(ErrorCode.SET_VALUE_FAILED, str(e),
                      blockId=block_id, operation="interchange_set_config")


def convey_flow_set_config(block_id: int,
                           speed: Optional[float] = None,
                           length: Optional[float] = None,
                           capacity_max: Optional[float] = None,
                           accumulating: Optional[int] = None,
                           # v1.17.4.4 — type, density, delay, shift, attribute transform
                           conveyor_type: Optional[int] = None,
                           max_density: Optional[float] = None,
                           delay: Optional[float] = None,
                           shift: Optional[int] = None,
                           empty_when_off_shift: Optional[bool] = None,
                           attribute_transform: Optional[int] = None,
                           model_id: Optional[str] = None) -> dict:
    """Configures a Convey Flow block (Rate.lbr) - transports flow with delay.

    Args:
        block_id: Convey Flow block ID
        speed: Flow speed
        length: Conveyor length
        capacity_max: Maximum capacity
        accumulating: Accumulate popup (1=accumulating, 2=non-accumulating)
    """
    try:
        app = get_extendsim_app()

        if speed is not None:
            _set_var(app, block_id, "Speed_prm", speed)

        if length is not None:
            _set_var(app, block_id, "Length_prm", length)

        if capacity_max is not None:
            _set_var(app, block_id, "CapacityMax_prm", capacity_max)

        if accumulating is not None:
            _set_var(app, block_id, "Accumulate_pop", accumulating)

        # v1.17.4.4 — type, density, delay, shift, attribute transform
        if conveyor_type is not None:
            _set_var(app, block_id, "ConveyorType_pop", conveyor_type)
        if max_density is not None:
            _set_var(app, block_id, "MaxQtityPerUnit_prm", max_density)
        if delay is not None:
            _set_var(app, block_id, "Delay_prm", delay)
        if shift is not None:
            _set_var(app, block_id, "Shift_pop", shift)
        if empty_when_off_shift is not None:
            _set_var(app, block_id, "EmptiedWhenShiftOFF_chk", 1 if empty_when_off_shift else 0)
        if attribute_transform is not None:
            _set_var(app, block_id, "AttributeTransform_pop", attribute_transform)

        return {
            "success": True,
            "blockId": block_id,
            "speed": speed,
            "length": length,
            "capacityMax": capacity_max,
            "accumulating": accumulating,
            "conveyorType": conveyor_type,
            "maxDensity": max_density,
            "delay": delay,
            "shift": shift,
            "emptyWhenOffShift": empty_when_off_shift,
            "attributeTransform": attribute_transform
        }
    except Exception as e:
        return _error(ErrorCode.SET_VALUE_FAILED, str(e),
                      blockId=block_id, operation="convey_flow_set_config")


def change_units_set_config(block_id: int,
                            factor: Optional[float] = None,
                            model_id: Optional[str] = None) -> dict:
    """Configures a Change Units block (Rate.lbr) - converts flow units.

    Args:
        block_id: Change Units block ID
        factor: Conversion factor (FactorResult_prm)
    """
    try:
        app = get_extendsim_app()

        if factor is not None:
            _set_var(app, block_id, "FactorResult_prm", factor)

        return {
            "success": True,
            "blockId": block_id,
            "factor": factor
        }
    except Exception as e:
        return _error(ErrorCode.SET_VALUE_FAILED, str(e),
                      blockId=block_id, operation="change_units_set_config")


def bias_set_config(block_id: int,
                    bias_order: Optional[float] = None,
                    model_id: Optional[str] = None) -> dict:
    """Configures a Bias block (Rate.lbr) - adjusts flow allocation priority.

    Args:
        block_id: Bias block ID
        bias_order: Priority ordering value (RealBiasOrder_prm)
    """
    try:
        app = get_extendsim_app()

        if bias_order is not None:
            _set_var(app, block_id, "RealBiasOrder_prm", bias_order)

        return {
            "success": True,
            "blockId": block_id,
            "biasOrder": bias_order
        }
    except Exception as e:
        return _error(ErrorCode.SET_VALUE_FAILED, str(e),
                      blockId=block_id, operation="bias_set_config")


def catch_flow_set_config(block_id: int,
                          position: Optional[float] = None,
                          model_id: Optional[str] = None) -> dict:
    """Configures a Catch Flow block (Rate.lbr) - receives flow from Throw Flow.

    Args:
        block_id: Catch Flow block ID
        position: Position value (Position_prm)
    """
    try:
        app = get_extendsim_app()

        if position is not None:
            _set_var(app, block_id, "Position_prm", position)

        return {
            "success": True,
            "blockId": block_id,
            "position": position
        }
    except Exception as e:
        return _error(ErrorCode.SET_VALUE_FAILED, str(e),
                      blockId=block_id, operation="catch_flow_set_config")


def throw_flow_set_config(block_id: int,
                          position: Optional[float] = None,
                          connector_num: Optional[int] = None,
                          model_id: Optional[str] = None) -> dict:
    """Configures a Throw Flow block (Rate.lbr) - sends flow to Catch Flow.

    Args:
        block_id: Throw Flow block ID
        position: Position value (Position_prm)
        connector_num: Connector number (ConnectorNum_prm)
    """
    try:
        app = get_extendsim_app()

        if position is not None:
            _set_var(app, block_id, "Position_prm", position)

        if connector_num is not None:
            _set_var(app, block_id, "ConnectorNum_prm", connector_num)

        return {
            "success": True,
            "blockId": block_id,
            "position": position,
            "connectorNum": connector_num
        }
    except Exception as e:
        return _error(ErrorCode.SET_VALUE_FAILED, str(e),
                      blockId=block_id, operation="throw_flow_set_config")


def history_r_set_config(block_id: int,
                         max_rows: Optional[int] = None,
                         enable_database_log: Optional[bool] = None,
                         model_id: Optional[str] = None) -> dict:
    """Configures a History(R) block (Rate.lbr) - logs flow history data.

    Args:
        block_id: History(R) block ID
        max_rows: Maximum rows in history (also enables MaxRowsInHistory_chk)
        enable_database_log: Enable database logging (DBL_chk)
    """
    try:
        app = get_extendsim_app()

        if max_rows is not None:
            _set_var(app, block_id, "MaxRowsInHistory_chk", 1)
            _set_var(app, block_id, "MaxRows_prm", max_rows)

        if enable_database_log is not None:
            val = 1 if enable_database_log else 0
            _set_var(app, block_id, "DBL_chk", val)

        return {
            "success": True,
            "blockId": block_id,
            "maxRows": max_rows,
            "enableDatabaseLog": enable_database_log
        }
    except Exception as e:
        return _error(ErrorCode.SET_VALUE_FAILED, str(e),
                      blockId=block_id, operation="history_r_set_config")


def get_r_set_config(block_id: int,
                     location_block_id: Optional[int] = None,
                     info_type=None,
                     flow_attribute=None,
                     model_id: Optional[str] = None) -> dict:
    """Configures a Get(R) block (Rate.lbr) - reads flow attribute values.

    Args:
        block_id: Get(R) block ID
        location_block_id: Block number to read from (SelectLocationBlockNum_prm)
        info_type: InformationReportedType_pop index (what to report)
        flow_attribute: FlowAttribute_pop index (which attribute)
    """
    try:
        app = get_extendsim_app()

        if location_block_id is not None:
            _set_var(app, block_id, "SelectLocationBlockNum_prm", location_block_id)

        if info_type is not None:
            _set_var(app, block_id, "InformationReportedType_pop", info_type)

        if flow_attribute is not None:
            _set_var(app, block_id, "FlowAttribute_pop", flow_attribute)

        return {
            "success": True,
            "blockId": block_id,
            "locationBlockId": location_block_id,
            "infoType": info_type,
            "flowAttribute": flow_attribute
        }
    except Exception as e:
        return _error(ErrorCode.SET_VALUE_FAILED, str(e),
                      blockId=block_id, operation="get_r_set_config")


def set_r_set_config(block_id: int,
                     model_id: Optional[str] = None) -> dict:
    """Configures a Set(R) block (Rate.lbr) - sets flow attribute values.

    Validates the block exists and is the correct type.
    Use block_set_value for advanced configuration of Set(R) parameters.

    Args:
        block_id: Set(R) block ID
    """
    try:
        app = get_extendsim_app()

        model_check = _validate_model_open(app)
        if not model_check.get("success"):
            return model_check

        # Validate block exists and is correct type
        type_check = _validate_block_type(app, block_id, "Set")
        if not type_check.get("success"):
            return type_check

        return {
            "success": True,
            "blockId": block_id,
            "message": "Set(R) block validated. Use block_set_value for advanced configuration."
        }
    except Exception as e:
        return _error(ErrorCode.SET_VALUE_FAILED, str(e),
                      blockId=block_id, operation="set_r_set_config")


def throw_item_set_config(block_id: int,
                          catch_type=None,
                          catch_group=None,
                          attribute_name=None,
                          use_block_num=None,
                          model_id: Optional[str] = None) -> dict:
    """Configures a Throw Item block (Item.lbr) for routing items to Catch blocks.

    Args:
        block_id: Throw Item block ID
        catch_type: CatchType_pop index for catch type
        catch_group: CatchGroupPop index for catch group
        attribute_name: Attribute name for attribute-based routing
        use_block_num: Route by block number
    """
    try:
        app = get_extendsim_app()

        model_check = _validate_model_open(app)
        if not model_check.get("success"):
            return model_check

        type_check = _validate_block_type(app, block_id, "Throw Item")
        if not type_check.get("success"):
            return type_check

        if catch_type is not None:
            _set_var(app, block_id, "CatchType_pop", catch_type)

        if catch_group is not None:
            _set_var(app, block_id, "CatchGroupPop", catch_group)

        if attribute_name is not None:
            _set_var_string(app, block_id, "AttribName", attribute_name)

        if use_block_num is not None:
            _set_var(app, block_id, "UseBlockNum", 1 if use_block_num else 0)

        return {
            "success": True,
            "blockId": block_id,
            "catchType": catch_type,
            "catchGroup": catch_group,
            "attributeName": attribute_name,
            "useBlockNum": use_block_num
        }
    except Exception as e:
        return _error(ErrorCode.SET_VALUE_FAILED, str(e),
                      blockId=block_id, operation="throw_item_set_config")


def catch_item_set_config(block_id: int,
                          catch_group=None,
                          count_by_throw=None,
                          model_id: Optional[str] = None) -> dict:
    """Configures a Catch Item block (Item.lbr).

    Args:
        block_id: Catch Item block ID
        catch_group: CatchGroupPop index for catch group
        count_by_throw: Count items by throw block
    """
    try:
        app = get_extendsim_app()

        model_check = _validate_model_open(app)
        if not model_check.get("success"):
            return model_check

        type_check = _validate_block_type(app, block_id, "Catch Item")
        if not type_check.get("success"):
            return type_check

        if catch_group is not None:
            _set_var(app, block_id, "CatchGroupPop", catch_group)

        if count_by_throw is not None:
            _set_var(app, block_id, "CountByThrow_chk", 1 if count_by_throw else 0)

        return {
            "success": True,
            "blockId": block_id,
            "catchGroup": catch_group,
            "countByThrow": count_by_throw
        }
    except Exception as e:
        return _error(ErrorCode.SET_VALUE_FAILED, str(e),
                      blockId=block_id, operation="catch_item_set_config")


def clear_statistics_set_config(block_id: int,
                                clear_time=None, time_units=None,
                                clear_activity=None, clear_resource=None,
                                clear_queue=None, clear_exit=None,
                                clear_mean_variance=None, clear_information=None,
                                clear_rate=None, clear_max_min=None,
                                model_id: Optional[str] = None) -> dict:
    """Configures a Clear Statistics block (Value.lbr).

    Args:
        block_id: Clear Statistics block ID
        clear_time: Time to clear statistics (warm-up period end)
        time_units: Popup index for time units
        clear_activity: Clear Activity statistics
        clear_resource: Clear Resource Pool statistics
        clear_queue: Clear Queue statistics
        clear_exit: Clear Exit statistics
        clear_mean_variance: Clear Mean & Variance statistics
        clear_information: Clear Information block statistics
        clear_rate: Clear Rate library statistics
        clear_max_min: Clear Max & Min statistics
    """
    try:
        app = get_extendsim_app()

        model_check = _validate_model_open(app)
        if not model_check.get("success"):
            return model_check

        type_check = _validate_block_type(app, block_id, "Clear Statistics")
        if not type_check.get("success"):
            return type_check

        if clear_time is not None:
            _set_var(app, block_id, "SetClear", clear_time)

        if time_units is not None:
            _set_var(app, block_id, "TimeUnits_pop", time_units)

        if clear_activity is not None:
            _set_var(app, block_id, "ClearAct_chk", 1 if clear_activity else 0)

        if clear_resource is not None:
            _set_var(app, block_id, "ClearRes_chk", 1 if clear_resource else 0)

        if clear_queue is not None:
            _set_var(app, block_id, "ClearQue_chk", 1 if clear_queue else 0)

        if clear_exit is not None:
            _set_var(app, block_id, "ClearExit_chk", 1 if clear_exit else 0)

        if clear_mean_variance is not None:
            _set_var(app, block_id, "ClearMV_chk", 1 if clear_mean_variance else 0)

        if clear_information is not None:
            _set_var(app, block_id, "ClearInformation_chk", 1 if clear_information else 0)

        if clear_rate is not None:
            _set_var(app, block_id, "ClearRate_chk", 1 if clear_rate else 0)

        if clear_max_min is not None:
            _set_var(app, block_id, "ClearMaxMin_chk", 1 if clear_max_min else 0)

        return {
            "success": True,
            "blockId": block_id,
            "clearTime": clear_time,
            "timeUnits": time_units,
            "clearActivity": clear_activity,
            "clearResource": clear_resource,
            "clearQueue": clear_queue,
            "clearExit": clear_exit,
            "clearMeanVariance": clear_mean_variance,
            "clearInformation": clear_information,
            "clearRate": clear_rate,
            "clearMaxMin": clear_max_min
        }
    except Exception as e:
        return _error(ErrorCode.SET_VALUE_FAILED, str(e),
                      blockId=block_id, operation="clear_statistics_set_config")


def information_set_config(block_id: int,
                           cycle_attribute=None, add_count=None,
                           count_by_one=None, no_reset=None,
                           reset_when=None, detailed_stats=None,
                           reset_every=None, reset_every_interval=None,
                           model_id: Optional[str] = None) -> dict:
    """Configures an Information block (Item.lbr).

    Args:
        block_id: Information block ID
        cycle_attribute: Popup index for cycle time attribute
        add_count: Enable add-count connector
        count_by_one: Count by 1 vs connector value
        no_reset: Don't reset on consecutive runs
        reset_when: Popup index for reset condition
        detailed_stats: Enable detailed statistics
        reset_every: Enable periodic reset
        reset_every_interval: Reset interval value
    """
    try:
        app = get_extendsim_app()

        model_check = _validate_model_open(app)
        if not model_check.get("success"):
            return model_check

        type_check = _validate_block_type(app, block_id, "Information")
        if not type_check.get("success"):
            return type_check

        if cycle_attribute is not None:
            _set_var(app, block_id, "Attribcycle_POP", cycle_attribute)

        if add_count is not None:
            _set_var(app, block_id, "AddCount_chk", 1 if add_count else 0)

        if count_by_one is not None:
            _set_var(app, block_id, "CountBy1_chk", 1 if count_by_one else 0)

        if no_reset is not None:
            _set_var(app, block_id, "NoReset_chk", 1 if no_reset else 0)

        if reset_when is not None:
            _set_var(app, block_id, "ResetWhen_pop", reset_when)

        if detailed_stats is not None:
            _set_var(app, block_id, "DetailedStats_chk", 1 if detailed_stats else 0)

        if reset_every is not None:
            _set_var(app, block_id, "ResetEvery_chk", 1 if reset_every else 0)

        if reset_every_interval is not None:
            _set_var(app, block_id, "ResetEvery_prm", reset_every_interval)

        return {
            "success": True,
            "blockId": block_id,
            "cycleAttribute": cycle_attribute,
            "addCount": add_count,
            "countByOne": count_by_one,
            "noReset": no_reset,
            "resetWhen": reset_when,
            "detailedStats": detailed_stats,
            "resetEvery": reset_every,
            "resetEveryInterval": reset_every_interval
        }
    except Exception as e:
        return _error(ErrorCode.SET_VALUE_FAILED, str(e),
                      blockId=block_id, operation="information_set_config")


def mean_variance_set_config(block_id: int,
                             multi_sim=None, weight=None,
                             clear_time=None, confidence=None,
                             init_value=None, moving_average=None,
                             moving_average_interval=None,
                             record_history=None, relative_error=None,
                             relative_error_threshold=None,
                             model_id: Optional[str] = None) -> dict:
    """Configures a Mean & Variance block (Value.lbr).

    Args:
        block_id: Mean & Variance block ID
        multi_sim: Enable multi-simulation mode
        weight: Enable weighted statistics
        clear_time: Time to clear statistics
        confidence: Confidence level percentage (default 95)
        init_value: Popup index for initial value mode
        moving_average: Enable moving average
        moving_average_interval: Moving average window size
        record_history: Enable history table recording
        relative_error: Enable relative error checking
        relative_error_threshold: Relative error threshold
    """
    try:
        app = get_extendsim_app()

        model_check = _validate_model_open(app)
        if not model_check.get("success"):
            return model_check

        type_check = _validate_block_type(app, block_id, "Mean & Variance")
        if not type_check.get("success"):
            return type_check

        if multi_sim is not None:
            _set_var(app, block_id, "MultiSim", 1 if multi_sim else 0)

        if weight is not None:
            _set_var(app, block_id, "Weight", 1 if weight else 0)

        if clear_time is not None:
            _set_var(app, block_id, "ClearTime", clear_time)

        if confidence is not None:
            _set_var(app, block_id, "Conf", confidence)

        if init_value is not None:
            _set_var(app, block_id, "InitValue", init_value)

        if moving_average is not None:
            _set_var(app, block_id, "MovingAverage", 1 if moving_average else 0)

        if moving_average_interval is not None:
            _set_var(app, block_id, "MovingAverageInterval", moving_average_interval)

        if record_history is not None:
            _set_var(app, block_id, "RecordHistory_chk", 1 if record_history else 0)

        if relative_error is not None:
            _set_var(app, block_id, "RelativeError_chk", 1 if relative_error else 0)

        if relative_error_threshold is not None:
            _set_var(app, block_id, "RelativeError_prm", relative_error_threshold)

        return {
            "success": True,
            "blockId": block_id,
            "multiSim": multi_sim,
            "weight": weight,
            "clearTime": clear_time,
            "confidence": confidence,
            "initValue": init_value,
            "movingAverage": moving_average,
            "movingAverageInterval": moving_average_interval,
            "recordHistory": record_history,
            "relativeError": relative_error,
            "relativeErrorThreshold": relative_error_threshold
        }
    except Exception as e:
        return _error(ErrorCode.SET_VALUE_FAILED, str(e),
                      blockId=block_id, operation="mean_variance_set_config")


def line_chart_set_config(block_id: int,
                          start_time=None, end_time=None,
                          disable_recording=None, fixed_points=None,
                          model_id: Optional[str] = None) -> dict:
    """Configures a Line Chart block (Chart.lbr).

    Args:
        block_id: Line Chart block ID
        start_time: Data collection window start time
        end_time: Data collection window end time
        disable_recording: Disable data recording
        fixed_points: Fixed number of points to plot (enables fixed-point mode)
    """
    try:
        app = get_extendsim_app()

        model_check = _validate_model_open(app)
        if not model_check.get("success"):
            return model_check

        type_check = _validate_block_type(app, block_id, "Line Chart")
        if not type_check.get("success"):
            return type_check

        if start_time is not None:
            _set_var(app, block_id, "DCW_StartTime_prm", start_time)

        if end_time is not None:
            _set_var(app, block_id, "DCW_EndTime_prm", end_time)

        if disable_recording is not None:
            _set_var(app, block_id, "DisableRecording_chk", 1 if disable_recording else 0)

        if fixed_points is not None:
            _set_var(app, block_id, "FixedNumberOfPointsToPlot_chk", 1)
            _set_var(app, block_id, "FixedNumberOfPointsToPlot_prm", fixed_points)

        return {
            "success": True,
            "blockId": block_id,
            "startTime": start_time,
            "endTime": end_time,
            "disableRecording": disable_recording,
            "fixedPoints": fixed_points
        }
    except Exception as e:
        return _error(ErrorCode.SET_VALUE_FAILED, str(e),
                      blockId=block_id, operation="line_chart_set_config")


def histogram_set_config(block_id: int,
                         num_bins=None, bin_size=None,
                         x_min=None, x_max=None,
                         model_id: Optional[str] = None) -> dict:
    """Configures a Histogram block (Chart.lbr).

    Args:
        block_id: Histogram block ID
        num_bins: Number of bins
        bin_size: Bin size
        x_min: X-axis minimum value
        x_max: X-axis maximum value
    """
    try:
        app = get_extendsim_app()

        model_check = _validate_model_open(app)
        if not model_check.get("success"):
            return model_check

        type_check = _validate_block_type(app, block_id, "Histogram")
        if not type_check.get("success"):
            return type_check

        if num_bins is not None:
            _set_var(app, block_id, "Data_Bin_Number_PRM", num_bins)

        if bin_size is not None:
            _set_var(app, block_id, "Data_Bin_Size_PRM", bin_size)

        if x_min is not None:
            _set_var(app, block_id, "Data_Bin_X_Min_PRM", x_min)

        if x_max is not None:
            _set_var(app, block_id, "Data_Bin_X_Max_PRM", x_max)

        return {
            "success": True,
            "blockId": block_id,
            "numBins": num_bins,
            "binSize": bin_size,
            "xMin": x_min,
            "xMax": x_max
        }
    except Exception as e:
        return _error(ErrorCode.SET_VALUE_FAILED, str(e),
                      blockId=block_id, operation="histogram_set_config")


# v1.17.4.4 — Resource Item handler
def resource_item_set_config(block_id: int,
                             initial_count=None, strip_attributes=None,
                             item_type=None, cost_per_time=None,
                             cost_per_item=None, cost_time_unit=None,
                             shift=None, cost_enabled=None,
                             model_id: Optional[str] = None) -> dict:
    """Configures a Resource Item block (Item.lbr) for resource pools with individual items.

    Args:
        block_id: Resource Item block ID
        initial_count: Initial number of resource items
        strip_attributes: Strip attributes when item is released
        item_type: ItemType_pop index for item type
        cost_per_time: Cost per time unit (ABC costing)
        cost_per_item: Cost per item (ABC costing)
        cost_time_unit: CostTimeUnits_pop index for cost time unit
        shift: Shift_pop index for shift block reference
        cost_enabled: Enable costing
    """
    try:
        app = get_extendsim_app()

        model_check = _validate_model_open(app)
        if not model_check.get("success"):
            return model_check

        type_check = _validate_block_type(app, block_id, "Resource Item")
        if not type_check.get("success"):
            return type_check

        if initial_count is not None:
            _set_var(app, block_id, "InitialNumItems_prm", initial_count)
        if strip_attributes is not None:
            _set_var(app, block_id, "StripAttribs_chk", 1 if strip_attributes else 0)
        if item_type is not None:
            _set_var(app, block_id, "ItemType_pop", item_type)
        if cost_enabled is not None:
            _set_var(app, block_id, "CostEnable_chk", 1 if cost_enabled else 0)
        if cost_per_time is not None:
            _set_var(app, block_id, "CostPerTime_prm", cost_per_time)
        if cost_per_item is not None:
            _set_var(app, block_id, "CostPerItem_prm", cost_per_item)
        if cost_time_unit is not None:
            _set_var(app, block_id, "CostTimeUnits_pop", cost_time_unit)
        if shift is not None:
            _set_var(app, block_id, "Shift_pop", shift)

        return {
            "success": True,
            "blockId": block_id,
            "initialCount": initial_count,
            "stripAttributes": strip_attributes,
            "itemType": item_type,
            "costPerTime": cost_per_time,
            "costPerItem": cost_per_item,
            "costTimeUnit": cost_time_unit,
            "shift": shift,
            "costEnabled": cost_enabled
        }
    except Exception as e:
        return _error(ErrorCode.SET_VALUE_FAILED, str(e),
                      blockId=block_id, operation="resource_item_set_config")


# ============================================================================
# v1.5 TOOLS - Hierarchies, Optimizer, Scenario Manager, Analysis Manager
# ============================================================================


def hierarchy_list(model_id: Optional[str] = None) -> dict:
    """Lists all H-blocks (hierarchical blocks) in the model with depth info.

    Uses ObjectIDNext(fromBlock, 1) to iterate H-blocks,
    GetEnclosingHblockNum2() for parent, LocalNumBlocks2() for internal count.
    """
    try:
        app = get_extendsim_app()
        hierarchies = []

        # Start iteration from block 0, which=1 for H-blocks only
        app.Execute("global0 = ObjectIDNext(0, 1);")
        current = int(parse_float(app.Request("System", "global0+:0:0:0")))

        while current > 0:
            # Get block name
            app.Execute(f'globalStr0 = BlockName({current});')
            block_name = app.Request("System", "globalStr0+:0:0:0") or ""

            # Get block label
            app.Execute(f'globalStr0 = GetBlockLabel({current});')
            label = app.Request("System", "globalStr0+:0:0:0") or ""

            # Get parent H-block (-1 if top-level)
            app.Execute(f'global0 = GetEnclosingHblockNum2({current});')
            parent_id = int(parse_float(app.Request("System", "global0+:0:0:0")))

            # Get internal block count
            app.Execute(f'global0 = LocalNumBlocks2({current});')
            internal_count = int(parse_float(app.Request("System", "global0+:0:0:0")))

            # Calculate depth by following parent chain
            depth = 0
            walk = parent_id
            while walk > 0:
                depth += 1
                app.Execute(f'global0 = GetEnclosingHblockNum2({walk});')
                walk = int(parse_float(app.Request("System", "global0+:0:0:0")))

            hierarchies.append({
                "blockId": current,
                "blockName": block_name,
                "label": label,
                "parentHBlockId": parent_id if parent_id > 0 else None,
                "internalBlockCount": internal_count,
                "depth": depth
            })

            # Next H-block
            app.Execute(f"global0 = ObjectIDNext({current}, 1);")
            current = int(parse_float(app.Request("System", "global0+:0:0:0")))

        return {
            "success": True,
            "hierarchies": hierarchies,
            "count": len(hierarchies)
        }
    except Exception as e:
        return _error(ErrorCode.COM_ERROR, str(e),
                      operation="hierarchy_list")


def hierarchy_get_contents(block_id: int,
                           model_id: Optional[str] = None) -> dict:
    """Gets blocks and connections inside an H-block.

    Validates block is H-block via GetBlockTypeNumeric()==4,
    then uses LocalNumBlocks2/LocalToGlobal2 for internal blocks.
    """
    try:
        app = get_extendsim_app()

        # Validate it's an H-block (type 4)
        app.Execute(f'global0 = GetBlockTypeNumeric({block_id});')
        block_type = int(parse_float(app.Request("System", "global0+:0:0:0")))
        if block_type != 4:
            return _error(ErrorCode.NOT_AN_HBLOCK,
                         f"Block {block_id} is not an H-block (type={block_type}, expected 4)",
                         blockId=block_id)

        # Get internal block count
        app.Execute(f'global0 = LocalNumBlocks2({block_id});')
        internal_count = int(parse_float(app.Request("System", "global0+:0:0:0")))

        # Collect all internal block global IDs
        internal_ids = set()
        blocks = []
        for i in range(internal_count):
            app.Execute(f'global0 = LocalToGlobal2({block_id}, {i});')
            gid = int(parse_float(app.Request("System", "global0+:0:0:0")))
            if gid <= 0:
                continue
            internal_ids.add(gid)

            # Get block info
            app.Execute(f'globalStr0 = BlockName({gid});')
            name = app.Request("System", "globalStr0+:0:0:0") or ""

            app.Execute(f'globalStr0 = GetBlockLabel({gid});')
            label = app.Request("System", "globalStr0+:0:0:0") or ""

            app.Execute(f'globalStr0 = GetLibraryPathName({gid}, 2);')
            lib = app.Request("System", "globalStr0+:0:0:0") or ""

            app.Execute(f'global0 = GetBlockTypeNumeric({gid});')
            gtype = int(parse_float(app.Request("System", "global0+:0:0:0")))

            blocks.append({
                "blockId": gid,
                "localIndex": i,
                "blockName": name,
                "label": label,
                "library": lib,
                "isHBlock": gtype == 4
            })

        # Get connections using NodeGetIDIndex (same pattern as connection_list)
        node_map = {}  # nodeIndex -> [(blockId, connIdx, connName)]
        for gid in internal_ids:
            try:
                app.Execute(f'global0 = GetNumCons({gid});')
                num_cons = int(parse_float(app.Request("System", "global0+:0:0:0")))
                for conn_idx in range(num_cons):
                    app.Execute(f'global0 = NodeGetIDIndex({gid}, {conn_idx});')
                    node_index = int(parse_float(app.Request("System", "global0+:0:0:0")))
                    if node_index == 0:
                        continue
                    try:
                        app.Execute(f'globalStr0 = GetConName({gid}, {conn_idx});')
                        con_name = app.Request("System", "globalStr0+:0:0:0") or ""
                    except Exception:
                        con_name = ""
                    if node_index not in node_map:
                        node_map[node_index] = []
                    node_map[node_index].append((gid, conn_idx, con_name))
            except Exception:
                pass

        connections = []
        for ni, endpoints in node_map.items():
            # Filter to connections where both endpoints are internal
            internal_eps = [ep for ep in endpoints if ep[0] in internal_ids]
            if len(internal_eps) == 2:
                ep0, ep1 = internal_eps[0], internal_eps[1]
                # Put "out" first
                if "in" in ep0[2].lower() and "out" in ep1[2].lower():
                    ep0, ep1 = ep1, ep0
                connections.append({
                    "sourceBlockId": ep0[0],
                    "sourceConnector": ep0[1],
                    "sourceConnectorName": ep0[2],
                    "targetBlockId": ep1[0],
                    "targetConnector": ep1[1],
                    "targetConnectorName": ep1[2]
                })

        return {
            "success": True,
            "blockId": block_id,
            "blocks": blocks,
            "connections": connections,
            "blockCount": len(blocks),
            "connectionCount": len(connections)
        }
    except Exception as e:
        return _error(ErrorCode.COM_ERROR, str(e),
                      blockId=block_id, operation="hierarchy_get_contents")


def optimizer_set_config(block_id: int,
                         population_size: Optional[int] = None,
                         max_generations: Optional[int] = None,
                         convergence_percent: Optional[float] = None,
                         min_generations: Optional[int] = None,
                         max_sample_size: Optional[int] = None,
                         truncate: Optional[bool] = None,
                         truncate_percent: Optional[float] = None,
                         antithetic: Optional[bool] = None,
                         show_plotter: Optional[bool] = None,
                         model_id: Optional[str] = None) -> dict:
    """Configures an Optimizer block (Analysis.lbr).

    Verified variable names from block_discover_variables:
    PopulationSizeInit, TermGenerations, TermKappaPercent,
    MinGenerations, MaxSampleSizeDialog, Truncate, TruncatePercent,
    Antithetic, ShowPlotter
    """
    try:
        app = get_extendsim_app()

        # Validate block type
        check = _validate_block_type(app, block_id, "Optimizer")
        if not check["success"]:
            return check

        if population_size is not None:
            _set_var(app, block_id, "PopulationSizeInit", population_size)

        if max_generations is not None:
            _set_var(app, block_id, "TermGenerations", max_generations)

        if convergence_percent is not None:
            _set_var(app, block_id, "TermKappaPercent", convergence_percent)

        if min_generations is not None:
            _set_var(app, block_id, "MinGenerations", min_generations)

        if max_sample_size is not None:
            _set_var(app, block_id, "MaxSampleSizeDialog", max_sample_size)

        if truncate is not None:
            _set_var(app, block_id, "Truncate", 1 if truncate else 0)

        if truncate_percent is not None:
            _set_var(app, block_id, "TruncatePercent", truncate_percent)

        if antithetic is not None:
            _set_var(app, block_id, "Antithetic", 1 if antithetic else 0)

        if show_plotter is not None:
            _set_var(app, block_id, "ShowPlotter", 1 if show_plotter else 0)

        return {
            "success": True,
            "blockId": block_id,
            "populationSize": population_size,
            "maxGenerations": max_generations,
            "convergencePercent": convergence_percent,
            "minGenerations": min_generations,
            "maxSampleSize": max_sample_size,
            "truncate": truncate,
            "truncatePercent": truncate_percent,
            "antithetic": antithetic,
            "showPlotter": show_plotter
        }
    except Exception as e:
        return _error(ErrorCode.SET_VALUE_FAILED, str(e),
                      blockId=block_id, operation="optimizer_set_config")


def optimizer_run(model_id: Optional[str] = None,
                  timeout: int = 600,
                  wait_for_completion: bool = False) -> dict:
    """Runs optimization via ExtendSim's built-in optimizer.

    Uses ExecuteMenuCommand(6002) which triggers the Optimizer block.
    COM call returns immediately.

    Args:
        timeout: Max seconds to wait when wait_for_completion=True (default 600 = 10 minutes)
        wait_for_completion: If False (default), returns immediately after starting.
            Use optimizer_get_results to check results when done.
            If True, polls GetSimulationPhase() until idle (legacy behavior).
    """
    import time
    try:
        app = get_extendsim_app()

        # Start background dialog auto-dismisser for COM error dialogs
        _start_dialog_auto_dismisser(duration_sec=timeout, poll_sec=3.0)

        # Trigger optimization/scenario run
        app.Execute("ExecuteMenuCommand(6002)")

        # Fire-and-forget mode: return immediately
        if not wait_for_completion:
            return {
                "success": True,
                "status": "started",
                "message": "Optimizer started. Use simulation_status to check if running, "
                           "then optimizer_get_results to collect results."
            }

        # Legacy blocking mode: poll until simulation phase returns to idle (0)
        start_time = time.time()
        started = False
        while True:
            elapsed = time.time() - start_time
            if elapsed > timeout:
                return _error(ErrorCode.OPTIMIZER_TIMEOUT,
                             f"Optimization timed out after {timeout}s",
                             elapsedSeconds=elapsed)

            time.sleep(2)  # Poll every 2 seconds
            try:
                app.Execute("globalInt0 = GetSimulationPhase();")
                phase = int(parse_float(app.Request("System", "globalInt0+:0:0:0")) or 0)
                if phase > 0:
                    started = True  # Optimizer is running
                if started and phase == 0:
                    break  # Optimizer finished (went from running -> idle)
                if not started and elapsed > 30:
                    # Optimizer never started after 30s — likely failed
                    return _error(ErrorCode.OPTIMIZER_FAILED,
                                 "Optimizer did not start within 30 seconds. "
                                 "Check that an Optimizer block exists with configured factors.",
                                 elapsedSeconds=elapsed)
            except Exception:
                # COM might be briefly unresponsive during optimization
                continue

        return {
            "success": True,
            "status": "completed",
            "elapsedSeconds": round(time.time() - start_time, 1)
        }
    except Exception as e:
        return _error(ErrorCode.OPTIMIZER_FAILED, str(e),
                      operation="optimizer_run")


def optimizer_get_results(block_id: int,
                          model_id: Optional[str] = None) -> dict:
    """Reads results from a completed Optimizer block.

    Verified output variables: Cost, RunNumber, Generation,
    RunningMean, Sample, Convergence, ElapsedTime
    """
    try:
        app = get_extendsim_app()

        # Validate block type
        check = _validate_block_type(app, block_id, "Optimizer")
        if not check["success"]:
            return check

        results = {}
        # Read numeric outputs via GetVariableNumeric (no suffix → real variables)
        for var_name in ["Cost", "RunNumber", "Generation", "RunningMean", "Sample"]:
            try:
                val_str = _get_var(app, block_id, var_name) or ""
                results[var_name] = parse_float(val_str) if val_str else None
            except Exception:
                results[var_name] = None

        # Read string outputs (edittext type)
        for var_name in ["Convergence", "ElapsedTime"]:
            try:
                app.Execute(f'globalStr0 = GetDialogVariableString({block_id}, "{var_name}", 0, 0);')
                val_str = app.Request("System", "globalStr0+:0:0:0") or ""
                results[var_name] = val_str if val_str else None
            except Exception:
                results[var_name] = None

        return {
            "success": True,
            "blockId": block_id,
            "results": results
        }
    except Exception as e:
        return _error(ErrorCode.GET_VALUE_FAILED, str(e),
                      blockId=block_id, operation="optimizer_get_results")


def scenario_manager_set_config(block_id: int,
                                runs_per_scenario: Optional[int] = None,
                                confidence_interval: Optional[float] = None,
                                sim_start: Optional[float] = None,
                                sim_end: Optional[float] = None,
                                report_details: Optional[bool] = None,
                                save_scenarios: Optional[bool] = None,
                                model_id: Optional[str] = None) -> dict:
    """Configures a Scenario Manager block (Analysis.lbr).

    Verified variable names from block_discover_variables:
    RunsPerScenario_prm, CI_prm, SimStart_prm, SimEnd_prm,
    ReportDetails_chk, SaveScenarios_chk
    """
    try:
        app = get_extendsim_app()

        check = _validate_block_type(app, block_id, "Scenario Manager")
        if not check["success"]:
            return check

        if runs_per_scenario is not None:
            _set_var(app, block_id, "RunsPerScenario_prm", runs_per_scenario)

        if confidence_interval is not None:
            _set_var(app, block_id, "CI_prm", confidence_interval)

        if sim_start is not None:
            _set_var(app, block_id, "SimStart_prm", sim_start)

        if sim_end is not None:
            _set_var(app, block_id, "SimEnd_prm", sim_end)

        if report_details is not None:
            _set_var(app, block_id, "ReportDetails_chk", 1 if report_details else 0)

        if save_scenarios is not None:
            _set_var(app, block_id, "SaveScenarios_chk", 1 if save_scenarios else 0)

        return {
            "success": True,
            "blockId": block_id,
            "runsPerScenario": runs_per_scenario,
            "confidenceInterval": confidence_interval,
            "simStart": sim_start,
            "simEnd": sim_end,
            "reportDetails": report_details,
            "saveScenarios": save_scenarios
        }
    except Exception as e:
        return _error(ErrorCode.SET_VALUE_FAILED, str(e),
                      blockId=block_id, operation="scenario_manager_set_config")


def _collect_sm_results(app, sm_id: int, total_scenarios: int):
    """Collect results from Scenarios_tbl after SM run completes.

    Reads all rows and columns from the Scenarios_tbl stringtable.
    Column layout (from SM source code):
      Col 0: Select (checkbox)
      Col 1: Scenario Name
      Col 2+: Factor and Response columns (dynamic)

    Uses GetDialogVariable directly since Scenarios_tbl has _tbl suffix
    which doesn't route correctly through _get_var.

    Returns list of scenario result dicts.
    """
    if total_scenarios <= 0:
        return []

    # Get column count by reading SM block variables that tell us the layout.
    # Scenarios_tbl layout: [Select][Name][Factor1..N][Response1..M]
    # Total cols = 2 + NumFactorVariables + NumDBFactorVariables + NumResponseVariables
    # We read these from the SM block to calculate total columns.
    num_cols = 2  # Minimum: col 0 (Select) + col 1 (Name)
    try:
        # Read NumFactorVariables, NumDBFactorVariables, NumResponseVariables from SM block
        num_factor = 0
        num_db_factor = 0
        num_response = 0
        try:
            num_factor = int(_get_var(app, sm_id, "NumFactorVariables"))
        except Exception:
            pass
        try:
            num_db_factor = int(_get_var(app, sm_id, "NumDBFactorVariables"))
        except Exception:
            pass
        try:
            num_response = int(_get_var(app, sm_id, "NumResponseVariables"))
        except Exception:
            pass
        calculated = 2 + num_factor + num_db_factor + num_response
        if calculated > 2:
            num_cols = calculated
    except Exception:
        pass

    # Read all scenario data (skip col 0 = checkbox)
    scenarios = []
    for row in range(total_scenarios):
        scenario = {}
        try:
            # Col 1: Scenario Name
            app.Execute(f'globalStr0 = GetDialogVariable({sm_id}, "Scenarios_tbl", {row}, 1);')
            scenario["name"] = app.Request("System", "globalStr0+:0:0:0") or ""

            # Remaining columns: factors and responses
            values = []
            for c in range(2, num_cols):
                app.Execute(f'globalStr0 = GetDialogVariable({sm_id}, "Scenarios_tbl", {row}, {c});')
                val = app.Request("System", "globalStr0+:0:0:0") or ""
                # Try to parse as number (handle Swedish decimal separator)
                if val:
                    try:
                        values.append(parse_float(val.replace(",", ".")))
                    except (ValueError, TypeError):
                        values.append(val)
                else:
                    values.append(None)
            # Trim trailing None values (SM allocates extra columns beyond actual data)
            while values and values[-1] is None:
                values.pop()
            scenario["values"] = values
        except Exception:
            scenario["error"] = "Failed to read scenario data"
        scenarios.append(scenario)

    return scenarios


def _start_dialog_auto_dismisser(duration_sec: int = 600, poll_sec: float = 2.0):
    """Start a background thread that auto-dismisses ExtendSim error dialogs.

    Runs for up to duration_sec, polling every poll_sec seconds.
    Uses the dialog_watcher.py subprocess for reliable UIA-based dismissal.
    Returns the thread (daemon, will die with process).
    """
    import threading
    import subprocess
    import os

    watcher_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dialog_watcher.py")

    def _dismiss_loop():
        import time as _time
        deadline = _time.time() + duration_sec
        while _time.time() < deadline:
            try:
                # Quick check: spawn dialog_watcher with short timeout
                result = subprocess.run(
                    ["python", "-u", watcher_script, "3", "0.5"],
                    capture_output=True, text=True, timeout=8
                )
                if result.returncode == 0:
                    try:
                        data = json.loads(result.stdout.strip())
                        if data.get("found"):
                            # Dialog dismissed — check for chained dialogs immediately
                            continue
                    except Exception:
                        pass
            except Exception:
                pass
            _time.sleep(poll_sec)

    thread = threading.Thread(target=_dismiss_loop, daemon=True)
    thread.start()
    return thread


def _detect_blocking_dialog():
    """Detect ExtendSim blocking dialogs using win32gui.

    Checks for visible ExtendSim dialog windows and reads their text.
    Returns dialog text string if found, None otherwise.
    Does NOT dismiss the dialog — just reads it.
    """
    try:
        import win32gui
        dialog_texts = []

        def enum_callback(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd):
                return True
            title = win32gui.GetWindowText(hwnd)
            cls = win32gui.GetClassName(hwnd)
            if not title:
                return True
            # ExtendSim Qt dialogs (QMessageBox)
            if title == "ExtendSim" and cls in ("Qt5QWindowIcon", "Ghost"):
                dialog_texts.append(f"[ExtendSim dialog detected, class={cls}]")
            # Standard Windows dialogs (#32770)
            elif cls == "#32770":
                texts = []
                def enum_children(child_hwnd, _):
                    child_cls = win32gui.GetClassName(child_hwnd)
                    child_text = win32gui.GetWindowText(child_hwnd)
                    if child_cls == "Static" and child_text:
                        texts.append(child_text.strip())
                    return True
                try:
                    win32gui.EnumChildWindows(hwnd, enum_children, None)
                except Exception:
                    pass
                if texts:
                    dialog_texts.append(" | ".join(texts))
            return True

        win32gui.EnumWindows(enum_callback, None)
        return dialog_texts[0] if dialog_texts else None
    except Exception:
        return None


def _read_sm_config(app, auto_select_all: bool = True):
    """Read Scenario Manager configuration before launching.

    Finds the SM block, reads runs per scenario, confidence interval,
    scenario count, and which scenarios are selected.

    The Scenarios_tbl stringtable must be accessed via direct
    GetDialogVariable/SetDialogVariable calls (not _get_var helper,
    which mis-routes the _tbl suffix to GetVariableNumeric).

    Args:
        app: ExtendSim COM object
        auto_select_all: If True and no scenarios are selected,
            automatically select all scenarios before running.

    Returns config dict or None if SM block not found.
    """
    # Find SM block (uses cache to avoid scanning 24k+ blocks repeatedly)
    sm_id = _get_sm_block_id(app)
    if sm_id is None:
        return None

    config = {"smBlockId": sm_id}

    # Read key parameters from SM block
    try:
        runs_val = _get_var(app, sm_id, "RunsPerScenario_prm")
        config["runsPerScenario"] = int(parse_float(runs_val))
    except Exception:
        config["runsPerScenario"] = None

    try:
        ci_val = _get_var(app, sm_id, "CI_prm")
        config["confidenceInterval"] = int(parse_float(ci_val))
    except Exception:
        config["confidenceInterval"] = None

    # Count total scenarios from _Scenario DB "All Scenarios" table.
    # This is stable regardless of SM run state (unlike CurrentScenarioNumber_txt
    # which changes to "X/Y" based on last run and is unreliable).
    try:
        app.Execute('global0 = DBDatabaseGetIndex("_Scenario DB");')
        db_idx = int(parse_float(app.Request("System", "global0+:0:0:0")))
        if db_idx > 0:
            app.Execute(f'global0 = DBTableGetIndex({db_idx}, "All Scenarios");')
            tbl_idx = int(parse_float(app.Request("System", "global0+:0:0:0")))
            if tbl_idx > 0:
                app.Execute(f'global0 = DBRecordsGetNum({db_idx}, {tbl_idx});')
                config["totalScenarios"] = int(parse_float(app.Request("System", "global0+:0:0:0")))
            else:
                config["totalScenarios"] = None
        else:
            config["totalScenarios"] = None
    except Exception:
        config["totalScenarios"] = None

    # Read scenario selection state from Scenarios_tbl column 0 (Select checkbox)
    # Must use GetDialogVariable directly — _get_var routes _tbl suffix wrong
    SCENARIO_SELECTCOL = 0
    total = config.get("totalScenarios") or 0
    selected_count = 0
    selected_scenarios = []
    try:
        for i in range(total):
            app.Execute(f'globalStr0 = GetDialogVariable({sm_id}, "Scenarios_tbl", {i}, {SCENARIO_SELECTCOL});')
            val = app.Request("System", "globalStr0+:0:0:0") or ""
            if val != "" and val != "0":
                selected_count += 1
                selected_scenarios.append(i)
    except Exception:
        selected_count = -1  # Could not read selection state

    config["selectedScenarios"] = selected_count
    config["selectedIndices"] = selected_scenarios

    # Auto-select all scenarios if none are selected
    if selected_count == 0 and auto_select_all and total > 0:
        try:
            for i in range(total):
                app.Execute(f'SetDialogVariable({sm_id}, "Scenarios_tbl", "1", {i}, {SCENARIO_SELECTCOL});')
            config["selectedScenarios"] = total
            config["selectedIndices"] = list(range(total))
            config["autoSelectedAll"] = True
        except Exception:
            config["autoSelectedAll"] = False

    return config


def scenario_manager_run(model_id: Optional[str] = None,
                         timeout: int = 600,
                         wait_for_completion: bool = False) -> dict:
    """Runs scenarios via ExtendSim's built-in Scenario Manager.

    Uses ExecuteMenuCommand(6002) - same as optimizer_run.
    ExtendSim determines whether to run Optimizer or Scenario Manager
    based on which Analysis block is present in the model.

    Reads SM configuration before launching and reports it in the response.

    Args:
        timeout: Max seconds to wait (default 600 = 10 minutes)
        wait_for_completion: If False (default), returns immediately after starting.
            Use scenario_manager_status to poll progress and
            scenario_manager_get_results to collect results.
            If True, blocks until SM completes (legacy behavior).
    """
    import time
    try:
        app = get_extendsim_app()

        # Read SM configuration before launching
        sm_config = _read_sm_config(app)
        if sm_config is None:
            return _error(ErrorCode.BLOCK_NOT_FOUND,
                         "No Scenario Manager block found in the model.",
                         suggestion="Add a Scenario Manager block from the Analysis library.")

        if sm_config.get("totalScenarios") is not None and sm_config["totalScenarios"] == 0:
            return _error(ErrorCode.OPTIMIZER_FAILED,
                         "Scenario Manager has no scenarios configured.",
                         smBlockId=sm_config["smBlockId"],
                         suggestion="Open the Scenario Manager and add scenarios on the Scenarios tab.")

        if sm_config.get("selectedScenarios") == 0 and not sm_config.get("autoSelectedAll"):
            return _error(ErrorCode.OPTIMIZER_FAILED,
                         "No scenarios are selected in the Scenario Manager.",
                         smBlockId=sm_config["smBlockId"],
                         smConfig=sm_config,
                         suggestion="Select scenarios on the Scenarios tab or set auto_select_all=True.")

        # If not all scenarios are selected, select all before running
        total = sm_config.get("totalScenarios") or 0
        selected = sm_config.get("selectedScenarios") or 0
        if selected < total and total > 0:
            sm_id = sm_config["smBlockId"]
            SCENARIO_SELECTCOL = 0
            try:
                for i in range(total):
                    app.Execute(f'SetDialogVariable({sm_id}, "Scenarios_tbl", "1", {i}, {SCENARIO_SELECTCOL});')
                sm_config["selectedScenarios"] = total
                sm_config["selectedIndices"] = list(range(total))
                sm_config["autoSelectedAll"] = True
            except Exception:
                pass  # Continue with whatever was selected

        # Start background dialog auto-dismisser to handle COM error dialogs
        # (sCode: 80004003) that appear during SM scenario transitions on large models.
        # These are internal ExtendSim errors that don't prevent SM from completing.
        _start_dialog_auto_dismisser(duration_sec=timeout, poll_sec=3.0)

        app.Execute("ExecuteMenuCommand(6002)")

        # Fire-and-forget mode: return immediately
        if not wait_for_completion:
            return {
                "success": True,
                "status": "started",
                "smConfig": sm_config,
                "message": "Scenario Manager started. Use scenario_manager_status to poll progress, "
                           "then scenario_manager_get_results to collect results."
            }

        # Legacy blocking mode: poll using CurrentScenarioNumber_txt
        # instead of GetSimulationPhase() which triggers COM errors (sCode: 80004003)
        # during scenario transitions on large models.
        sm_id = sm_config["smBlockId"]
        start_time = time.time()
        started = False
        last_scenario = ""
        consecutive_failures = 0
        while True:
            elapsed = time.time() - start_time
            if elapsed > timeout:
                return _error(ErrorCode.OPTIMIZER_TIMEOUT,
                             f"Scenario run timed out after {timeout}s",
                             elapsedSeconds=elapsed,
                             lastScenario=last_scenario,
                             smConfig=sm_config)

            time.sleep(3)
            try:
                # Read current scenario progress — lighter than GetSimulationPhase
                app.Execute(f'globalStr0 = GetDialogVariable({sm_id}, "CurrentScenarioNumber_txt", 0, 0);')
                scenario_txt = app.Request("System", "globalStr0+:0:0:0") or ""
                consecutive_failures = 0  # Reset on success

                if scenario_txt and scenario_txt != last_scenario:
                    last_scenario = scenario_txt
                    started = True

                # Check if SM is done: scenario text shows "X/Y" where X == Y
                # and simulation phase is idle
                if started and scenario_txt:
                    try:
                        parts = scenario_txt.split("/")
                        if len(parts) == 2:
                            current = int(parts[0])
                            total = int(parts[1])
                            if current >= total:
                                # Final scenario reached — check if simulation is idle
                                time.sleep(3)
                                app.Execute("globalInt0 = GetSimulationPhase();")
                                phase = int(parse_float(app.Request("System", "globalInt0+:0:0:0")) or 0)
                                if phase == 0:
                                    break  # SM finished
                    except (ValueError, IndexError):
                        pass

                if not started and elapsed > 120:
                    return _error(ErrorCode.OPTIMIZER_FAILED,
                                 "Scenario Manager did not start within 120 seconds. "
                                 "Check that scenarios are enabled on the Scenarios tab.",
                                 elapsedSeconds=elapsed,
                                 smConfig=sm_config)
            except Exception:
                consecutive_failures += 1
                # During SM run, COM can be temporarily unstable — tolerate up to 10 failures
                if not started and consecutive_failures >= 3:
                    dialog_text = _detect_blocking_dialog()
                    if dialog_text:
                        return _error(ErrorCode.OPTIMIZER_FAILED,
                                     f"Scenario Manager blocked by dialog: {dialog_text}",
                                     elapsedSeconds=round(time.time() - start_time, 1),
                                     smConfig=sm_config,
                                     suggestion="Check scenario selection on the Scenarios tab.")
                if consecutive_failures >= 10:
                    return _error(ErrorCode.OPTIMIZER_FAILED,
                                 "Lost COM connection during Scenario Manager run after "
                                 f"{consecutive_failures} consecutive failures.",
                                 elapsedSeconds=round(time.time() - start_time, 1),
                                 lastScenario=last_scenario,
                                 smConfig=sm_config)
                continue

        # Collect results from Scenarios_tbl
        results = _collect_sm_results(app, sm_config["smBlockId"],
                                      sm_config.get("totalScenarios") or 0)

        return {
            "success": True,
            "status": "completed",
            "elapsedSeconds": round(time.time() - start_time, 1),
            "lastScenario": last_scenario,
            "smConfig": sm_config,
            "results": results
        }
    except Exception as e:
        return _error(ErrorCode.OPTIMIZER_FAILED, str(e),
                      operation="scenario_manager_run")


# Cache SM block ID to avoid scanning 24k+ blocks on every status poll
_cached_sm_block_id: Optional[int] = None


def _get_sm_block_id(app) -> Optional[int]:
    """Get SM block ID, using cache if available."""
    global _cached_sm_block_id
    if _cached_sm_block_id is not None:
        # Verify cached ID is still a Scenario Manager block
        try:
            app.Execute(f"globalStr0 = BlockName({_cached_sm_block_id});")
            name = app.Request("System", "globalStr0+:0:0:0") or ""
            if name == "Scenario Manager":
                return _cached_sm_block_id
        except Exception:
            pass
        _cached_sm_block_id = None

    # Full scan
    sm_blocks = _enumerate_blocks_by_name(app, {"Scenario Manager"})
    if sm_blocks:
        _cached_sm_block_id = sm_blocks[0][0]
        return _cached_sm_block_id
    return None


def scenario_manager_status(model_id: Optional[str] = None) -> dict:
    """Returns the current status of a running Scenario Manager.

    Reads CurrentScenarioNumber_txt and CurrentRunNumber_txt from the SM block,
    plus GetSimulationPhase() to determine if SM is still running.
    """
    try:
        app = get_extendsim_app()

        # Find SM block (cached — avoids scanning 24k+ blocks on every poll)
        sm_id = _get_sm_block_id(app)
        if sm_id is None:
            return _error(ErrorCode.BLOCK_NOT_FOUND,
                         "No Scenario Manager block found in the model.")

        # Read scenario progress
        scenario_txt = ""
        run_txt = ""
        try:
            app.Execute(f'globalStr0 = GetDialogVariable({sm_id}, "CurrentScenarioNumber_txt", 0, 0);')
            scenario_txt = app.Request("System", "globalStr0+:0:0:0") or ""
        except Exception:
            pass

        try:
            app.Execute(f'globalStr0 = GetDialogVariable({sm_id}, "CurrentRunNumber_txt", 0, 0);')
            run_txt = app.Request("System", "globalStr0+:0:0:0") or ""
        except Exception:
            pass

        # Read simulation phase
        phase = 0
        try:
            app.Execute("globalInt0 = GetSimulationPhase();")
            phase = int(parse_float(app.Request("System", "globalInt0+:0:0:0")) or 0)
        except Exception:
            pass

        # Determine if running based on simulation phase
        # Note: CurrentScenarioNumber_txt format is "X/Y" but the meaning of X and Y
        # varies (can be scenarioIndex/totalSelected). Phase is the reliable indicator.
        running = phase > 0

        result = {
            "success": True,
            "running": running,
            "currentScenario": scenario_txt,
            "currentRun": run_txt,
            "phase": phase
        }

        if not running:
            result["message"] = "Scenario Manager run complete. Use scenario_manager_get_results to collect results."

        return result
    except Exception as e:
        return _error(ErrorCode.OPTIMIZER_FAILED, str(e),
                      operation="scenario_manager_status")


def scenario_manager_get_results(model_id: Optional[str] = None) -> dict:
    """Collects results from a completed Scenario Manager run.

    Reads all scenario data from the SM block's Scenarios_tbl, including
    factor values and response values for each scenario.
    """
    try:
        app = get_extendsim_app()

        # Find SM block and get total scenarios
        sm_config = _read_sm_config(app, auto_select_all=False)
        if sm_config is None:
            return _error(ErrorCode.BLOCK_NOT_FOUND,
                         "No Scenario Manager block found in the model.")

        total = sm_config.get("totalScenarios") or 0
        if total == 0:
            return _error(ErrorCode.OPTIMIZER_FAILED,
                         "No scenarios found in Scenario Manager.")

        results = _collect_sm_results(app, sm_config["smBlockId"], total)

        return {
            "success": True,
            "totalScenarios": total,
            "smConfig": sm_config,
            "scenarios": results
        }
    except Exception as e:
        return _error(ErrorCode.OPTIMIZER_FAILED, str(e),
                      operation="scenario_manager_get_results")


def analysis_manager_set_config(block_id: int,
                                enable_db_responses: Optional[bool] = None,
                                enable_block_responses: Optional[bool] = None,
                                enable_reliability_responses: Optional[bool] = None,
                                enable_db_factors: Optional[bool] = None,
                                enable_block_factors: Optional[bool] = None,
                                enable_reliability_factors: Optional[bool] = None,
                                enable_results_table: Optional[bool] = None,
                                auto_export: Optional[bool] = None,
                                model_id: Optional[str] = None) -> dict:
    """Configures an Analysis Manager block (Analysis.lbr).

    Exposes key data collection toggles. Remaining 176 variables
    can be set via block_set_value.

    Verified variable names from block_discover_variables:
    RSP_Db_chk, RSP_Blk_chk, RSP_Rel_chk,
    FACT_Db_chk, FACT_Blk_chk, FACT_Rel_chk,
    RSLT_ResultsTbl_chk, OPT_Export_AutoExport_chk
    """
    try:
        app = get_extendsim_app()

        check = _validate_block_type(app, block_id, "Analysis Manager")
        if not check["success"]:
            return check

        if enable_db_responses is not None:
            _set_var(app, block_id, "RSP_Db_chk", 1 if enable_db_responses else 0)

        if enable_block_responses is not None:
            _set_var(app, block_id, "RSP_Blk_chk", 1 if enable_block_responses else 0)

        if enable_reliability_responses is not None:
            _set_var(app, block_id, "RSP_Rel_chk", 1 if enable_reliability_responses else 0)

        if enable_db_factors is not None:
            _set_var(app, block_id, "FACT_Db_chk", 1 if enable_db_factors else 0)

        if enable_block_factors is not None:
            _set_var(app, block_id, "FACT_Blk_chk", 1 if enable_block_factors else 0)

        if enable_reliability_factors is not None:
            _set_var(app, block_id, "FACT_Rel_chk", 1 if enable_reliability_factors else 0)

        if enable_results_table is not None:
            _set_var(app, block_id, "RSLT_ResultsTbl_chk", 1 if enable_results_table else 0)

        if auto_export is not None:
            _set_var(app, block_id, "OPT_Export_AutoExport_chk", 1 if auto_export else 0)

        return {
            "success": True,
            "blockId": block_id,
            "enableDbResponses": enable_db_responses,
            "enableBlockResponses": enable_block_responses,
            "enableReliabilityResponses": enable_reliability_responses,
            "enableDbFactors": enable_db_factors,
            "enableBlockFactors": enable_block_factors,
            "enableReliabilityFactors": enable_reliability_factors,
            "enableResultsTable": enable_results_table,
            "autoExport": auto_export
        }
    except Exception as e:
        return _error(ErrorCode.SET_VALUE_FAILED, str(e),
                      blockId=block_id, operation="analysis_manager_set_config")


# ============================================================================
# BLOCK_CONFIGURE - Universal block configuration (v1.7)
# ============================================================================

# Maps BlockName() return value → existing COMMANDS key(s)
BLOCK_TYPE_MAP = {
    "Activity": ["activity_set_delay"],
    "Queue": ["queue_set_priority", "queue_set_resource_pool"],
    "Create": ["create_set_arrivals"],
    "Gate": ["gate_set_condition"],
    "Select Item Out": ["select_item_out_set_mode"],
    "Select Item In": ["select_item_in_set_mode"],
    "Batch": ["batch_set_config"],
    "Unbatch": ["unbatch_set_config"],
    "Resource Pool": ["resource_pool_set_config"],
    "Resource Pool Release": ["resource_pool_release_set_config"],
    "Workstation": ["workstation_set_config"],
    "Equation": ["equation_set_formula"],
    "Equation(I)": ["equation_i_set_formula"],
    "Queue Equation": ["queue_equation_set_config"],
    "Shift": ["shift_set_schedule"],
    "Transport": ["transport_set_config"],
    "Convey Item": ["convey_item_set_config"],
    "Shutdown": ["shutdown_set_config"],
    "Tank": ["tank_set_config"],
    "Valve": ["valve_set_config"],
    "Merge": ["merge_set_config"],
    "Diverge": ["diverge_set_config"],
    "Interchange": ["interchange_set_config"],
    "Convey Flow": ["convey_flow_set_config"],
    "Change Units": ["change_units_set_config"],
    "Bias": ["bias_set_config"],
    "Catch Flow": ["catch_flow_set_config"],
    "Throw Flow": ["throw_flow_set_config"],
    "History(R)": ["history_r_set_config"],
    "Throw Item": ["throw_item_set_config"],
    "Catch Item": ["catch_item_set_config"],
    "Clear Statistics": ["clear_statistics_set_config"],
    "Information": ["information_set_config"],
    "Mean & Variance": ["mean_variance_set_config"],
    "Line Chart": ["line_chart_set_config"],
    "Histogram": ["histogram_set_config"],
    "Resource Item": ["resource_item_set_config"],
    "Get(R)": ["get_r_set_config"],
    "Set(R)": ["set_r_set_config"],
    "Optimizer": ["optimizer_set_config"],
    "Scenario Manager": ["scenario_manager_set_config"],
    "Analysis Manager": ["analysis_manager_set_config"],
}

# Help reference: parameter names + descriptions per block type
BLOCK_PARAMS = {
    "Activity": {
        "params": {
            "delayType": "fixed|distribution|connector|attribute (default: fixed)",
            "value": "Fixed delay value (for delayType=fixed)",
            "distribution": "constant|uniform|triangular|normal|exponential|erlang|gamma|weibull|lognormal|beta|pearson5|pearson6",
            "arg1": "Distribution arg 1 (e.g., min, mean)",
            "arg2": "Distribution arg 2 (e.g., max, stddev)",
            "arg3": "Distribution arg 3 (e.g., mode for triangular)",
            "maxItems": "Maximum items in activity (parallel processing capacity)",
            "preemptEnabled": "true|false — enable preemption via PE connector",
            "shutdownEnabled": "true|false — enable shutdown via SD connector",
            "costPerTime": "Cost per time unit (ABC costing)",
            "costPerItem": "Cost per item processed (ABC costing)",
            "costTimeUnit": "Popup index for costing time unit (CostingTimeUnit_pop)",
            "shift": "Popup index for shift block reference (Shift_pop)"
        }
    },
    "Queue": {
        "params": {
            "rankType": "fifo|lifo|attribute|priority (default: fifo)",
            "sortAttribute": "Attribute name to sort by (for rankType=attribute)",
            "ascending": "true|false (default: true)",
            "resourcePoolBlockId": "Resource Pool block ID (enables resource requirement)",
            "resourcesNeeded": "Number of resources needed per item (default: 1)",
            "maxLength": "Maximum queue length (0=unlimited). Items rejected when full.",
            "renegeEnabled": "true|false — enable reneging (items leave after timeout)",
            "renegeTime": "Time before item reneges (leaves queue)",
            "calcWaitCosts": "true|false — enable wait cost calculation (ABC)",
            "shift": "Popup index for shift block reference (Shift_pop)",
            "calcDelay": "true|false — calculate delay for animation"
        },
        "note": "maxLength/renege params can be combined with ranking and resource params."
    },
    "Create": {
        "params": {
            "arrivalType": "schedule|distribution|connector|database|infinite (default: distribution)",
            "distribution": "exponential|constant|uniform|triangular|normal|... (default: exponential)",
            "arg1": "Distribution arg 1 (e.g., mean for exponential)",
            "arg2": "Distribution arg 2",
            "arg3": "Distribution arg 3",
            "maxArrivals": "Maximum items to create (omit for infinite)",
            "costPerTime": "Cost per time unit (ABC costing)",
            "costPerItem": "Cost per item created (ABC costing)",
            "costTimeUnit": "Popup index for costing time unit (Cost_PerTime_pop)"
        },
        "note": "arrivalType popup index 5 = infinite supply (pull-based, no arrivals until downstream demand)."
    },
    "Gate": {
        "params": {
            "demandType": "passing|waiting|value (default: passing)",
            "initialState": "opened|closed (default: opened)",
            "openValue": "Value that opens gate (default: 1)",
            "closeValue": "Value that closes gate (default: 0)"
        }
    },
    "Select Item Out": {
        "params": {
            "mode": "random|sequential|conditional|attribute|probability",
            "attributeName": "Attribute to route by (for mode=attribute)",
            "probabilities": "[0.5, 0.3, 0.2] - array of probabilities (for mode=probability)",
            "ifBlocked": "tryUnblocked|wait — what to do when selected output is blocked",
            "predictPath": "true|false — predict item path before entry"
        }
    },
    "Select Item In": {
        "params": {
            "mode": "first_available|priority|longest_waiting"
        }
    },
    "Batch": {
        "params": {
            "batchType": "batch|match",
            "batchSize": "Number of items to batch together",
            "preserveUniqueness": "true|false",
            "matchAttribute": "Attribute name to match by (for batchType=match)",
            "showDemandConnector": "true|false — show demand connector",
            "demandConnectorValue": "Popup index for demand connector value type",
            "allowZeroBatchSize": "true|false — allow zero batch size",
            "batchSizeWhen": "Popup index for when to evaluate batch size"
        }
    },
    "Unbatch": {
        "params": {
            "preserveUniqueness": "true|false",
            "quantityPerOutput": "Number of items per output",
            "costType": "Popup index for cost handling (UnbatchCostType_pop)",
            "usePreservedQuantity": "true|false — use preserved quantity from batch",
            "duplicatePreserved": "true|false — duplicate preserved items",
            "quantityOut": "true|false — enable quantity output connector"
        }
    },
    "Resource Pool": {
        "params": {
            "poolName": "Name of the resource pool",
            "initialResources": "Number of initial resources",
            "allocationRule": "random|priority|cyclical|longest_idle"
        }
    },
    "Resource Pool Release": {
        "params": {
            "poolName": "Resource Pool name to release to (required)",
            "releaseQuantity": "Number of resources to release per item"
        }
    },
    "Workstation": {
        "params": {
            "maxServers": "Number of parallel servers",
            "maxQueueLength": "Maximum queue capacity",
            "delayType": "fixed|distribution|connector|attribute (default: fixed)",
            "distribution": "Distribution name for process time",
            "arg1": "Distribution arg 1",
            "arg2": "Distribution arg 2",
            "arg3": "Distribution arg 3",
            "value": "Fixed delay value",
            "costPerTime": "Cost per time unit",
            "costPerItem": "Cost per item processed"
        }
    },
    "Equation": {
        "params": {
            "equation": "Equation text (e.g. 'o0 = i0 * 2 + i1')"
        }
    },
    "Equation(I)": {
        "params": {
            "equation": "ModL equation referencing item attributes (e.g. 'priority = 1;')",
            "showInputNames": "true|false — show input connector names on block",
            "showInputValues": "true|false — show input connector values on block",
            "showOutputNames": "true|false — show output connector names on block",
            "showOutputValues": "true|false — show output connector values on block",
            "outputInitValue": "Initial value for output variables (OVars_Initialize_prm)",
            "includeEnabled": "true|false — enable include files",
            "expandRecords": "true|false — expand records"
        },
        "note": "I/O variable tables (IVars_ttbl, OVars_ttbl) and include file names (Incl_FileNames_ttbl) are string tables. Use block_set_value with the table name + row/col to manage entries."
    },
    "Queue Equation": {
        "params": {
            "equation": "ModL equation using QEQ variables (e.g. 'QEQItemRank = DueDate - RemainingTime;')",
            "releaseRule": "highestRank|lowestRank|firstTrue|allTrue"
        }
    },
    "Shift": {
        "params": {
            "schedule": "[{startTime, endTime, capacity}, ...] - array of schedule periods",
            "statusType": "Popup index for shift status type: on/off vs numeric (ShiftStatusTypePop)",
            "shiftName": "Shift name string (for reference by other blocks)",
            "repeat": "true|false — repeat shift schedule",
            "repeatTime": "Repeat interval value",
            "repeatUnit": "Popup index for repeat time unit",
            "timeUnit": "Popup index for shift time unit",
            "timeFormat": "Popup index for time format display"
        }
    },
    "Transport": {
        "params": {
            "defaultDistance": "Default transport distance",
            "defaultSpeed": "Default transport speed"
        }
    },
    "Convey Item": {
        "params": {
            "conveyorLength": "Total conveyor length",
            "defaultSpeed": "Default conveyor speed",
            "accumulating": "true=accumulating, false=non-accumulating"
        }
    },
    "Shutdown": {
        "params": {
            "tbfDistribution": "Time Between Failures distribution",
            "tbfArg1": "TBF distribution arg 1",
            "tbfArg2": "TBF distribution arg 2",
            "ttrDistribution": "Time To Repair distribution",
            "ttrArg1": "TTR distribution arg 1",
            "ttrArg2": "TTR distribution arg 2"
        }
    },
    "Tank": {
        "params": {
            "capacity": "Maximum tank capacity",
            "initialLevel": "Initial contents level",
            "maxInputRate": "Maximum inflow rate",
            "maxOutputRate": "Maximum outflow rate",
            "flowControlEnabled": "true|false — enable flow control (FC connector)",
            "flowControlPolicy": "Popup index for flow control policy (ControlFlowValue_POP)",
            "flowControlValue": "Flow control target value"
        }
    },
    "Valve": {
        "params": {
            "maxRate": "Maximum constraining flow rate",
            "goal": "Goal quantity for the valve to pass",
            "goalType": "Popup index: 1=quantity, 2=duration, 3=hysteresis",
            "goalOffStatus": "Popup index for off behavior after goal met",
            "controlType": "Popup index for hysteresis control type",
            "startCondition": "Hysteresis start threshold value",
            "stopCondition": "Hysteresis stop threshold value",
            "shutdownCondition": "Popup index for shutdown condition",
            "pullConstraintDelay": "Pull constraint delay value (enables delay checkbox)"
        }
    },
    "Merge": {
        "params": {
            "mode": "Mode popup index (Mode_pop)",
            "initialValueSelected": "Initial selected branch index",
            "initializeSelected": "true|false — initialize selected branch at start",
            "paramFromConnectors": "true|false — get branch params from value connectors"
        },
        "note": "Per-branch proportions/priorities are in Parameters_dtbl. Use block_set_value(blockId, 'Parameters_dtbl', row=branchIndex, col=0, value=X) to set individual branch values."
    },
    "Diverge": {
        "params": {
            "mode": "Mode popup index (Mode_pop)",
            "initialValueSelected": "Initial selected branch index",
            "initializeSelected": "true|false — initialize selected branch at start",
            "paramFromConnectors": "true|false — get branch params from value connectors"
        },
        "note": "Per-branch proportions/priorities are in Parameters_dtbl. Use block_set_value(blockId, 'Parameters_dtbl', row=branchIndex, col=0, value=X) to set individual branch values."
    },
    "Interchange": {
        "params": {
            "capacity": "Maximum capacity",
            "initialLevel": "Initial contents level",
            "maxInputRate": "Maximum inflow rate",
            "maxOutputRate": "Maximum outflow rate",
            "mode": "Popup index: 1=tank exists while item, 2=tank separate from item",
            "releaseCondition": "Popup index for item release condition",
            "releaseTarget": "Target value for release (default 10)",
            "releaseInterrupt": "true|false — enable interrupt release via connector"
        }
    },
    "Convey Flow": {
        "params": {
            "speed": "Flow speed",
            "length": "Conveyor length",
            "capacityMax": "Maximum capacity",
            "accumulating": "1=accumulating, 2=non-accumulating",
            "conveyorType": "Popup index for conveyor type (ConveyorType_pop)",
            "maxDensity": "Max quantity per length unit",
            "delay": "Delay time value",
            "shift": "Popup index for shift block reference (Shift_pop)",
            "emptyWhenOffShift": "true|false — empty conveyor when shift is off",
            "attributeTransform": "Popup index for attribute transform mode"
        }
    },
    "Change Units": {
        "params": {
            "factor": "Conversion factor"
        }
    },
    "Bias": {
        "params": {
            "biasOrder": "Priority ordering value"
        }
    },
    "Catch Flow": {
        "params": {
            "position": "Position value"
        }
    },
    "Throw Flow": {
        "params": {
            "position": "Position value",
            "connectorNum": "Connector number"
        }
    },
    "History(R)": {
        "params": {
            "maxRows": "Maximum rows in history",
            "enableDatabaseLog": "true|false - enable database logging"
        }
    },
    "Throw Item": {
        "params": {
            "catchType": "Popup index for catch type (CatchType_pop)",
            "catchGroup": "Popup index for catch group (CatchGroupPop)",
            "attributeName": "Attribute name for attribute-based routing",
            "useBlockNum": "true|false — route by block number"
        }
    },
    "Catch Item": {
        "params": {
            "catchGroup": "Popup index for catch group (CatchGroupPop)",
            "countByThrow": "true|false — count items by throw block"
        }
    },
    "Clear Statistics": {
        "params": {
            "clearTime": "Time to clear statistics (warm-up period end)",
            "timeUnits": "Popup index for time units (TimeUnits_pop)",
            "clearActivity": "true|false — clear Activity statistics",
            "clearResource": "true|false — clear Resource Pool statistics",
            "clearQueue": "true|false — clear Queue statistics",
            "clearExit": "true|false — clear Exit statistics",
            "clearMeanVariance": "true|false — clear Mean & Variance statistics",
            "clearInformation": "true|false — clear Information block statistics",
            "clearRate": "true|false — clear Rate library statistics",
            "clearMaxMin": "true|false — clear Max & Min statistics"
        }
    },
    "Information": {
        "params": {
            "cycleAttribute": "Popup index for cycle time attribute (Attribcycle_POP)",
            "addCount": "true|false — enable add-count connector",
            "countByOne": "true|false — count by 1 vs connector value",
            "noReset": "true|false — don't reset on consecutive runs",
            "resetWhen": "Popup index for reset condition (ResetWhen_pop)",
            "detailedStats": "true|false — enable detailed statistics",
            "resetEvery": "true|false — enable periodic reset",
            "resetEveryInterval": "Reset interval value"
        }
    },
    "Mean & Variance": {
        "params": {
            "multiSim": "true|false — enable multi-simulation mode",
            "weight": "true|false — enable weighted statistics",
            "clearTime": "Time to clear statistics",
            "confidence": "Confidence level percentage (default 95)",
            "initValue": "Popup index for initial value mode (InitValue)",
            "movingAverage": "true|false — enable moving average",
            "movingAverageInterval": "Moving average window size (default 10)",
            "recordHistory": "true|false — enable history table recording",
            "relativeError": "true|false — enable relative error checking",
            "relativeErrorThreshold": "Relative error threshold (default 0.01)"
        }
    },
    "Line Chart": {
        "params": {
            "startTime": "Data collection window start time",
            "endTime": "Data collection window end time",
            "disableRecording": "true|false — disable data recording",
            "fixedPoints": "Fixed number of points to plot (enables fixed-point mode)"
        }
    },
    "Histogram": {
        "params": {
            "numBins": "Number of bins (default 5)",
            "binSize": "Bin size",
            "xMin": "X-axis minimum value",
            "xMax": "X-axis maximum value"
        }
    },
    "Resource Item": {
        "params": {
            "initialCount": "Initial number of resource items",
            "stripAttributes": "true|false — strip attributes when released",
            "itemType": "Popup index for item type (ItemType_pop)",
            "costEnabled": "true|false — enable ABC costing",
            "costPerTime": "Cost per time unit (ABC costing)",
            "costPerItem": "Cost per item (ABC costing)",
            "costTimeUnit": "Popup index for costing time unit (CostTimeUnits_pop)",
            "shift": "Popup index for shift block reference (Shift_pop)"
        }
    },
    "Get(R)": {
        "params": {
            "locationBlockId": "Block number to read from",
            "infoType": "Popup index for information type (InformationReportedType_pop)",
            "flowAttribute": "Popup index for flow attribute (FlowAttribute_pop)"
        }
    },
    "Set(R)": {
        "params": {}
    },
    "Optimizer": {
        "params": {
            "populationSize": "Initial population size",
            "maxGenerations": "Max generations before termination",
            "convergencePercent": "Convergence threshold 0-1",
            "minGenerations": "Minimum generations before convergence check",
            "maxSampleSize": "Max sample size per generation",
            "truncate": "true|false - enable truncation selection",
            "truncatePercent": "Truncation percentage 0-1",
            "antithetic": "true|false - use antithetic variates",
            "showPlotter": "true|false - show optimization plotter"
        }
    },
    "Scenario Manager": {
        "params": {
            "runsPerScenario": "Replications per scenario",
            "confidenceInterval": "Confidence interval percentage e.g. 95",
            "simStart": "Simulation start time",
            "simEnd": "Simulation end time",
            "reportDetails": "true|false - include detailed report",
            "saveScenarios": "true|false - save scenario data"
        }
    },
    "Analysis Manager": {
        "params": {
            "enableDbResponses": "true|false",
            "enableBlockResponses": "true|false",
            "enableReliabilityResponses": "true|false",
            "enableDbFactors": "true|false",
            "enableBlockFactors": "true|false",
            "enableReliabilityFactors": "true|false",
            "enableResultsTable": "true|false",
            "autoExport": "true|false"
        }
    },
}

# Queue config param sets - used to determine which handler(s) to call
_QUEUE_PRIORITY_PARAMS = {"rankType", "sortAttribute", "ascending", "maxLength", "renegeEnabled", "renegeTime", "calcWaitCosts", "shift", "calcDelay"}
_QUEUE_RESOURCE_PARAMS = {"resourcePoolBlockId", "resourcesNeeded"}


def block_configure(block_id, config, model_id=None):
    """Universal block configuration - auto-detects block type and dispatches."""
    try:
        app = get_extendsim_app()
        if not app:
            return _error(ErrorCode.NOT_CONNECTED, "ExtendSim not connected")

        model_check = _validate_model_open(app)
        if not model_check.get("success"):
            return model_check

        # Detect block type via BlockName() → globalStr0
        app.Execute(f'globalStr0 = BlockName({block_id});')
        block_name = app.Request("System", "globalStr0+:0:0:0").strip()

        if not block_name:
            return _error(ErrorCode.BLOCK_NOT_FOUND,
                         f"Block {block_id} not found or has no type",
                         blockId=block_id)

        # Look up in type map
        if block_name not in BLOCK_TYPE_MAP:
            return _error(ErrorCode.SET_VALUE_FAILED,
                         f"Block type '{block_name}' is not supported by block_configure. "
                         f"Supported types: {', '.join(sorted(BLOCK_TYPE_MAP.keys()))}",
                         blockId=block_id, blockType=block_name)

        # If no config provided, return help for this block type
        if not config:
            help_info = BLOCK_PARAMS.get(block_name, {})
            return {
                "success": True,
                "blockId": block_id,
                "blockType": block_name,
                "mode": "help",
                "availableParams": help_info.get("params", {}),
                "note": help_info.get("note", f"Pass config object with parameters to configure this {block_name} block.")
            }

        # Validate config keys against known params for this block type
        help_info_check = BLOCK_PARAMS.get(block_name, {})
        expected_params = set(help_info_check.get("params", {}).keys())
        if expected_params:
            unknown_params = set(config.keys()) - expected_params
            if unknown_params:
                return _error(ErrorCode.INVALID_PARAMETER,
                             f"Unknown parameters for {block_name}: {', '.join(sorted(unknown_params))}. "
                             f"Valid parameters: {', '.join(sorted(expected_params))}",
                             blockId=block_id, blockType=block_name,
                             unknownParams=list(unknown_params))

        # Build merged params for dispatch
        commands = BLOCK_TYPE_MAP[block_name]

        # Special case: Queue has two handlers (priority + resource pool)
        if block_name == "Queue":
            config_keys = set(config.keys())
            has_priority = bool(config_keys & _QUEUE_PRIORITY_PARAMS)
            has_resource = bool(config_keys & _QUEUE_RESOURCE_PARAMS)

            results = []

            if has_priority or (not has_resource):
                # Call priority handler (default if no resource params)
                priority_params = {
                    "blockId": block_id,
                    "rankType": config.get("rankType", "fifo"),
                    "sortAttribute": config.get("sortAttribute"),
                    "ascending": config.get("ascending", True),
                    "modelId": model_id,
                }
                result = COMMANDS["queue_set_priority"](priority_params)
                results.append(result)

            if has_resource:
                rp_block_id = config.get("resourcePoolBlockId")
                if rp_block_id is None:
                    return _error(ErrorCode.MISSING_PARAMETER,
                                 "resourcePoolBlockId is required for Queue resource pool configuration",
                                 blockId=block_id)
                resource_params = {
                    "blockId": block_id,
                    "resourcePoolBlockId": rp_block_id,
                    "resourcesNeeded": config.get("resourcesNeeded", 1),
                    "modelId": model_id,
                }
                result = COMMANDS["queue_set_resource_pool"](resource_params)
                results.append(result)

            # Check for failures
            for r in results:
                if not r.get("success"):
                    return r

            return {
                "success": True,
                "blockId": block_id,
                "blockType": block_name,
                "configured": [r for r in results]
            }

        # All other blocks: single dispatch
        cmd = commands[0]
        merged = {"blockId": block_id, "modelId": model_id}
        merged.update(config)
        result = COMMANDS[cmd](merged)
        if result.get("success"):
            result["blockType"] = block_name
        return result

    except Exception as e:
        return _error(ErrorCode.SET_VALUE_FAILED, str(e),
                      blockId=block_id, operation="block_configure")


# ============================================================================
# AI CONTEXT PERSISTENCE
# ============================================================================

AI_CONTEXT_DB_NAME = "AI_Context"
AI_CONTEXT_TABLE = "context"
AI_HISTORY_TABLE = "changeHistory"


def _ensure_context_db(app):
    """Create AI_Context database and tables if they don't exist.

    Returns (db_idx, ctx_tbl_idx, hist_tbl_idx) or raises on failure.
    """
    # Check if DB exists
    app.Execute(f'globalInt0 = DBDatabaseGetIndex("{AI_CONTEXT_DB_NAME}");')
    db_idx = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))

    if db_idx < 0:
        # Create database
        app.Execute(f'globalInt0 = DBDatabaseCreate("{AI_CONTEXT_DB_NAME}");')
        db_idx = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))
        if db_idx < 0:
            raise RuntimeError("Failed to create AI_Context database")

    # Check/create "context" table
    app.Execute(f'globalInt0 = DBTableGetIndex({db_idx}, "{AI_CONTEXT_TABLE}");')
    ctx_tbl_idx = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))

    if ctx_tbl_idx < 0:
        app.Execute(f'globalInt0 = DBTableCreate({db_idx}, "{AI_CONTEXT_TABLE}");')
        ctx_tbl_idx = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))
        if ctx_tbl_idx < 0:
            raise RuntimeError("Failed to create context table")
        # Create fields: key (string, format=4), value (string, format=4)
        # DBFieldCreate(dbIdx, tblIdx, fieldName, format, decimals, unique, readOnly, invisible)
        # format 4 = string
        app.Execute(f'DBFieldCreate({db_idx}, {ctx_tbl_idx}, "key", 4, 0, 0, 0, 0);')
        app.Execute(f'DBFieldCreate({db_idx}, {ctx_tbl_idx}, "value", 4, 0, 0, 0, 0);')

    # Check/create "changeHistory" table
    app.Execute(f'globalInt0 = DBTableGetIndex({db_idx}, "{AI_HISTORY_TABLE}");')
    hist_tbl_idx = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))

    if hist_tbl_idx < 0:
        app.Execute(f'globalInt0 = DBTableCreate({db_idx}, "{AI_HISTORY_TABLE}");')
        hist_tbl_idx = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))
        if hist_tbl_idx < 0:
            raise RuntimeError("Failed to create changeHistory table")
        # Fields: timestamp (string), summary (string), details (string)
        app.Execute(f'DBFieldCreate({db_idx}, {hist_tbl_idx}, "timestamp", 4, 0, 0, 0, 0);')
        app.Execute(f'DBFieldCreate({db_idx}, {hist_tbl_idx}, "summary", 4, 0, 0, 0, 0);')
        app.Execute(f'DBFieldCreate({db_idx}, {hist_tbl_idx}, "details", 4, 0, 0, 0, 0);')

    return db_idx, ctx_tbl_idx, hist_tbl_idx


def _context_get_field_idx(app, db_idx, tbl_idx, field_name):
    """Get field index by name. Returns int >= 0 or raises."""
    app.Execute(f'globalInt0 = DBFieldGetIndex({db_idx}, {tbl_idx}, "{field_name}");')
    fld_idx = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))
    if fld_idx < 0:
        raise RuntimeError(f"Field '{field_name}' not found")
    return fld_idx


def _context_upsert(app, db_idx, tbl_idx, key, value):
    """Find row with matching key, update value. Insert new row if not found."""
    key_fld = _context_get_field_idx(app, db_idx, tbl_idx, "key")
    val_fld = _context_get_field_idx(app, db_idx, tbl_idx, "value")

    app.Execute(f'globalInt0 = DBRecordsGetNum({db_idx}, {tbl_idx});')
    num_records = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))

    escaped_val = _escape_modl_string(str(value))

    for i in range(num_records):
        app.Execute(f'globalStr0 = DBDataGetAsString({db_idx}, {tbl_idx}, {key_fld}, {i});')
        existing_key = app.Request("System", "globalStr0+:0:0:0")
        if existing_key == key:
            # Update existing row
            app.Execute(f'DBDataSetAsString({db_idx}, {tbl_idx}, {val_fld}, {i}, "{escaped_val}");')
            return

    # Insert new record (insertAt=0 means append at end)
    app.Execute(f'DBRecordsInsert({db_idx}, {tbl_idx}, 0, 1);')
    new_idx = num_records
    escaped_key = _escape_modl_string(str(key))
    app.Execute(f'DBDataSetAsString({db_idx}, {tbl_idx}, {key_fld}, {new_idx}, "{escaped_key}");')
    app.Execute(f'DBDataSetAsString({db_idx}, {tbl_idx}, {val_fld}, {new_idx}, "{escaped_val}");')


def _context_read_all(app):
    """Read all context from AI_Context database. Returns dict or None if DB doesn't exist."""
    app.Execute(f'globalInt0 = DBDatabaseGetIndex("{AI_CONTEXT_DB_NAME}");')
    db_idx = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))

    if db_idx < 0:
        return None

    result = {"context": {}, "changeHistory": []}

    # Read context table
    app.Execute(f'globalInt0 = DBTableGetIndex({db_idx}, "{AI_CONTEXT_TABLE}");')
    ctx_tbl_idx = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))

    if ctx_tbl_idx >= 0:
        key_fld = _context_get_field_idx(app, db_idx, ctx_tbl_idx, "key")
        val_fld = _context_get_field_idx(app, db_idx, ctx_tbl_idx, "value")

        app.Execute(f'globalInt0 = DBRecordsGetNum({db_idx}, {ctx_tbl_idx});')
        num_records = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))

        for i in range(num_records):
            app.Execute(f'globalStr0 = DBDataGetAsString({db_idx}, {ctx_tbl_idx}, {key_fld}, {i});')
            key = app.Request("System", "globalStr0+:0:0:0")
            app.Execute(f'globalStr0 = DBDataGetAsString({db_idx}, {ctx_tbl_idx}, {val_fld}, {i});')
            value = app.Request("System", "globalStr0+:0:0:0")

            # Try to parse JSON values back to structured data
            try:
                result["context"][key] = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                result["context"][key] = value

    # Read changeHistory table
    app.Execute(f'globalInt0 = DBTableGetIndex({db_idx}, "{AI_HISTORY_TABLE}");')
    hist_tbl_idx = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))

    if hist_tbl_idx >= 0:
        ts_fld = _context_get_field_idx(app, db_idx, hist_tbl_idx, "timestamp")
        sum_fld = _context_get_field_idx(app, db_idx, hist_tbl_idx, "summary")
        det_fld = _context_get_field_idx(app, db_idx, hist_tbl_idx, "details")

        app.Execute(f'globalInt0 = DBRecordsGetNum({db_idx}, {hist_tbl_idx});')
        num_records = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))

        for i in range(num_records):
            app.Execute(f'globalStr0 = DBDataGetAsString({db_idx}, {hist_tbl_idx}, {ts_fld}, {i});')
            ts = app.Request("System", "globalStr0+:0:0:0")
            app.Execute(f'globalStr0 = DBDataGetAsString({db_idx}, {hist_tbl_idx}, {sum_fld}, {i});')
            summary = app.Request("System", "globalStr0+:0:0:0")
            app.Execute(f'globalStr0 = DBDataGetAsString({db_idx}, {hist_tbl_idx}, {det_fld}, {i});')
            details = app.Request("System", "globalStr0+:0:0:0")
            result["changeHistory"].append({
                "timestamp": ts,
                "summary": summary,
                "details": details
            })

    return result


def context_get(model_id: Optional[str] = None) -> dict:
    """Read stored AI context from the model's internal database."""
    try:
        app = get_extendsim_app()
        validation = _validate_model_open(app)
        if not validation["success"]:
            return validation

        context_data = _context_read_all(app)
        if context_data is None:
            return {"success": True, "exists": False}

        return {"success": True, "exists": True, **context_data}
    except Exception as e:
        return _error(ErrorCode.DB_OPERATION_FAILED, f"Failed to read context: {e}")


def context_set(purpose: Optional[str] = None,
                key_blocks: Optional[list] = None,
                assumptions: Optional[list] = None,
                notes: Optional[str] = None,
                tags: Optional[list] = None,
                custom: Optional[dict] = None,
                change_entry: Optional[dict] = None,
                model_id: Optional[str] = None) -> dict:
    """Save or update AI context in the model's internal database."""
    try:
        app = get_extendsim_app()
        validation = _validate_model_open(app)
        if not validation["success"]:
            return validation

        db_idx, ctx_tbl_idx, hist_tbl_idx = _ensure_context_db(app)

        updated_keys = []

        # Upsert each provided field
        if purpose is not None:
            _context_upsert(app, db_idx, ctx_tbl_idx, "purpose", purpose)
            updated_keys.append("purpose")

        if key_blocks is not None:
            _context_upsert(app, db_idx, ctx_tbl_idx, "keyBlocks", json.dumps(key_blocks))
            updated_keys.append("keyBlocks")

        if assumptions is not None:
            _context_upsert(app, db_idx, ctx_tbl_idx, "assumptions", json.dumps(assumptions))
            updated_keys.append("assumptions")

        if notes is not None:
            _context_upsert(app, db_idx, ctx_tbl_idx, "notes", notes)
            updated_keys.append("notes")

        if tags is not None:
            _context_upsert(app, db_idx, ctx_tbl_idx, "tags", json.dumps(tags))
            updated_keys.append("tags")

        if custom is not None:
            for k, v in custom.items():
                val = json.dumps(v) if isinstance(v, (dict, list)) else str(v)
                _context_upsert(app, db_idx, ctx_tbl_idx, k, val)
                updated_keys.append(k)

        # Append change history entry if provided
        if change_entry is not None:
            summary = change_entry.get("summary", "")
            details = change_entry.get("details", "")
            if summary:
                from datetime import datetime, timezone
                timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

                ts_fld = _context_get_field_idx(app, db_idx, hist_tbl_idx, "timestamp")
                sum_fld = _context_get_field_idx(app, db_idx, hist_tbl_idx, "summary")
                det_fld = _context_get_field_idx(app, db_idx, hist_tbl_idx, "details")

                app.Execute(f'globalInt0 = DBRecordsGetNum({db_idx}, {hist_tbl_idx});')
                num_records = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))

                app.Execute(f'DBRecordsInsert({db_idx}, {hist_tbl_idx}, 0, 1);')
                new_idx = num_records

                app.Execute(f'DBDataSetAsString({db_idx}, {hist_tbl_idx}, {ts_fld}, {new_idx}, "{_escape_modl_string(timestamp)}");')
                app.Execute(f'DBDataSetAsString({db_idx}, {hist_tbl_idx}, {sum_fld}, {new_idx}, "{_escape_modl_string(summary)}");')
                app.Execute(f'DBDataSetAsString({db_idx}, {hist_tbl_idx}, {det_fld}, {new_idx}, "{_escape_modl_string(details)}");')
                updated_keys.append("changeHistory")

        return {"success": True, "updatedKeys": updated_keys}
    except Exception as e:
        return _error(ErrorCode.DB_OPERATION_FAILED, f"Failed to set context: {e}")


def context_clear(confirm: bool = False, model_id: Optional[str] = None) -> dict:
    """Delete the AI_Context database from the model."""
    try:
        if not confirm:
            return _error(ErrorCode.INVALID_PARAMETER,
                          "Must pass confirm=true to delete context database",
                          suggestion="Set confirm=true to confirm deletion")

        app = get_extendsim_app()
        validation = _validate_model_open(app)
        if not validation["success"]:
            return validation

        app.Execute(f'globalInt0 = DBDatabaseGetIndex("{AI_CONTEXT_DB_NAME}");')
        db_idx = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))

        if db_idx < 0:
            return {"success": True, "message": "No AI_Context database found - nothing to delete"}

        # Delete all tables first, then the database
        # Delete changeHistory table
        app.Execute(f'globalInt0 = DBTableGetIndex({db_idx}, "{AI_HISTORY_TABLE}");')
        hist_tbl_idx = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))
        if hist_tbl_idx >= 0:
            app.Execute(f'DBTableDelete({db_idx}, {hist_tbl_idx});')

        # Re-resolve db_idx after table deletion (indices may shift)
        app.Execute(f'globalInt0 = DBDatabaseGetIndex("{AI_CONTEXT_DB_NAME}");')
        db_idx = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))

        # Delete context table
        app.Execute(f'globalInt0 = DBTableGetIndex({db_idx}, "{AI_CONTEXT_TABLE}");')
        ctx_tbl_idx = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))
        if ctx_tbl_idx >= 0:
            app.Execute(f'DBTableDelete({db_idx}, {ctx_tbl_idx});')

        # Delete the database itself
        app.Execute(f'globalInt0 = DBDatabaseGetIndex("{AI_CONTEXT_DB_NAME}");')
        db_idx = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))
        if db_idx >= 0:
            app.Execute(f'DBDatabaseDelete({db_idx});')

        return {"success": True, "message": "AI_Context database deleted"}
    except Exception as e:
        return _error(ErrorCode.DB_OPERATION_FAILED, f"Failed to clear context: {e}")


# ============================================================================
# v1.10.0 — BLOCK TOOLS (H1-H5)
# ============================================================================

def block_move(block_id: int, x: int, y: int, model_id: Optional[str] = None) -> dict:
    """Moves a block to an absolute pixel position."""
    try:
        app = get_extendsim_app()
        model_check = _validate_model_open(app)
        if not model_check.get("success"):
            return model_check

        app.Execute(f"MoveBlockTo({block_id}, {x}, {y});")
        return {"success": True, "blockId": block_id, "x": x, "y": y}
    except Exception as e:
        return _com_error(e, "block_move")


def block_get_position(block_id: int, model_id: Optional[str] = None) -> dict:
    """Reads a block's position and size via GetBlockTypePosition."""
    try:
        app = get_extendsim_app()
        model_check = _validate_model_open(app)
        if not model_check.get("success"):
            return model_check

        # GetBlockTypePosition fills a 4-element array: top, left, bottom, right
        app.Execute(f"global0 = GetBlockTypePosition({block_id}, 0);")  # top
        top = parse_float(app.Request("System", "global0+:0:0:0"))
        app.Execute(f"global0 = GetBlockTypePosition({block_id}, 1);")  # left
        left = parse_float(app.Request("System", "global0+:0:0:0"))
        app.Execute(f"global0 = GetBlockTypePosition({block_id}, 2);")  # bottom
        bottom = parse_float(app.Request("System", "global0+:0:0:0"))
        app.Execute(f"global0 = GetBlockTypePosition({block_id}, 3);")  # right
        right = parse_float(app.Request("System", "global0+:0:0:0"))

        return {
            "success": True,
            "blockId": block_id,
            "x": int(left),
            "y": int(top),
            "width": int(right - left),
            "height": int(bottom - top),
            "bounds": {"top": int(top), "left": int(left), "bottom": int(bottom), "right": int(right)}
        }
    except Exception as e:
        return _com_error(e, "block_get_position")


def block_align(source_block_id: int, source_connector, target_block_id: int,
                target_connector, vertical: bool = True, model_id: Optional[str] = None) -> dict:
    """Aligns a target block so the connection line to it is straight."""
    try:
        app = get_extendsim_app()
        model_check = _validate_model_open(app)
        if not model_check.get("success"):
            return model_check

        src_con = _resolve_connector(app, source_block_id, source_connector)
        tgt_con = _resolve_connector(app, target_block_id, target_connector)

        v_flag = 1 if vertical else 0
        app.Execute(f"AlignConnection({source_block_id}, {src_con}, {target_block_id}, {tgt_con}, {v_flag});")
        return {
            "success": True,
            "sourceBlockId": source_block_id,
            "targetBlockId": target_block_id,
            "vertical": vertical
        }
    except Exception as e:
        return _com_error(e, "block_align")


def block_duplicate(block_id: int, label: Optional[str] = None,
                    model_id: Optional[str] = None) -> dict:
    """Duplicates a block with all its settings."""
    try:
        app = get_extendsim_app()
        model_check = _validate_model_open(app)
        if not model_check.get("success"):
            return model_check

        # Collect IDs before
        before_ids = set()
        current_id = -1
        while True:
            app.Execute(f"global0 = objectIDNext({current_id}, 0);")
            next_id = int(parse_float(app.Request("System", "global0+:0:0:0")))
            if next_id == -1:
                break
            before_ids.add(next_id)
            current_id = next_id

        app.Execute(f"DuplicateBlock({block_id});")

        # Find new block ID
        new_block_id = -1
        current_id = -1
        while True:
            app.Execute(f"global0 = objectIDNext({current_id}, 0);")
            next_id = int(parse_float(app.Request("System", "global0+:0:0:0")))
            if next_id == -1:
                break
            if next_id not in before_ids:
                new_block_id = next_id
            current_id = next_id

        if new_block_id < 0:
            return _error(ErrorCode.COMMAND_FAILED, f"DuplicateBlock({block_id}) did not create a new block")

        if label:
            app.Execute(f'SetBlockLabel({new_block_id}, "{_escape_modl_string(label)}");')

        return {
            "success": True,
            "originalBlockId": block_id,
            "newBlockId": new_block_id,
            "label": label or ""
        }
    except Exception as e:
        return _com_error(e, "block_duplicate")


def block_find(search_str: str, which: int = 1, model_id: Optional[str] = None) -> dict:
    """Finds a block by label (which=1) or block name (which=2)."""
    try:
        app = get_extendsim_app()
        model_check = _validate_model_open(app)
        if not model_check.get("success"):
            return model_check

        safe = _escape_modl_string(search_str)
        app.Execute(f'global0 = FindBlock("{safe}", {which}, 0, 0, 1);')
        found_id = int(parse_float(app.Request("System", "global0+:0:0:0")))

        if found_id < 0:
            search_type = "label" if which == 1 else "block name"
            return _error(ErrorCode.BLOCK_NOT_FOUND,
                          f"No block found with {search_type} '{search_str}'",
                          suggestion="Check spelling or use block_list to see all blocks.")

        # Get info about found block
        app.Execute(f"globalStr0 = BlockName({found_id});")
        block_name = app.Request("System", "globalStr0+:0:0:0")
        app.Execute(f'globalStr0 = GetBlockLabel({found_id});')
        block_label = app.Request("System", "globalStr0+:0:0:0")

        return {
            "success": True,
            "blockId": found_id,
            "blockName": block_name,
            "label": block_label,
            "searchStr": search_str,
            "searchType": "label" if which == 1 else "blockName"
        }
    except Exception as e:
        return _com_error(e, "block_find")


# ============================================================================
# v1.10.0 — DB TOOLS (H7-H10)
# ============================================================================


def db_create(database_name: str, tables: Optional[list] = None,
              model_id: Optional[str] = None) -> dict:
    """Creates a database with optional tables and fields. Idempotent — skips existing."""
    try:
        app = get_extendsim_app()
        model_check = _validate_model_open(app)
        if not model_check.get("success"):
            return model_check

        # Check if DB already exists
        safe_db = _escape_modl_string(database_name)
        app.Execute(f'globalInt0 = DBDatabaseGetIndex("{safe_db}");')
        db_idx = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))

        created_db = False
        if db_idx < 0:
            app.Execute(f'globalInt0 = DBDatabaseCreate("{safe_db}");')
            db_idx = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))
            if db_idx < 0:
                return _error(ErrorCode.DB_OPERATION_FAILED,
                              f"Failed to create database '{database_name}'")
            created_db = True

        tables_result = []
        if tables:
            for tbl in tables:
                tbl_name = tbl.get("name", "")
                safe_tbl = _escape_modl_string(tbl_name)

                # Check if table exists
                app.Execute(f'globalInt0 = DBTableGetIndex({db_idx}, "{safe_tbl}");')
                tbl_idx = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))

                created_tbl = False
                if tbl_idx < 0:
                    app.Execute(f'globalInt0 = DBTableCreate({db_idx}, "{safe_tbl}");')
                    tbl_idx = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))
                    if tbl_idx < 0:
                        tables_result.append({"name": tbl_name, "error": "Failed to create table"})
                        continue
                    created_tbl = True

                fields_result = []
                for fld in tbl.get("fields", []):
                    fld_name = fld.get("name", "")
                    fld_type_str = fld.get("type", "real")
                    fld_type = DB_FIELD_TYPE_REVERSE.get(fld_type_str, 0)
                    safe_fld = _escape_modl_string(fld_name)

                    # Check if field exists
                    app.Execute(f'globalInt0 = DBFieldGetIndex({db_idx}, {tbl_idx}, "{safe_fld}");')
                    fld_idx = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))

                    if fld_idx < 0:
                        app.Execute(f'globalInt0 = DBFieldCreate({db_idx}, {tbl_idx}, "{safe_fld}", {fld_type});')
                        fld_idx = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))
                        fields_result.append({"name": fld_name, "type": fld_type_str,
                                              "created": fld_idx >= 0, "index": fld_idx})
                    else:
                        fields_result.append({"name": fld_name, "type": fld_type_str,
                                              "created": False, "index": fld_idx, "existed": True})

                tables_result.append({
                    "name": tbl_name, "index": tbl_idx,
                    "created": created_tbl, "fields": fields_result
                })

        return {
            "success": True,
            "databaseName": database_name,
            "databaseIndex": db_idx,
            "createdDatabase": created_db,
            "tables": tables_result
        }
    except Exception as e:
        return _com_error(e, "db_create")


def db_import(file_path: str, database_name: str, table_name: str,
              delimiter: str = ",", has_header: bool = True,
              model_id: Optional[str] = None) -> dict:
    """Imports data from a file into a database table."""
    try:
        app = get_extendsim_app()
        indices = _resolve_db_indices(app, database_name, table_name)
        if not indices.get("success"):
            return indices

        db_idx = indices["dbIdx"]
        tbl_idx = indices["tblIdx"]
        safe_path = _escape_modl_string(file_path.replace("\\", "/"))

        # Negative dbIdx = first line has field names
        sign = -1 if has_header else 1
        delim_char = _escape_modl_string(delimiter)
        app.Execute(f'globalInt0 = DBTableImportData("{safe_path}", "", "{delim_char}", {sign * db_idx}, {tbl_idx});')
        result_val = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))

        # Get new record count
        app.Execute(f'globalInt0 = DBRecordsGetNum({db_idx}, {tbl_idx});')
        num_records = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))

        return {
            "success": True,
            "databaseName": database_name,
            "tableName": table_name,
            "filePath": file_path,
            "records": num_records,
            "result": result_val
        }
    except Exception as e:
        return _com_error(e, "db_import")


def db_export(file_path: str, database_name: str, table_name: str,
              delimiter: str = ",", include_header: bool = True,
              model_id: Optional[str] = None) -> dict:
    """Exports a database table to a file."""
    try:
        app = get_extendsim_app()
        indices = _resolve_db_indices(app, database_name, table_name)
        if not indices.get("success"):
            return indices

        db_idx = indices["dbIdx"]
        tbl_idx = indices["tblIdx"]
        safe_path = _escape_modl_string(file_path.replace("\\", "/"))

        # Get dimensions for the export
        app.Execute(f'globalInt0 = DBRecordsGetNum({db_idx}, {tbl_idx});')
        num_records = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))
        app.Execute(f'globalInt0 = DBFieldsGetNum({db_idx}, {tbl_idx});')
        num_fields = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))

        sign = -1 if include_header else 1
        delim_char = _escape_modl_string(delimiter)
        app.Execute(f'globalInt0 = DBTableExportData("{safe_path}", "", "{delim_char}", {sign * db_idx}, {tbl_idx}, {num_records}, {num_fields});')
        result_val = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))

        return {
            "success": True,
            "databaseName": database_name,
            "tableName": table_name,
            "filePath": file_path,
            "records": num_records,
            "fields": num_fields,
            "result": result_val
        }
    except Exception as e:
        return _com_error(e, "db_export")


def db_find_record(database_name: str, table_name: str, field_name: str,
                   find_value, exact_match: bool = True, start_record: int = 0,
                   model_id: Optional[str] = None) -> dict:
    """Searches for a record in a database table."""
    try:
        app = get_extendsim_app()
        indices = _resolve_db_indices(app, database_name, table_name, field_name)
        if not indices.get("success"):
            return indices

        db_idx = indices["dbIdx"]
        tbl_idx = indices["tblIdx"]
        fld_idx = indices["fldIdx"]

        exact = 1 if exact_match else 0

        if isinstance(find_value, str):
            safe_val = _escape_modl_string(find_value)
            app.Execute(f'globalInt0 = DBRecordFind({db_idx}, {tbl_idx}, {fld_idx}, {start_record}, {exact}, "{safe_val}");')
        else:
            app.Execute(f'globalInt0 = DBRecordFind({db_idx}, {tbl_idx}, {fld_idx}, {start_record}, {exact}, {find_value});')

        record_idx = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))

        if record_idx < 0:
            return {
                "success": True,
                "found": False,
                "record": -1,
                "message": f"Value '{find_value}' not found in field '{field_name}'"
            }

        return {
            "success": True,
            "found": True,
            "record": record_idx,
            "databaseName": database_name,
            "tableName": table_name,
            "fieldName": field_name,
            "value": find_value
        }
    except Exception as e:
        return _com_error(e, "db_find_record")


def db_sort(database_name: str, table_name: str,
            field1: str, direction1: int = 0,
            field2: Optional[str] = None, direction2: int = 0,
            field3: Optional[str] = None, direction3: int = 0,
            model_id: Optional[str] = None) -> dict:
    """Sorts a database table by up to 3 fields."""
    try:
        app = get_extendsim_app()
        indices = _resolve_db_indices(app, database_name, table_name)
        if not indices.get("success"):
            return indices

        db_idx = indices["dbIdx"]
        tbl_idx = indices["tblIdx"]

        # Resolve field indices
        safe_f1 = _escape_modl_string(field1)
        app.Execute(f'globalInt0 = DBFieldGetIndex({db_idx}, {tbl_idx}, "{safe_f1}");')
        fld1_idx = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))
        if fld1_idx < 0:
            return _error(ErrorCode.FIELD_NOT_FOUND, f"Field '{field1}' not found")

        fld2_idx = 0
        fld3_idx = 0
        dir2 = 0
        dir3 = 0

        if field2:
            safe_f2 = _escape_modl_string(field2)
            app.Execute(f'globalInt0 = DBFieldGetIndex({db_idx}, {tbl_idx}, "{safe_f2}");')
            fld2_idx = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))
            if fld2_idx < 0:
                return _error(ErrorCode.FIELD_NOT_FOUND, f"Field '{field2}' not found")
            dir2 = direction2

        if field3:
            safe_f3 = _escape_modl_string(field3)
            app.Execute(f'globalInt0 = DBFieldGetIndex({db_idx}, {tbl_idx}, "{safe_f3}");')
            fld3_idx = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))
            if fld3_idx < 0:
                return _error(ErrorCode.FIELD_NOT_FOUND, f"Field '{field3}' not found")
            dir3 = direction3

        app.Execute(f'DBTableSort({db_idx}, {tbl_idx}, {fld1_idx}, {direction1}, {fld2_idx}, {dir2}, {fld3_idx}, {dir3});')

        return {
            "success": True,
            "databaseName": database_name,
            "tableName": table_name,
            "sortedBy": [
                {"field": field1, "direction": "ascending" if direction1 == 0 else "descending"}
            ] + ([{"field": field2, "direction": "ascending" if dir2 == 0 else "descending"}] if field2 else [])
              + ([{"field": field3, "direction": "ascending" if dir3 == 0 else "descending"}] if field3 else [])
        }
    except Exception as e:
        return _com_error(e, "db_sort")


# ============================================================================
# v1.10.0 — SIMULATION TOOLS (H11-H12)
# ============================================================================


def simulation_step(model_id: Optional[str] = None) -> dict:
    """Single-steps the simulation by one event."""
    try:
        app = get_extendsim_app()

        app.Execute("ExecuteMenuCommand(30003);")

        # Read current state after step
        app.Execute("global0 = currentTime;")
        current_time = parse_float(app.Request("System", "global0+:0:0:0"))

        app.Execute("globalInt0 = GetSimulationPhase();")
        sim_phase = int(app.Request("System", "globalInt0+:0:0:0") or 0)

        return {
            "success": True,
            "currentTime": current_time,
            "simulationPhase": sim_phase,
            "phaseName": SIMULATION_PHASE_NAMES.get(sim_phase, f"unknown({sim_phase})")
        }
    except Exception as e:
        return _com_error(e, "simulation_step")


def simulation_get_state(model_id: Optional[str] = None) -> dict:
    """Reads live simulation system variables."""
    try:
        app = get_extendsim_app()

        app.Execute("global0 = CurrentTime;")
        current_time = parse_float(app.Request("System", "global0+:0:0:0"))

        app.Execute("global0 = CurrentStep;")
        current_step = parse_float(app.Request("System", "global0+:0:0:0"))

        app.Execute("global0 = CurrentSim;")
        current_sim = parse_float(app.Request("System", "global0+:0:0:0"))

        app.Execute("global0 = NumSteps;")
        num_steps = parse_float(app.Request("System", "global0+:0:0:0"))

        app.Execute("global0 = NumSims;")
        num_sims = parse_float(app.Request("System", "global0+:0:0:0"))

        app.Execute("global0 = EndTime;")
        end_time = parse_float(app.Request("System", "global0+:0:0:0"))

        app.Execute("global0 = GetRunParameter(5);")
        random_seed = parse_float(app.Request("System", "global0+:0:0:0"))

        app.Execute("globalInt0 = GetSimulationPhase();")
        sim_phase = int(app.Request("System", "globalInt0+:0:0:0") or 0)

        return {
            "success": True,
            "currentTime": current_time,
            "currentStep": int(current_step),
            "currentSim": int(current_sim),
            "numSteps": int(num_steps),
            "numSims": int(num_sims),
            "endTime": end_time,
            "randomSeed": int(random_seed),
            "simulationPhase": sim_phase,
            "phaseName": SIMULATION_PHASE_NAMES.get(sim_phase, f"unknown({sim_phase})")
        }
    except Exception as e:
        return _com_error(e, "simulation_get_state")


# ============================================================================
# v1.10.0 — GLOBAL ARRAY TOOLS (M1)
# ============================================================================

def ga_list(model_id: Optional[str] = None) -> dict:
    """Lists all global arrays in the model."""
    try:
        app = get_extendsim_app()
        model_check = _validate_model_open(app)
        if not model_check.get("success"):
            return model_check

        try:
            app.Execute("globalInt0 = GALastUsedIndex();")
            last_idx = int(parse_float(app.Request("System", "globalInt0+:0:0:0")) or -1)
        except Exception:
            # GALastUsedIndex fails on models without global arrays (triggers dialog)
            return {
                "success": True,
                "arrays": [],
                "count": 0,
                "warning": "Could not enumerate global arrays (none defined or GA functions unavailable)"
            }

        arrays = []
        for idx in range(last_idx + 1):
            try:
                app.Execute(f'globalStr0 = GAGetName({idx});')
                name = app.Request("System", "globalStr0+:0:0:0")
                if not name or name.startswith("_"):
                    continue  # Skip empty and internal ExtendSim arrays

                app.Execute(f'globalInt0 = GAGetRows({idx});')
                rows = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))
                app.Execute(f'globalInt0 = GAGetCols({idx});')
                cols = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))
                app.Execute(f'globalInt0 = GAGetType({idx});')
                ga_type = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))

                type_name = {1: "real", 2: "integer", 3: "string"}.get(ga_type, f"unknown({ga_type})")

                arrays.append({
                    "index": idx,
                    "name": name,
                    "rows": rows,
                    "cols": cols,
                    "type": type_name
                })
            except Exception:
                continue  # Skip unreadable GAs instead of crashing

        return {"success": True, "arrays": arrays, "count": len(arrays)}
    except Exception as e:
        return _com_error(e, "ga_list")


def ga_create(name: str, ga_type: str = "real", cols: int = 1, rows: int = 0,
              model_id: Optional[str] = None) -> dict:
    """Creates a global array."""
    try:
        app = get_extendsim_app()
        model_check = _validate_model_open(app)
        if not model_check.get("success"):
            return model_check

        type_map = {"real": 1, "integer": 2, "string": 3}
        type_num = type_map.get(ga_type, 1)
        safe_name = _escape_modl_string(name)

        app.Execute(f'globalInt0 = GACreate("{safe_name}", {type_num}, {cols});')
        ga_idx = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))

        if ga_idx < 0:
            return _error(ErrorCode.COMMAND_FAILED, f"Failed to create global array '{name}'")

        if rows > 0:
            app.Execute(f'GAResize("{safe_name}", {rows});')

        return {
            "success": True,
            "name": name,
            "index": ga_idx,
            "type": ga_type,
            "cols": cols,
            "rows": rows
        }
    except Exception as e:
        return _com_error(e, "ga_create")


def ga_read(name: str, row: int = 0, col: int = 0,
            end_row: Optional[int] = None, end_col: Optional[int] = None,
            model_id: Optional[str] = None) -> dict:
    """Reads from a global array. Single cell or range."""
    try:
        app = get_extendsim_app()
        safe_name = _escape_modl_string(name)

        # Get array index and type
        app.Execute(f'globalInt0 = GAGetIndex("{safe_name}");')
        ga_idx = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))
        if ga_idx < 0:
            return _error(ErrorCode.COMMAND_FAILED, f"Global array '{name}' not found")

        app.Execute(f'globalInt0 = GAGetType({ga_idx});')
        ga_type = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))

        # Single cell
        if end_row is None and end_col is None:
            if ga_type == 3:
                app.Execute(f'globalStr0 = GAGetString({ga_idx}, {row}, {col});')
                value = app.Request("System", "globalStr0+:0:0:0")
            elif ga_type == 2:
                app.Execute(f'globalInt0 = GAGetInteger({ga_idx}, {row}, {col});')
                value = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))
            else:
                app.Execute(f'global0 = GAGetReal({ga_idx}, {row}, {col});')
                value = parse_float(app.Request("System", "global0+:0:0:0"))

            return {"success": True, "name": name, "row": row, "col": col, "value": value}

        # Range read
        r_end = end_row if end_row is not None else row
        c_end = end_col if end_col is not None else col
        data = []
        for r in range(row, r_end + 1):
            row_data = []
            for c in range(col, c_end + 1):
                if ga_type == 3:
                    app.Execute(f'globalStr0 = GAGetString({ga_idx}, {r}, {c});')
                    val = app.Request("System", "globalStr0+:0:0:0")
                elif ga_type == 2:
                    app.Execute(f'globalInt0 = GAGetInteger({ga_idx}, {r}, {c});')
                    val = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))
                else:
                    app.Execute(f'global0 = GAGetReal({ga_idx}, {r}, {c});')
                    val = parse_float(app.Request("System", "global0+:0:0:0"))
                row_data.append(val)
            data.append(row_data)

        return {"success": True, "name": name, "fromRow": row, "fromCol": col,
                "toRow": r_end, "toCol": c_end, "data": data}
    except Exception as e:
        return _com_error(e, "ga_read")


def ga_write(name: str, row: int, col: int, value,
             model_id: Optional[str] = None) -> dict:
    """Writes to a global array cell."""
    try:
        app = get_extendsim_app()
        safe_name = _escape_modl_string(name)

        # Get array index and type
        app.Execute(f'globalInt0 = GAGetIndex("{safe_name}");')
        ga_idx = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))
        if ga_idx < 0:
            return _error(ErrorCode.COMMAND_FAILED, f"Global array '{name}' not found")

        app.Execute(f'globalInt0 = GAGetType({ga_idx});')
        ga_type = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))

        if ga_type == 3:
            safe_val = _escape_modl_string(str(value))
            app.Execute(f'GASetString("{safe_val}", {ga_idx}, {row}, {col});')
        elif ga_type == 2:
            app.Execute(f'GASetInteger({int(value)}, {ga_idx}, {row}, {col});')
        else:
            app.Execute(f'GASetReal({float(value)}, {ga_idx}, {row}, {col});')

        return {"success": True, "name": name, "row": row, "col": col, "value": value}
    except Exception as e:
        return _com_error(e, "ga_write")


# ============================================================================
# v1.10.0 — TEXT BLOCK (M2)
# ============================================================================

def text_block_add(text: str, x: int = 100, y: int = 100,
                   neighbor: int = -1, side: int = 2,
                   width: int = 200, model_id: Optional[str] = None) -> dict:
    """Places a text annotation block on the worksheet."""
    try:
        app = get_extendsim_app()
        model_check = _validate_model_open(app)
        if not model_check.get("success"):
            return model_check

        safe_text = _escape_modl_string(text)

        # Collect IDs before
        before_ids = set()
        current_id = -1
        while True:
            app.Execute(f"global0 = objectIDNext({current_id}, 0);")
            next_id = int(parse_float(app.Request("System", "global0+:0:0:0")))
            if next_id == -1:
                break
            before_ids.add(next_id)
            current_id = next_id

        app.Execute(f'PlaceTextBlock("{safe_text}", {x}, {y}, {neighbor}, {side}, {width});')

        # Find new block ID
        new_id = -1
        current_id = -1
        while True:
            app.Execute(f"global0 = objectIDNext({current_id}, 0);")
            next_id = int(parse_float(app.Request("System", "global0+:0:0:0")))
            if next_id == -1:
                break
            if next_id not in before_ids:
                new_id = next_id
            current_id = next_id

        return {
            "success": True,
            "blockId": new_id,
            "text": text,
            "x": x,
            "y": y
        }
    except Exception as e:
        return _com_error(e, "text_block_add")


# ============================================================================
# v1.10.0 — DB RELATIONS (M5)
# ============================================================================

def db_relations_list(database_name: str, model_id: Optional[str] = None) -> dict:
    """Lists all relations in a database."""
    try:
        app = get_extendsim_app()
        safe_db = _escape_modl_string(database_name)
        app.Execute(f'globalInt0 = DBDatabaseGetIndex("{safe_db}");')
        db_idx = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))
        if db_idx < 0:
            return _error(ErrorCode.DATABASE_NOT_FOUND, f"Database '{database_name}' not found")

        app.Execute(f'globalInt0 = DBRelationsGetNum({db_idx});')
        num_rels = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))

        relations = []
        for rel_idx in range(num_rels):
            # DBRelationsGetNames fills globalStr array: child table, child field, parent table, parent field
            app.Execute(f'DBRelationsGetNames({db_idx}, {rel_idx}, globalStr0);')
            # Read the 4 strings back
            child_table = app.Request("System", "globalStr0+:0:0:0")
            app.Execute(f'DBRelationsGetNames({db_idx}, {rel_idx}, globalStr0);')
            # Since we can't easily read an array of strings, we'll use individual calls
            # Try reading relation info through field properties instead
            relations.append({
                "index": rel_idx
            })

        return {
            "success": True,
            "databaseName": database_name,
            "relationCount": num_rels,
            "relations": relations
        }
    except Exception as e:
        return _com_error(e, "db_relations_list")


def db_relation_create(database_name: str, child_table: str, child_field: str,
                       parent_table: str, parent_field: str,
                       model_id: Optional[str] = None) -> dict:
    """Creates a relation between two tables in a database."""
    try:
        app = get_extendsim_app()
        safe_db = _escape_modl_string(database_name)
        safe_ct = _escape_modl_string(child_table)
        safe_cf = _escape_modl_string(child_field)
        safe_pt = _escape_modl_string(parent_table)
        safe_pf = _escape_modl_string(parent_field)

        app.Execute(f'globalInt0 = DBRelationCreate("{safe_db}", "{safe_ct}", "{safe_cf}", "{safe_pt}", "{safe_pf}");')
        result_val = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))

        if result_val < 0:
            return _error(ErrorCode.DB_OPERATION_FAILED,
                          f"Failed to create relation: {child_table}.{child_field} -> {parent_table}.{parent_field}")

        return {
            "success": True,
            "databaseName": database_name,
            "childTable": child_table,
            "childField": child_field,
            "parentTable": parent_table,
            "parentField": parent_field
        }
    except Exception as e:
        return _com_error(e, "db_relation_create")


# ============================================================================
# v1.10.0 — TIME CONVERT (M6)
# ============================================================================

def time_convert(operation: str, value: Optional[float] = None,
                 from_type: Optional[int] = None, to_type: Optional[int] = None,
                 sim_time: Optional[float] = None, time_units: Optional[int] = None,
                 date: Optional[str] = None,
                 model_id: Optional[str] = None) -> dict:
    """Time/date conversion utility.

    Operations:
      - convert_units: ConvertTimeUnits(value, fromType, toType)
      - sim_to_date: EDSimTimeToDate(simTime, timeUnits)
      - date_to_sim: EDDateToSimTime(date, timeUnits)
    """
    try:
        app = get_extendsim_app()

        if operation == "convert_units":
            if value is None or from_type is None or to_type is None:
                return _error(ErrorCode.MISSING_PARAMETER,
                              "convert_units requires value, fromType, and toType")
            app.Execute(f"global0 = ConvertTimeUnits({value}, {from_type}, {to_type});")
            result = parse_float(app.Request("System", "global0+:0:0:0"))
            return {"success": True, "operation": operation, "result": result,
                    "value": value, "fromType": from_type, "toType": to_type}

        elif operation == "sim_to_date":
            if sim_time is None or time_units is None:
                return _error(ErrorCode.MISSING_PARAMETER,
                              "sim_to_date requires simTime and timeUnits")
            app.Execute(f'globalStr0 = EDSimTimeToDate({sim_time}, {time_units});')
            result = app.Request("System", "globalStr0+:0:0:0")
            return {"success": True, "operation": operation, "date": result,
                    "simTime": sim_time, "timeUnits": time_units}

        elif operation == "date_to_sim":
            if date is None or time_units is None:
                return _error(ErrorCode.MISSING_PARAMETER,
                              "date_to_sim requires date and timeUnits")
            safe_date = _escape_modl_string(date)
            app.Execute(f'global0 = EDDateToSimTime("{safe_date}", {time_units});')
            result = parse_float(app.Request("System", "global0+:0:0:0"))
            return {"success": True, "operation": operation, "simTime": result,
                    "date": date, "timeUnits": time_units}

        else:
            return _error(ErrorCode.INVALID_PARAMETER,
                          f"Unknown operation '{operation}'. Use: convert_units, sim_to_date, date_to_sim")
    except Exception as e:
        return _com_error(e, "time_convert")


# ============================================================================
# MODEL EXTRACT — full model snapshot for pattern library
# ============================================================================

# Block-type → list of (friendly_name, dialog_variable_name) for parameter extraction
EXTRACT_PARAMS = {
    "Create": [
        ("arrivalType", "CreateOptions_pop"),
        ("distribution", "Rnd_Distributions_pop"),
        ("arg1", "Rnd_Arg1_prm"),
        ("arg2", "Rnd_Arg2_prm"),
        ("arg3", "Rnd_Arg3_prm"),
        ("maxArrivalsEnabled", "RndI_MaxItems_chk"),
        ("maxArrivals", "RndI_MaxItems_prm"),
    ],
    "Queue": [
        ("rankType", "QueueRank_Pop"),
        ("sortAttribute", "SortAttrib_Pop"),
        ("maxLength", "MaxLength_prm"),
    ],
    "Activity": [
        ("delayType", "66"),  # Delay_Options_pop by dialog ID
        ("distribution", "69"),  # Delay_Distributions_pop by dialog ID
        ("arg1", "74"),  # Delay_Arg1_prm
        ("arg2", "75"),  # Delay_Arg2_prm
        ("arg3", "76"),  # Delay_Arg3_prm
        ("fixedDelay", "WaitDelta_prm"),
    ],
    "Workstation": [
        ("maxServers", "MaxServers_prm"),
        ("maxQueueLength", "MaxQueueLength_prm"),
        ("delayType", "99"),  # Delay_Options_pop by dialog ID
        ("distribution", "Delay_Distributions_pop"),
        ("arg1", "Delay_Arg1_prm"),
        ("arg2", "Delay_Arg2_prm"),
        ("arg3", "Delay_Arg3_prm"),
    ],
    "Gate": [
        ("demandType", "DemandType_Pop"),
        ("initialState", "InitialCondition_Pop"),
    ],
    "Select Item Out": [
        ("mode", "SelectType_Pop"),
    ],
    "Select Item In": [
        ("mode", "SelectType_Pop"),
    ],
    "Batch": [
        ("batchSize", "Quantity_prm"),
    ],
    "Resource Pool": [
        ("poolName", "PoolName_prm"),
        ("initialResources", "NumberOfItems_prm"),
    ],
    "Tank": [
        ("capacity", "MaxLevel_prm"),
        ("initialLevel", "InitialLevel_prm"),
    ],
    "Valve": [
        ("maxRate", "MaxRate_prm"),
    ],
    "Exit": [],  # No configurable parameters to extract
}


def _extract_blocks(app) -> list:
    """Extract all blocks with type, label, library, position."""
    blocks = []
    current_id = -1

    while True:
        app.Execute(f"global0 = objectIDNext({current_id}, 0);")
        bid = int(parse_float(app.Request("System", "global0+:0:0:0")))
        if bid == -1:
            break
        current_id = bid

        app.Execute(f"globalStr0 = BlockName({bid});")
        block_type = app.Request("System", "globalStr0+:0:0:0") or ""
        if not block_type:
            continue

        app.Execute(f'globalStr0 = GetBlockLabel({bid});')
        label = app.Request("System", "globalStr0+:0:0:0") or ""

        app.Execute(f'globalStr0 = GetLibraryPathName({bid}, 2);')
        library = app.Request("System", "globalStr0+:0:0:0") or ""

        blocks.append({
            "id": bid,
            "type": block_type,
            "library": library,
            "label": label,
        })
    return blocks


def _extract_connections(app, blocks: list) -> list:
    """Extract all connections between blocks.

    Uses the same nodeIndex-matching pattern as connection_list():
    1. For each block, get all connectors and their nodeIndex via NodeGetIDIndex
    2. Group connectors by nodeIndex (shared nodeIndex = connected)
    3. Use connector name to determine direction (contains "out" = source)
    """
    connections = []
    block_ids = {b["id"] for b in blocks}

    # Collect all connectors with their nodeIndex
    node_map = {}  # nodeIndex -> [(blockId, connectorIndex, connectorName)]

    for b in blocks:
        bid = b["id"]
        try:
            app.Execute(f"global0 = GetNumCons({bid});")
            num_cons = int(parse_float(app.Request("System", "global0+:0:0:0")))

            for c in range(num_cons):
                app.Execute(f"global0 = NodeGetIDIndex({bid}, {c});")
                node_index = int(parse_float(app.Request("System", "global0+:0:0:0")))
                if node_index == 0:
                    continue  # Unconnected

                app.Execute(f'globalStr0 = GetConName({bid}, {c});')
                con_name = app.Request("System", "globalStr0+:0:0:0") or ""

                if node_index not in node_map:
                    node_map[node_index] = []
                node_map[node_index].append((bid, c, con_name))
        except Exception:
            pass

    # Build connections from node_map (pairs of connectors with same nodeIndex)
    for ni, endpoints in node_map.items():
        if len(endpoints) == 2:
            ep0, ep1 = endpoints[0], endpoints[1]
            # Determine source (out) and target (in)
            if "out" in ep0[2].lower() and "in" in ep1[2].lower():
                src, tgt = ep0, ep1
            elif "in" in ep0[2].lower() and "out" in ep1[2].lower():
                src, tgt = ep1, ep0
            else:
                # Fallback: first endpoint is source
                src, tgt = ep0, ep1

            if src[0] in block_ids and tgt[0] in block_ids:
                connections.append({
                    "sourceBlockId": src[0],
                    "sourceConnector": src[2],
                    "sourceConnectorIndex": src[1],
                    "targetBlockId": tgt[0],
                    "targetConnector": tgt[2],
                    "targetConnectorIndex": tgt[1],
                })

    return connections


def _extract_parameters(app, blocks: list) -> dict:
    """Extract block-type-specific parameters for known block types."""
    parameters = {}
    skipped = []

    for b in blocks:
        bid = b["id"]
        block_type = b["type"]
        param_defs = EXTRACT_PARAMS.get(block_type)

        if param_defs is None:
            skipped.append({"id": bid, "type": block_type})
            continue
        if not param_defs:
            continue  # Known type but no params to read (e.g. Exit)

        params = {"blockType": block_type}
        for friendly_name, dialog_var in param_defs:
            try:
                raw = _get_var(app, bid, dialog_var) or ""
                # Try to parse as number, keep as string if it fails
                try:
                    params[friendly_name] = parse_float(raw)
                except (ValueError, TypeError):
                    params[friendly_name] = raw
            except Exception:
                params[friendly_name] = None

        parameters[str(bid)] = params

    result = {"blocks": parameters}
    if skipped:
        result["skippedBlocks"] = skipped
    return result


def _extract_simulation(app) -> dict:
    """Extract simulation setup parameters."""
    setup = {}
    param_map = [
        ("endTime", 1), ("startTime", 2), ("numberOfRuns", 3),
        ("deltaTime", 4), ("randomSeed", 5), ("seedControl", 6),
        ("numberOfSteps", 7),
    ]
    for name, which in param_map:
        app.Execute(f"global0 = GetRunParameter({which});")
        setup[name] = parse_float(app.Request("System", "global0+:0:0:0"))

    # Time units (GetRunParameter(8) — same as simulation_setup_get)
    app.Execute("global0 = GetRunParameter(8);")
    setup["timeUnits"] = int(parse_float(app.Request("System", "global0+:0:0:0")))

    return setup


def _extract_databases(app) -> dict:
    """Extract database structure (names, tables, fields, record counts).

    Uses numeric indices for all DB functions (same pattern as db_list/db_table_info).
    """
    databases = {}
    app.Execute("globalInt0 = DBDatabasesGetNum();")
    num_dbs = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))

    for d in range(num_dbs):
        app.Execute(f'globalStr0 = DBDatabaseGetName({d});')
        db_name = app.Request("System", "globalStr0+:0:0:0") or ""
        if not db_name:
            continue

        tables = {}
        app.Execute(f'globalInt0 = DBTablesGetNum({d});')
        num_tables = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))

        for t in range(num_tables):
            app.Execute(f'globalStr0 = DBTableGetName({d}, {t});')
            tbl_name = app.Request("System", "globalStr0+:0:0:0") or ""
            if not tbl_name:
                continue

            # Field count and names
            app.Execute(f'globalInt0 = DBFieldsGetNum({d}, {t});')
            num_fields = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))

            fields = []
            for f_idx in range(num_fields):
                app.Execute(f'globalStr0 = DBFieldGetName({d}, {t}, {f_idx});')
                fname = app.Request("System", "globalStr0+:0:0:0") or ""
                app.Execute(f'globalInt0 = DBFieldGetProperties({d}, {t}, {f_idx}, 1);')
                ftype_code = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))
                ftype = DB_FIELD_TYPE_MAP.get(ftype_code, f"unknown({ftype_code})")
                fields.append({"name": fname, "type": ftype, "index": f_idx})

            # Record count
            app.Execute(f'globalInt0 = DBRecordsGetNum({d}, {t});')
            rec_count = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))

            tables[tbl_name] = {"fields": fields, "recordCount": rec_count}

        databases[db_name] = {"tables": tables}

    return databases


def _extract_hierarchies(app) -> list:
    """Extract hierarchy blocks (H-blocks) and their child counts."""
    hierarchies = []
    current_id = -1

    while True:
        app.Execute(f"global0 = objectIDNext({current_id}, 0);")
        bid = int(parse_float(app.Request("System", "global0+:0:0:0")))
        if bid == -1:
            break
        current_id = bid

        app.Execute(f"global0 = GetBlockTypeNumeric({bid});")
        btype = int(parse_float(app.Request("System", "global0+:0:0:0")))
        if btype != 4:
            continue

        app.Execute(f'globalStr0 = GetBlockLabel({bid});')
        label = app.Request("System", "globalStr0+:0:0:0") or ""

        app.Execute(f'global0 = LocalNumBlocks2({bid});')
        child_count = int(parse_float(app.Request("System", "global0+:0:0:0")))

        app.Execute(f'global0 = GetEnclosingHblockNum2({bid});')
        parent = int(parse_float(app.Request("System", "global0+:0:0:0")))

        hierarchies.append({
            "blockId": bid,
            "label": label,
            "parentBlockId": parent if parent >= 0 else None,
            "childBlockCount": child_count
        })
    return hierarchies


def _extract_global_arrays(app) -> list:
    """Extract global array names and sizes.

    Uses same pattern as ga_list(): GALastUsedIndex, GAGetName, GAGetRows, GAGetCols.
    Wrapped in try/except since GA functions may not work on all models.
    """
    try:
        arrays = []
        app.Execute("globalInt0 = GALastUsedIndex();")
        last_idx = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))
        if last_idx < 0:
            return []

        for i in range(last_idx + 1):
            try:
                app.Execute(f'globalStr0 = GAGetName({i});')
                name = app.Request("System", "globalStr0+:0:0:0") or ""
                if not name or name.startswith("_"):
                    continue  # Skip empty and internal ExtendSim arrays

                app.Execute(f'globalInt0 = GAGetRows({i});')
                rows = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))

                app.Execute(f'globalInt0 = GAGetCols({i});')
                cols = int(parse_float(app.Request("System", "globalInt0+:0:0:0")))

                arrays.append({"name": name, "rows": rows, "cols": cols})
            except Exception:
                continue
        return arrays
    except Exception:
        return []


def model_extract(save_path=None, sections=None, model_id=None):
    """Extracts a complete or partial model snapshot for pattern library ingestion."""
    try:
        app = get_extendsim_app()
        model_check = _validate_model_open(app)
        if not model_check.get("success"):
            return model_check

        if sections is None or "all" in sections:
            sections = ["blocks", "connections", "parameters", "simulation",
                        "databases", "hierarchies", "global_arrays"]

        # Get model info
        app.Execute("globalStr0 = GetModelName();")
        model_name = app.Request("System", "globalStr0+:0:0:0") or ""
        app.Execute("globalStr0 = GetModelPathName();")
        model_path = app.Request("System", "globalStr0+:0:0:0") or ""

        result_sections = {}
        blocks = None  # Cache for reuse by connections and parameters

        if "blocks" in sections:
            blocks = _extract_blocks(app)
            result_sections["blocks"] = blocks

        if "connections" in sections:
            if blocks is None:
                blocks = _extract_blocks(app)
            result_sections["connections"] = _extract_connections(app, blocks)

        if "parameters" in sections:
            if blocks is None:
                blocks = _extract_blocks(app)
            result_sections["parameters"] = _extract_parameters(app, blocks)

        if "simulation" in sections:
            result_sections["simulation"] = _extract_simulation(app)

        if "databases" in sections:
            result_sections["databases"] = _extract_databases(app)

        if "hierarchies" in sections:
            result_sections["hierarchies"] = _extract_hierarchies(app)

        if "global_arrays" in sections:
            result_sections["global_arrays"] = _extract_global_arrays(app)

        from datetime import datetime, timezone
        result = {
            "success": True,
            "modelName": model_name,
            "modelPath": model_path,
            "extractedAt": datetime.now(timezone.utc).isoformat(),
            "sections": result_sections
        }

        if save_path:
            import json as json_mod
            with open(save_path, "w", encoding="utf-8") as f:
                json_mod.dump(result, f, indent=2, allow_nan=False)
            return {
                "success": True,
                "savedTo": save_path,
                "modelName": model_name,
                "sectionCount": len(result_sections),
                "sections": list(result_sections.keys())
            }

        return result
    except Exception as e:
        return _com_error(e, "model_extract")


# Dispatch table
COMMANDS = {
    "extendsim_status": lambda p: extendsim_status(),
    "extendsim_start": lambda p: extendsim_start(),
    "detect_license": lambda p: detect_license(p.get("modelId")),
    "model_open": lambda p: model_open(p["filePath"], p.get("readOnly", False)),
    "model_save": lambda p: model_save(p.get("modelId"), p.get("filePath")),
    "model_list": lambda p: model_list(),
    "model_info": lambda p: model_info(p.get("modelId"), p.get("includeStatistics", False)),
    "model_close": lambda p: model_close(p.get("modelId"), p.get("saveFirst", False)),
    "model_new": lambda p: model_new(p.get("savePath")),
    "block_add": lambda p: block_add(
        p["libraryName"], p["blockName"],
        p.get("x", 100), p.get("y", 100),
        p.get("neighbor", -1), p.get("side", 2),
        p.get("label"), p.get("modelId")
    ),
    "block_add_batch": lambda p: block_add_batch(
        p["blocks"], p.get("modelId")
    ),
    "block_connect": lambda p: block_connect(
        p["sourceBlockId"], p["sourceConnector"],
        p["targetBlockId"], p["targetConnector"],
        p.get("modelId")
    ),
    "block_disconnect": lambda p: block_disconnect(
        p["sourceBlockId"], p["sourceConnector"],
        p["targetBlockId"], p["targetConnector"],
        p.get("modelId")
    ),
    "connect_chain": lambda p: connect_chain(
        p["blockIds"],
        p.get("sourceConnector", "ItemOut"),
        p.get("targetConnector", "ItemIn"),
        p.get("modelId")
    ),
    "connect_graph": lambda p: connect_graph(
        p["connections"],
        p.get("modelId")
    ),
    "block_remove": lambda p: block_remove(
        p["blockId"], p.get("allowUndo", False), p.get("modelId")
    ),
    "block_list": lambda p: block_list(p.get("modelId"), p.get("detail", "summary")),
    "connection_list": lambda p: connection_list(p.get("modelId")),
    "instantiate_pattern": lambda p: __import__("instantiate").instantiate_pattern(
        p.get("moleculeId"), p.get("params"), p.get("modelId")),
    "compose_flow": lambda p: __import__("compose").compose_flow(
        p.get("flow"), p.get("modelId")),
    "list_patterns": lambda p: __import__("patterns").list_patterns(p.get("intent")),
    "get_pattern": lambda p: __import__("patterns").get_pattern(p.get("patternId")),
    "table_get": lambda p: __import__("dialog_table").table_get_entry(
        p.get("blockId"), p.get("variableName"), p.get("row", 0), p.get("col", 0)),
    "table_set": lambda p: __import__("dialog_table").table_set_entry(
        p.get("blockId"), p.get("variableName"), p.get("value"), p.get("row", 0), p.get("col", 0)),
    "detect_attributes": lambda p: __import__("attribute_detect").detect_attributes_entry(
        p.get("blockId"), p.get("modelId")),
    "block_info": lambda p: block_info(
        query=p.get("query"), block_id=p.get("blockId"),
        model_id=p.get("modelId")
    ),
    "block_discover": lambda p: block_discover(
        p["libraryName"], p["blockName"], p.get("modelId")
    ),
    "block_discover_variables": lambda p: block_discover_variables(
        p.get("blockId"), p.get("libraryName"), p.get("blockName"),
        p.get("maxDialogId", 200), p.get("modelId")
    ),
    "simulation_run": lambda p: simulation_run(
        p.get("modelId"), p.get("endTime"), p.get("runMode", "normal"),
        p.get("resetFirst", True), p.get("waitForCompletion", True),
        p.get("includeStats", False), p.get("statsBlockIds")
    ),
    "simulation_stop": lambda p: simulation_stop(p.get("modelId")),
    "simulation_pause": lambda p: simulation_pause(p.get("modelId")),
    "simulation_resume": lambda p: simulation_resume(p.get("modelId")),
    "simulation_status": lambda p: simulation_status(p.get("modelId")),
    "block_set_value": lambda p: block_set_value(
        p["blockId"], p["dialogNumber"], p["value"],
        p.get("row", 0), p.get("col", 0), p.get("modelId")
    ),
    "block_get_value": lambda p: block_get_value(
        p["blockId"], p["dialogNumber"],
        p.get("row", 0), p.get("col", 0),
        p.get("asString", False), p.get("modelId")
    ),
    "activity_set_delay": lambda p: activity_set_delay(
        p["blockId"],
        p.get("delayType", "fixed"),
        p.get("value"),
        p.get("distribution"),
        p.get("arg1"),
        p.get("arg2"),
        p.get("arg3"),
        p.get("maxItems"),
        p.get("preemptEnabled"),
        p.get("shutdownEnabled"),
        p.get("costPerTime"), p.get("costPerItem"),
        p.get("costTimeUnit"), p.get("shift"),
        p.get("modelId")
    ),
    "queue_set_priority": lambda p: queue_set_priority(
        p["blockId"],
        p.get("rankType", "fifo"),
        p.get("sortAttribute"),
        p.get("ascending", True),
        p.get("maxLength"),
        p.get("renegeEnabled"),
        p.get("renegeTime"),
        p.get("calcWaitCosts"), p.get("shift"), p.get("calcDelay"),
        p.get("modelId")
    ),
    "template_list": lambda p: template_list(),
    "block_template": lambda p: block_template(
        p["templateName"],
        p.get("startX", 100),
        p.get("startY", 100),
        p.get("spacing", 120),
        p.get("parameters"),
        p.get("modelId")
    ),
    "execute_command": lambda p: execute_command(
        p["command"], p.get("getResult", False), p.get("resultType", "number")
    ),
    "create_set_arrivals": lambda p: create_set_arrivals(
        p["blockId"],
        p.get("arrivalType", "distribution"),
        p.get("distribution", "exponential"),
        p.get("arg1"),
        p.get("arg2"),
        p.get("arg3"),
        p.get("maxArrivals"),
        p.get("costPerTime"), p.get("costPerItem"), p.get("costTimeUnit"),
        p.get("modelId")
    ),
    "gate_set_condition": lambda p: gate_set_condition(
        p["blockId"],
        p.get("demandType", "passing"),
        p.get("initialState", "opened"),
        p.get("openValue", 1),
        p.get("closeValue", 0),
        p.get("modelId")
    ),
    "model_validate": lambda p: model_validate(p.get("modelId")),
    "model_overview": lambda p: model_overview(p.get("modelId")),
    "model_snapshot": lambda p: model_snapshot(p.get("modelId")),
    "simulation_get_results": lambda p: simulation_get_results(p.get("modelId")),
    "attribute_set": lambda p: attribute_set(
        p["blockId"],
        p["attributeName"],
        p.get("valueType", "constant"),
        p.get("value"),
        p.get("distribution"),
        p.get("arg1"),
        p.get("arg2"),
        p.get("arg3"),
        p.get("modelId")
    ),
    "attribute_get": lambda p: attribute_get(
        p["blockId"],
        p["attributeName"],
        p.get("modelId")
    ),
    "select_item_out_set_mode": lambda p: select_item_out_set_mode(
        p["blockId"],
        p.get("mode", "random"),
        p.get("attributeName"),
        p.get("probabilities"),
        p.get("ifBlocked"),
        p.get("predictPath"),
        p.get("modelId")
    ),
    "select_item_in_set_mode": lambda p: select_item_in_set_mode(
        p["blockId"],
        p.get("mode", "first_available"),
        p.get("modelId")
    ),
    # Database operations
    "db_list": lambda p: db_list(p.get("modelId")),
    "db_table_info": lambda p: db_table_info(
        p["databaseName"], p["tableName"], p.get("modelId")
    ),
    "db_get_value": lambda p: db_get_value(
        p["databaseName"], p["tableName"], p["fieldName"],
        p["record"], p.get("asString", False), p.get("modelId")
    ),
    "db_set_value": lambda p: db_set_value(
        p["databaseName"], p["tableName"], p["fieldName"],
        p["record"], p["value"], p.get("modelId")
    ),
    "db_get_records": lambda p: db_get_records(
        p["databaseName"], p["tableName"],
        p.get("startRecord", 0), p.get("endRecord"),
        p.get("fields"), p.get("maxRecords", 1000),
        p.get("modelId")
    ),
    "db_add_records": lambda p: db_add_records(
        p["databaseName"], p["tableName"],
        p.get("count", 1), p.get("position"),
        p.get("modelId")
    ),
    "db_delete_records": lambda p: db_delete_records(
        p["databaseName"], p["tableName"],
        p["startRecord"], p["endRecord"],
        p.get("modelId")
    ),
    # Batch/Unbatch operations
    "batch_set_config": lambda p: batch_set_config(
        p["blockId"],
        p.get("batchType"), p.get("batchSize"),
        p.get("preserveUniqueness"), p.get("matchAttribute"),
        p.get("showDemandConnector"), p.get("demandConnectorValue"),
        p.get("allowZeroBatchSize"), p.get("batchSizeWhen"),
        p.get("modelId")
    ),
    "unbatch_set_config": lambda p: unbatch_set_config(
        p["blockId"],
        p.get("preserveUniqueness"), p.get("quantityPerOutput"),
        p.get("costType"), p.get("usePreservedQuantity"),
        p.get("duplicatePreserved"), p.get("quantityOut"),
        p.get("modelId")
    ),
    # Resource Pool operations
    "resource_pool_set_config": lambda p: resource_pool_set_config(
        p["blockId"],
        p.get("poolName"), p.get("initialResources"),
        p.get("allocationRule"), p.get("modelId")
    ),
    "resource_pool_get_stats": lambda p: resource_pool_get_stats(
        p["blockId"], p.get("modelId")
    ),
    "resource_pool_release_set_config": lambda p: resource_pool_release_set_config(
        p["blockId"],
        pool_name=p.get("poolName"),
        release_quantity=p.get("releaseQuantity"), model_id=p.get("modelId")
    ),
    "queue_set_resource_pool": lambda p: queue_set_resource_pool(
        p["blockId"], p["resourcePoolBlockId"],
        p.get("resourcesNeeded", 1), p.get("modelId")
    ),
    # Simulation setup operations
    "simulation_setup_get": lambda p: simulation_setup_get(p.get("modelId")),
    "simulation_setup_set": lambda p: simulation_setup_set(
        p.get("endTime"), p.get("startTime"), p.get("numberOfRuns"),
        p.get("randomSeed"), p.get("seedControl"),
        p.get("timeUnits"), p.get("deltaTime"), p.get("numSteps"),
        p.get("simulationOrder"), p.get("modelId")
    ),
    # Block statistics operations
    "block_get_stats": lambda p: block_get_stats(
        p["blockId"], p.get("modelId")
    ),
    "simulation_get_block_stats": lambda p: simulation_get_block_stats(
        p["blockIds"], p.get("modelId")
    ),
    # Multi-run and scenario operations
    "simulation_run_multi": lambda p: simulation_run_multi(
        p["numberOfRuns"], p.get("modelId"), p.get("endTime"),
        p.get("randomSeed"), p.get("runMode", "normal"),
        p.get("collectPerRun", True), p.get("blockIds")
    ),
    "simulation_run_scenarios": lambda p: simulation_run_scenarios(
        p["blockId"], p["dialogVariable"], p["values"],
        p.get("modelId"), p.get("endTime"), p.get("runMode", "normal")
    ),
    # v1.3 tools
    "workstation_set_config": lambda p: workstation_set_config(
        p["blockId"],
        p.get("maxServers"), p.get("maxQueueLength"),
        p.get("delayType", "fixed"), p.get("distribution"),
        p.get("arg1"), p.get("arg2"), p.get("arg3"), p.get("value"),
        p.get("costPerTime"), p.get("costPerItem"),
        p.get("modelId")
    ),
    "equation_set_formula": lambda p: equation_set_formula(
        p["blockId"], p.get("equation", ""), p.get("modelId")
    ),
    "equation_i_set_formula": lambda p: equation_i_set_formula(
        p["blockId"], p.get("equation", ""),
        p.get("showInputNames"), p.get("showInputValues"),
        p.get("showOutputNames"), p.get("showOutputValues"),
        p.get("outputInitValue"), p.get("includeEnabled"), p.get("expandRecords"),
        p.get("modelId")
    ),
    "queue_equation_set_config": lambda p: queue_equation_set_config(
        p["blockId"], p.get("equation"), p.get("releaseRule"), p.get("modelId")
    ),
    "shift_set_schedule": lambda p: shift_set_schedule(
        p["blockId"], p.get("schedule"),
        p.get("statusType"), p.get("shiftName"),
        p.get("repeat"), p.get("repeatTime"), p.get("repeatUnit"),
        p.get("timeUnit"), p.get("timeFormat"),
        p.get("modelId")
    ),
    "transport_set_config": lambda p: transport_set_config(
        p["blockId"],
        p.get("defaultDistance"), p.get("defaultSpeed"),
        p.get("modelId")
    ),
    "convey_item_set_config": lambda p: convey_item_set_config(
        p["blockId"],
        p.get("conveyorLength"), p.get("defaultSpeed"),
        p.get("accumulating"), p.get("modelId")
    ),
    "shutdown_set_config": lambda p: shutdown_set_config(
        p["blockId"],
        p.get("tbfDistribution"), p.get("tbfArg1"), p.get("tbfArg2"),
        p.get("ttrDistribution"), p.get("ttrArg1"), p.get("ttrArg2"),
        p.get("modelId")
    ),
    # v1.4 tools - Discrete Rate (Tank, Valve)
    "tank_set_config": lambda p: tank_set_config(
        p["blockId"],
        p.get("capacity"), p.get("initialLevel"),
        p.get("maxInputRate"), p.get("maxOutputRate"),
        p.get("flowControlEnabled"), p.get("flowControlPolicy"), p.get("flowControlValue"),
        p.get("modelId")
    ),
    "valve_set_config": lambda p: valve_set_config(
        p["blockId"],
        p.get("maxRate"), p.get("goal"),
        p.get("goalType"), p.get("goalOffStatus"), p.get("controlType"),
        p.get("startCondition"), p.get("stopCondition"),
        p.get("shutdownCondition"), p.get("pullConstraintDelay"),
        p.get("modelId")
    ),
    "merge_set_config": lambda p: merge_set_config(
        p["blockId"],
        p.get("mode"),
        p.get("initialValueSelected"), p.get("initializeSelected"), p.get("paramFromConnectors"),
        p.get("modelId")
    ),
    "diverge_set_config": lambda p: diverge_set_config(
        p["blockId"],
        p.get("mode"),
        p.get("initialValueSelected"), p.get("initializeSelected"), p.get("paramFromConnectors"),
        p.get("modelId")
    ),
    "interchange_set_config": lambda p: interchange_set_config(
        p["blockId"],
        p.get("capacity"), p.get("initialLevel"),
        p.get("maxInputRate"), p.get("maxOutputRate"),
        p.get("mode"), p.get("releaseCondition"), p.get("releaseTarget"), p.get("releaseInterrupt"),
        p.get("modelId")
    ),
    "convey_flow_set_config": lambda p: convey_flow_set_config(
        p["blockId"],
        p.get("speed"), p.get("length"),
        p.get("capacityMax"), p.get("accumulating"),
        p.get("conveyorType"), p.get("maxDensity"),
        p.get("delay"), p.get("shift"),
        p.get("emptyWhenOffShift"), p.get("attributeTransform"),
        p.get("modelId")
    ),
    "change_units_set_config": lambda p: change_units_set_config(
        p["blockId"],
        p.get("factor"),
        p.get("modelId")
    ),
    "bias_set_config": lambda p: bias_set_config(
        p["blockId"],
        p.get("biasOrder"),
        p.get("modelId")
    ),
    "catch_flow_set_config": lambda p: catch_flow_set_config(
        p["blockId"],
        p.get("position"),
        p.get("modelId")
    ),
    "throw_flow_set_config": lambda p: throw_flow_set_config(
        p["blockId"],
        p.get("position"), p.get("connectorNum"),
        p.get("modelId")
    ),
    "history_r_set_config": lambda p: history_r_set_config(
        p["blockId"],
        p.get("maxRows"), p.get("enableDatabaseLog"),
        p.get("modelId")
    ),
    "throw_item_set_config": lambda p: throw_item_set_config(
        p["blockId"], p.get("catchType"), p.get("catchGroup"),
        p.get("attributeName"), p.get("useBlockNum"), p.get("modelId")
    ),
    "catch_item_set_config": lambda p: catch_item_set_config(
        p["blockId"], p.get("catchGroup"), p.get("countByThrow"), p.get("modelId")
    ),
    "clear_statistics_set_config": lambda p: clear_statistics_set_config(
        p["blockId"], p.get("clearTime"), p.get("timeUnits"),
        p.get("clearActivity"), p.get("clearResource"), p.get("clearQueue"),
        p.get("clearExit"), p.get("clearMeanVariance"), p.get("clearInformation"),
        p.get("clearRate"), p.get("clearMaxMin"), p.get("modelId")
    ),
    "information_set_config": lambda p: information_set_config(
        p["blockId"], p.get("cycleAttribute"), p.get("addCount"),
        p.get("countByOne"), p.get("noReset"), p.get("resetWhen"),
        p.get("detailedStats"), p.get("resetEvery"), p.get("resetEveryInterval"),
        p.get("modelId")
    ),
    "mean_variance_set_config": lambda p: mean_variance_set_config(
        p["blockId"], p.get("multiSim"), p.get("weight"),
        p.get("clearTime"), p.get("confidence"), p.get("initValue"),
        p.get("movingAverage"), p.get("movingAverageInterval"),
        p.get("recordHistory"), p.get("relativeError"), p.get("relativeErrorThreshold"),
        p.get("modelId")
    ),
    "line_chart_set_config": lambda p: line_chart_set_config(
        p["blockId"], p.get("startTime"), p.get("endTime"),
        p.get("disableRecording"), p.get("fixedPoints"), p.get("modelId")
    ),
    "histogram_set_config": lambda p: histogram_set_config(
        p["blockId"], p.get("numBins"), p.get("binSize"),
        p.get("xMin"), p.get("xMax"), p.get("modelId")
    ),
    "resource_item_set_config": lambda p: resource_item_set_config(
        p["blockId"], p.get("initialCount"), p.get("stripAttributes"),
        p.get("itemType"), p.get("costPerTime"), p.get("costPerItem"),
        p.get("costTimeUnit"), p.get("shift"), p.get("costEnabled"),
        p.get("modelId")
    ),
    "get_r_set_config": lambda p: get_r_set_config(
        p["blockId"],
        p.get("locationBlockId"),
        p.get("infoType"), p.get("flowAttribute"),
        p.get("modelId")
    ),
    "set_r_set_config": lambda p: set_r_set_config(
        p["blockId"],
        p.get("modelId")
    ),
    # v1.5 tools - Hierarchies, Optimizer, Scenario Manager, Analysis Manager
    "hierarchy_list": lambda p: hierarchy_list(p.get("modelId")),
    "hierarchy_get_contents": lambda p: hierarchy_get_contents(
        p["blockId"], p.get("modelId")
    ),
    "optimizer_set_config": lambda p: optimizer_set_config(
        p["blockId"],
        p.get("populationSize"), p.get("maxGenerations"),
        p.get("convergencePercent"), p.get("minGenerations"),
        p.get("maxSampleSize"), p.get("truncate"),
        p.get("truncatePercent"), p.get("antithetic"),
        p.get("showPlotter"), p.get("modelId")
    ),
    "optimizer_run": lambda p: optimizer_run(
        p.get("modelId"), p.get("timeout", 600),
        p.get("waitForCompletion", False)
    ),
    "optimizer_get_results": lambda p: optimizer_get_results(
        p["blockId"], p.get("modelId")
    ),
    "scenario_manager_set_config": lambda p: scenario_manager_set_config(
        p["blockId"],
        p.get("runsPerScenario"), p.get("confidenceInterval"),
        p.get("simStart"), p.get("simEnd"),
        p.get("reportDetails"), p.get("saveScenarios"),
        p.get("modelId")
    ),
    "scenario_manager_run": lambda p: scenario_manager_run(
        p.get("modelId"), p.get("timeout", 600),
        p.get("waitForCompletion", False)
    ),
    "scenario_manager_status": lambda p: scenario_manager_status(
        p.get("modelId")
    ),
    "scenario_manager_get_results": lambda p: scenario_manager_get_results(
        p.get("modelId")
    ),
    "analysis_manager_set_config": lambda p: analysis_manager_set_config(
        p["blockId"],
        p.get("enableDbResponses"), p.get("enableBlockResponses"),
        p.get("enableReliabilityResponses"), p.get("enableDbFactors"),
        p.get("enableBlockFactors"), p.get("enableReliabilityFactors"),
        p.get("enableResultsTable"), p.get("autoExport"),
        p.get("modelId")
    ),
    # v1.7 - Universal block configuration
    "block_configure": lambda p: block_configure(p["blockId"], p.get("config"), p.get("modelId")),
    # v1.9.5 - AI Context Persistence
    "context_get": lambda p: context_get(p.get("modelId")),
    "context_set": lambda p: context_set(
        p.get("purpose"), p.get("keyBlocks"), p.get("assumptions"),
        p.get("notes"), p.get("tags"), p.get("custom"),
        p.get("changeEntry"), p.get("modelId")
    ),
    "context_clear": lambda p: context_clear(p.get("confirm", False), p.get("modelId")),
    # v1.10.0 — Block tools
    "block_move": lambda p: block_move(p["blockId"], p["x"], p["y"], p.get("modelId")),
    "block_get_position": lambda p: block_get_position(p["blockId"], p.get("modelId")),
    "block_align": lambda p: block_align(
        p["sourceBlockId"], p["sourceConnector"],
        p["targetBlockId"], p["targetConnector"],
        p.get("vertical", True), p.get("modelId")
    ),
    "block_duplicate": lambda p: block_duplicate(p["blockId"], p.get("label"), p.get("modelId")),
    "block_find": lambda p: block_find(p["searchStr"], p.get("which", 1), p.get("modelId")),
    # v1.10.0 — DB tools
    "db_create": lambda p: db_create(p["databaseName"], p.get("tables"), p.get("modelId")),
    "db_import": lambda p: db_import(
        p["filePath"], p["databaseName"], p["tableName"],
        p.get("delimiter", ","), p.get("hasHeader", True), p.get("modelId")
    ),
    "db_export": lambda p: db_export(
        p["filePath"], p["databaseName"], p["tableName"],
        p.get("delimiter", ","), p.get("includeHeader", True), p.get("modelId")
    ),
    "db_find_record": lambda p: db_find_record(
        p["databaseName"], p["tableName"], p["fieldName"],
        p["findValue"], p.get("exactMatch", True), p.get("startRecord", 0),
        p.get("modelId")
    ),
    "db_sort": lambda p: db_sort(
        p["databaseName"], p["tableName"],
        p["field1"], p.get("direction1", 0),
        p.get("field2"), p.get("direction2", 0),
        p.get("field3"), p.get("direction3", 0),
        p.get("modelId")
    ),
    # v1.10.0 — Simulation tools
    "simulation_step": lambda p: simulation_step(p.get("modelId")),
    "simulation_get_state": lambda p: simulation_get_state(p.get("modelId")),
    # v1.10.0 — Global Array tools
    "ga_list": lambda p: ga_list(p.get("modelId")),
    "ga_create": lambda p: ga_create(
        p["name"], p.get("type", "real"), p.get("cols", 1), p.get("rows", 0),
        p.get("modelId")
    ),
    "ga_read": lambda p: ga_read(
        p["name"], p.get("row", 0), p.get("col", 0),
        p.get("endRow"), p.get("endCol"), p.get("modelId")
    ),
    "ga_write": lambda p: ga_write(
        p["name"], p["row"], p["col"], p["value"], p.get("modelId")
    ),
    # v1.10.0 — Text block
    "text_block_add": lambda p: text_block_add(
        p["text"], p.get("x", 100), p.get("y", 100),
        p.get("neighbor", -1), p.get("side", 2), p.get("width", 200),
        p.get("modelId")
    ),
    # v1.10.0 — DB Relations
    "db_relations_list": lambda p: db_relations_list(p["databaseName"], p.get("modelId")),
    "db_relation_create": lambda p: db_relation_create(
        p["databaseName"], p["childTable"], p["childField"],
        p["parentTable"], p["parentField"], p.get("modelId")
    ),
    # v1.10.0 — Time convert
    "time_convert": lambda p: time_convert(
        p["operation"], p.get("value"), p.get("fromType"), p.get("toType"),
        p.get("simTime"), p.get("timeUnits"), p.get("date"), p.get("modelId")
    ),
    # v1.13.0 — Model extract
    "model_extract": lambda p: model_extract(
        p.get("savePath"), p.get("sections"), p.get("modelId")
    ),
}


def main():
    """Main loop - reads JSON commands from stdin."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            request = json.loads(line)
            command = request.get("command")
            params = request.get("params", {})

            if command in COMMANDS:
                result = COMMANDS[command](params)
            else:
                result = _error(ErrorCode.UNKNOWN_COMMAND,
                               f"Unknown command: {command}")

            print(json.dumps(result, allow_nan=False), flush=True)
        except json.JSONDecodeError as e:
            print(json.dumps(_error(ErrorCode.INVALID_JSON, str(e))), flush=True)
        except ValueError as e:
            # Handle Infinity/NaN values that can't be serialized to JSON
            import math

            def sanitize_for_json(obj):
                if isinstance(obj, float):
                    if math.isinf(obj) or math.isnan(obj):
                        return str(obj)
                    return obj
                if isinstance(obj, dict):
                    return {k: sanitize_for_json(v) for k, v in obj.items()}
                if isinstance(obj, list):
                    return [sanitize_for_json(v) for v in obj]
                return obj

            print(json.dumps(sanitize_for_json(result)), flush=True)
        except Exception as e:
            print(json.dumps(_error(ErrorCode.COM_ERROR, str(e))), flush=True)


if __name__ == "__main__":
    main()
