"""Data files of a model: the files its Data Import Export blocks read and write.

Lists every Data Import Export block (also inside hierarchical blocks) with its file and whether the file
exists, repoints blocks to other files (read back - fail closed), and tells the run tools which files an
auto-import would miss, so a run never starts into ExtendSim's "does not exist or is not in the current
folder" box. Measured live on ExtendSim 2026, 2026-10-09: Dlg_FileName_txt is written and read back
exactly and writing it triggers nothing; after a missing-file error the block empties it; a block inside
a hierarchical block with a relative missing path crashed ExtendSim when that box was answered. So file
existence is checked here, in Python - ExtendSim is never asked to open a file.

Paths, measured live on ExtendSim 2024 and 2026, 2026-10-10: a BARE file name (no folder separator) is
found next to the model on both versions, and 2024 even rewrites an absolute path into the model folder to a
bare name after an import/export - so a bare name resolves against the model folder and counts when the file
is there. A relative path WITH a folder (sub/x.xlsx) is not found by ExtendSim and stays refused. A model
that was never saved has no folder (GetModelPath is ""), so a bare name cannot be resolved there.

Pure: COM is reached only through an injected `ops` object; BackendOps adapts simulation_backend.
"""
import json
import os
import re

DIE = "Data Import Export"
FILE_VAR = "Dlg_FileName_txt"
FILE_TYPES = {1: "Excel spreadsheet", 2: "ODBC database", 3: "FTP file", 4: "Text file",
              5: "ADO Database", 6: "XML file"}
MAX_PATH = 255      # ModL string limit


def _err(code, message, steps=None, **extra):
    out = {"success": False, "errorCode": code, "error": message}
    if steps is not None:
        out["steps"] = steps
    out.update(extra)
    return out


MODEL_FOLDER = "model folder"


def is_bare_name(stored):
    """A file name with no folder in it ("in.xlsx"): no separator, no drive, not absolute, not empty."""
    return (isinstance(stored, str) and bool(stored) and not os.path.isabs(stored)
            and not any(c in stored for c in "/\\:"))


def resolve(stored, model_folder):
    """(resolved path, exists). Absolute stays as is; relative joins the model's folder; empty -> (None, False).
    Without a model folder (never-saved model) a relative path resolves to nothing - never Python's cwd."""
    if not stored:
        return None, False
    if os.path.isabs(stored):
        path = stored
    elif model_folder:
        path = os.path.join(model_folder, stored)
    else:
        return None, False
    return path, os.path.isfile(path)


def _int(n, default):
    """int(n), or default for None / NaN / infinity - NaN is truthy, so `int(nan or 1)` would raise."""
    try:
        return int(n) if n is not None and n == n else default
    except (OverflowError, ValueError, TypeError):
        return default


def _index(ops, bid, var):
    n = _int(ops.get_number(bid, var), 0)
    return n if n >= 1 else None


TARGETS_UNREADABLE = ("the tables stored for the model's Data Import Export blocks could not be read ({}), so a "
                      "block whose table changed silently is not detected")


def _read_intents(ops, warnings):
    """The stored targets (ops.read_targets); on failure {} plus a warning, so the other checks still run.
    Without a `warnings` list the failure is raised instead."""
    try:
        return ops.read_targets() or {}
    except Exception as e:
        if warnings is None:
            raise
        warnings.append(TARGETS_UNREADABLE.format(e))
        return {}


def scan(ops, warnings=None):
    """Every Data Import Export block of the model with its file settings, in block order.

    A block with intent stored by model_files_configure (see stored_targets) also gets intendedDatabase,
    intendedTable and targetChanged: true when the table its stored index resolves to now differs from what
    was intended - the silent retarget after a table is deleted and recreated. A block with no stored intent
    gets none of the three keys; a stale intent key of a deleted block is never looked at, since only blocks
    ops.blocks() still returns are considered.

    The stored targets are read lazily - only once the first Data Import Export block is found, so a model
    without one never touches its AI context - and a failing read only drops the target check (a warning in
    `warnings`), never the rest of the scan."""
    folder = ops.model_folder()
    intents = None
    out = []
    for b in ops.blocks():
        if b["blockName"] != DIE:
            continue
        if intents is None:
            intents = _read_intents(ops, warnings)
        bid = b["blockId"]
        stored = ops.get_text(bid, FILE_VAR)
        resolved, exists = resolve(stored, folder)
        parent = ops.parent(bid)
        db, table = _index(ops, bid, "XD_DBIndex_prm"), _index(ops, bid, "XD_tableIndex_prm")
        db_name = ops.db_name(db) if db else None
        table_name = ops.table_name(db, table) if db_name and table else None
        entry = {
            "blockId": bid,
            "label": b.get("label", ""),
            "parentHblock": parent if parent is not None and parent >= 0 else None,
            "direction": "export" if _int(ops.get_number(bid, "ImportExport_pop"), 1) == 2 else "import",
            "fileType": FILE_TYPES.get(_int(ops.get_number(bid, "ExternalDataType_pop"), 0), "unknown"),
            "storedPath": stored,
            "resolvedPath": resolved,
            "exists": exists,
            "sheet": ops.get_text(bid, "XLDBADONewName_txt"),
            "database": db_name,
            "table": table_name,
            "autoImportExport": _int(ops.get_number(bid, "DLG_AutoImportExport_chk"), 0) != 0,
        }
        if is_bare_name(stored):
            entry["location"] = MODEL_FOLDER      # ExtendSim looks for a bare name next to the model
        intent = intents.get(str(bid))
        if intent:
            entry["intendedDatabase"] = intent.get("database")
            entry["intendedTable"] = intent.get("table")
            entry["targetChanged"] = (intent.get("database") != db_name or intent.get("table") != table_name)
        out.append(entry)
    return out


INTENT_PREFIX = "dataFileTargets."     # AI context key per block: dataFileTargets.<block id>
CONTEXT_DB, CONTEXT_TABLE = "AI_Context", "context"     # as simulation_backend.AI_CONTEXT_DB_NAME / _TABLE


def intent_key(bid):
    return f"{INTENT_PREFIX}{bid}"


def stored_targets(context):
    """{block id string: {"database", "table"}} stored by model_files_configure in the model's AI context.

    One context key per block (dataFileTargets.<id>), not one JSON object for all of them: a context value
    is a ModL string of at most 255 characters, which a single object outgrows at about six blocks."""
    out = {}
    for key, value in (context or {}).items():
        if isinstance(key, str) and key.startswith(INTENT_PREFIX) and isinstance(value, dict):
            bid = key[len(INTENT_PREFIX):]
            if bid.isdigit():
                out[bid] = value
    return out


def _drive_relative(stored):
    """'C:in.xlsx' - a drive letter without a separator after it: relative to that drive's current folder."""
    return bool(stored) and len(stored) >= 2 and stored[1] == ":" and not os.path.isabs(stored)


def _relative_with_folder(stored):
    return (bool(stored) and not os.path.isabs(stored) and not is_bare_name(stored)
            and not _drive_relative(stored))


def list_files(ops, spec):
    warnings = []
    entries = scan(ops, warnings)
    out = {"success": True, "modelFolder": ops.model_folder(), "count": len(entries),
           "missing": sum(1 for e in entries if not e["exists"]), "files": entries, "warnings": warnings}
    for e in entries:
        if _relative_with_folder(e["storedPath"]):
            out["warnings"].append(f"block {e['blockId']} uses a relative path with a folder; ExtendSim does "
                                   "not find it - repoint it to an absolute path with model_files_repoint")
        elif _drive_relative(e["storedPath"]):
            out["warnings"].append(f"block {e['blockId']} uses a drive-relative path ('{e['storedPath']}'); "
                                   "repoint it to an absolute path with model_files_repoint")
        elif is_bare_name(e["storedPath"]) and not out["modelFolder"]:
            out["warnings"].append(f"block {e['blockId']} uses a bare file name, but the model has never been "
                                   "saved, so it has no folder to find it in - save the model next to the "
                                   "file, or repoint the block to an absolute path")
    if not entries:
        out["message"] = "the model has no Data Import Export blocks, so it reads and writes no data files"
    return out


def _norm_folder(path):
    """Lower-case, forward slashes, exactly one trailing slash - for prefix comparison only."""
    return path.replace("\\", "/").rstrip("/").lower() + "/"


def _path_problem(entry, path):
    if not isinstance(path, str) or not path:
        return "the path is empty"
    if not os.path.isabs(path):
        return f"'{path}' is not an absolute path"
    if len(path) > MAX_PATH:
        return f"the path is {len(path)} characters; ExtendSim takes at most {MAX_PATH}"
    if '"' in path:
        return "the path contains a double quote, which ExtendSim cannot store"
    if entry["direction"] == "import" and not os.path.isfile(path):
        return f"'{path}' does not exist (block {entry['blockId']} imports from it)"
    if entry["direction"] == "export" and not os.path.isdir(os.path.dirname(path)):
        return f"the folder of '{path}' does not exist (block {entry['blockId']} exports to it)"
    return None


def _planned_changes(entries, spec):
    """[(entry, new path)] or an error message. Exactly one of changes / fromFolder+toFolder."""
    by_id = {e["blockId"]: e for e in entries}
    has_changes = "changes" in spec
    has_folders = "fromFolder" in spec or "toFolder" in spec
    if has_changes == has_folders:
        return None, "give either changes or fromFolder and toFolder"
    if has_changes:
        changes = spec.get("changes") or []
        if not changes:
            return None, "changes is empty"
        planned = []
        for c in changes:
            entry = by_id.get(c.get("blockId"))
            if entry is None:
                return None, f"block {c.get('blockId')} is not a {DIE} block in this model"
            planned.append((entry, c.get("path")))
        return planned, None
    src, dst = spec.get("fromFolder"), spec.get("toFolder")
    if not src or not dst:
        return None, "fromFolder and toFolder must both be given"
    prefix = _norm_folder(src)
    planned = []
    for e in entries:
        stored = e["storedPath"] or ""
        if _norm_folder(stored).startswith(prefix) and stored.replace("\\", "/").lower() != prefix.rstrip("/"):
            rest = stored.replace("\\", "/")[len(prefix):]
            planned.append((e, os.path.join(dst, *[p for p in rest.split("/") if p])))
    if not planned:
        return None, f"no {DIE} block's file is under '{src}'"
    return planned, None


def repoint(ops, spec):
    entries = scan(ops, [])      # repointing needs no stored targets: never fail over them
    planned, problem = _planned_changes(entries, spec)
    if problem:
        return _err("INVALID_PARAMETER", problem)
    for entry, path in planned:
        problem = _path_problem(entry, path)
        if problem:
            return _err("INVALID_PARAMETER", f"nothing was changed: {problem}")
    steps = []
    for entry, path in planned:
        bid = entry["blockId"]
        try:
            ops.set_text(bid, FILE_VAR, path)
            after = ops.get_text(bid, FILE_VAR)
        except Exception as e:      # COM failed half way: earlier blocks are already rewritten - say which
            steps.append({"blockId": bid, "before": entry["storedPath"], "after": None, "verified": False,
                          "error": str(e)})
            return _err("MODEL_FILES_REPOINT_FAILED",
                        f"stopped at block {bid}: {e}; steps shows which blocks were changed before it "
                        "(its own path is unknown - check it with model_files_list)", steps)
        steps.append({"blockId": bid, "before": entry["storedPath"], "after": after, "verified": after == path})
    if not all(s["verified"] for s in steps):
        return _err("MODEL_FILES_REPOINT_FAILED",
                    "ExtendSim did not keep every new path; steps shows what each block holds now", steps)
    return {"success": True, "changed": steps,
            "note": "the model is not saved - save it with model_save to keep the new paths"}


def model_files_repoint_command(params, ops=None):
    return _with_ops(repoint, params, ops, "MODEL_FILES_REPOINT_FAILED")


MISSING_BOX_RE = re.compile(r"DLG_Filename_txt\s+(.*?)\s+used in Data Import Export block number\s+(\d+)\s+"
                            r"does not exist or is not in the current folder", re.IGNORECASE | re.DOTALL)


RELATIVE_REASON = ("relative path with a folder - ExtendSim does not resolve it; repoint it to an absolute "
                   "path (a bare file name next to the model is fine)")
DRIVE_RELATIVE_REASON = "drive-relative path (drive letter without a folder) - give an absolute path"
NEVER_SAVED_REASON = ("bare file name, but the model has never been saved, so it has no folder to find it "
                      "in - save the model next to the file, or repoint it to an absolute path")


def _missing_reason(entry):
    """Why an auto-import block would not find its file at run start, or None when it will.

    A bare file name resolves against the model folder - ExtendSim finds it there (measured 2026-10-10 on
    2024 and 2026). A relative path with a folder counts as missing even when the file sits under the
    model's folder: ExtendSim does not find it, and a relative missing path in a block inside a
    hierarchical block is the measured crash path. A never-saved model has no folder at all."""
    stored = entry["storedPath"]
    if not stored:
        return "no file set"
    if is_bare_name(stored):
        if entry["resolvedPath"] is None:
            return NEVER_SAVED_REASON
        return None if entry["exists"] else "file not found in the model folder"
    if _drive_relative(stored):
        return DRIVE_RELATIVE_REASON
    if not os.path.isabs(stored):
        return RELATIVE_REASON
    if not entry["exists"]:
        return "file not found"
    return None


def _missing_from(entries):
    """The already-scanned entries that would not find their file when a run starts (import, auto on),
    each with a reason."""
    out = []
    for e in entries:
        if e["direction"] == "import" and e["autoImportExport"]:
            reason = _missing_reason(e)
            if reason:
                out.append({**e, "reason": reason})
    return out


def missing_inputs(ops):
    """The blocks that would not find their file when a run starts (import, auto on), each with a reason."""
    return _missing_from(scan(ops))


def _target_problems_from(entries):
    """The already-scanned auto import/export entries whose current table no longer matches the intent
    model_files_configure stored for them. A block with no stored intent -
    a model configured by hand - is never flagged here."""
    return [e for e in entries if e["autoImportExport"] and e.get("targetChanged")]


def target_problems(ops):
    """Auto import/export blocks whose current table (resolved from the stored index) no longer matches the
    intent model_files_configure stored for them. A block with no stored intent is never checked here."""
    return _target_problems_from(scan(ops))


EXCEL_TYPE = "Excel spreadsheet"
CLOSE_AFTER_USE_VAR = "XL_CloseWorksheetAfterUse_chk"


def _close_conflicts_from(entries, ops):
    """The already-scanned auto Excel import entries with "close worksheet after use" on while the model
    also has an Excel export entry - the measured hang: the import's
    Excel.Quit at run start pops Excel's own "Save your changes..." prompt when an unsaved export workbook
    is open, and ExtendSim blocks on it. Empty when `entries` has no Excel export at all - the only case
    that still needs an ops call (the close flag is not part of a scanned entry)."""
    if not any(e["direction"] == "export" and e["fileType"] == EXCEL_TYPE for e in entries):
        return []
    out = []
    for e in entries:
        if e["direction"] == "import" and e["fileType"] == EXCEL_TYPE and e["autoImportExport"]:
            if _int(ops.get_number(e["blockId"], CLOSE_AFTER_USE_VAR), 0) == 1:
                out.append(e)
    return out


def close_conflicts(ops):
    """Auto Excel import blocks with "close worksheet after use" on while the model also has an Excel export
    block. Empty when the model has no Excel export block at all."""
    return _close_conflicts_from(scan(ops), ops)


def missing_file_notes(texts):
    """A readable note for every ExtendSim missing-file box among the dialog texts of a run."""
    notes = []
    for t in texts or []:
        m = MISSING_BOX_RE.search(t or "")
        if m:
            notes.append(f"the file {m.group(1)} used by Data Import Export block {m.group(2)} was not found; "
                         "the block has emptied its file path, so do not save the model before repointing "
                         "it with model_files_repoint")
    return notes


def guarded(run, make_ops, save_exports=True, worker=None, still_running=None):
    """Wrap a run command: refuse when, in this order -
    1. an auto-import file is missing (nothing starts, so ExtendSim shows no box);
    2. an auto import/export block's stored table intent no longer matches what its index resolves to now
       (DATA_FILE_TARGET_CHANGED);
    3. an auto Excel import would close Excel while the model has an Excel export (DATA_FILE_EXCEL_CLOSE_CONFLICT,
       the measured hang) -
    and add readable notes for missing-file boxes seen during the run. A guard that cannot check never blocks
    the run - the response then carries a warning. A failing read of the stored targets only drops check 2
    (warning); the missing-file check still runs.

    The model is scanned exactly once per call (one `scan(ops)`, feeding all three checks and the Excel export
    targets) - not once per check - to keep the pre-run COM walk lean.

    Excel exports (excel_exports.py): with a `worker` and `saveExports` (the call's parameter, else
    `save_exports`) on, a model with any Excel block has its open workbooks listed before the run, and after a
    finished run its auto Excel exports are saved and verified on disk and the workbooks the run opened are
    closed (`savedExports`, `closedWorkbooks`) - two worker calls per run (list before; save_close after, which
    reads the ROT itself). `still_running(params)` says, from the parameters and before the run, that the run
    will still be going when the command returns (simulation_run with waitForCompletion=false) - then Excel is
    not asked at all, not even for the pre-run list. Without a worker (tests,
    or a caller that wants none) Excel is never asked. Nothing here changes the run's own outcome."""
    import excel_exports

    def command(params):
        warnings = []
        entries = []
        try:
            ops = make_ops()
            entries = scan(ops, warnings) if ops is not None else []
            missing = _missing_from(entries)
            changed = _target_problems_from(entries)
            conflicts = _close_conflicts_from(entries, ops) if ops is not None else []
        except Exception as e:
            entries, missing, changed, conflicts = [], [], [], []
            warnings.append(f"the model's data files could not be checked before the run: {e}")
        if missing:
            return _err("DATA_FILE_MISSING",
                        f"{len(missing)} file(s) the model imports at run start are missing, so the run was "
                        "not started",
                        missingFiles=[{"blockId": e["blockId"], "label": e["label"],
                                       "path": e["storedPath"], "reason": e["reason"]} for e in missing],
                        suggestion="point the block at the right file with model_files_repoint, or restore "
                                   "the file")
        if changed:
            return _err("DATA_FILE_TARGET_CHANGED",
                        f"{len(changed)} block(s) are set to use a table other than the one their stored "
                        "index now resolves to, so the run was not started",
                        targetChanged=[{"blockId": e["blockId"], "label": e["label"],
                                        "intendedDatabase": e["intendedDatabase"],
                                        "intendedTable": e["intendedTable"], "currentDatabase": e["database"],
                                        "currentTable": e["table"]} for e in changed],
                        suggestion="point the block(s) at the right table again with model_files_configure")
        if conflicts:
            return _err("DATA_FILE_EXCEL_CLOSE_CONFLICT",
                        f"{len(conflicts)} auto Excel import block(s) would close Excel while the model also "
                        "has an Excel export; that can hang on Excel's own save prompt, so the run was not "
                        "started",
                        conflicts=[{"blockId": e["blockId"], "label": e["label"]} for e in conflicts],
                        suggestion="turn off \"close worksheet after use\" on the import block in ExtendSim, "
                                   "or remove the Excel export")
        excel = None
        final = not (still_running and still_running(params))
        if worker is not None and final and params.get("saveExports", save_exports):
            try:
                excel = excel_exports.snapshot(entries, worker)
            except Exception as e:      # never block a run over Excel
                warnings.append(f"Excel exports will not be saved: {e}")
        result = run(params)
        if isinstance(result, dict):
            notes = missing_file_notes(result.get("dialogs") or [])
            if notes:
                result["dataFileNotes"] = notes
            if warnings:
                result.setdefault("warnings", []).extend(warnings)
            if excel is not None:
                try:
                    done = excel_exports.finish(excel, worker)
                except Exception as e:
                    done = {"savedExports": [], "closedWorkbooks": [],
                            "warnings": [f"Excel exports could not be saved: {e}"]}
                result["savedExports"] = done["savedExports"]
                result["closedWorkbooks"] = done["closedWorkbooks"]
                if done["warnings"]:
                    result.setdefault("warnings", []).extend(done["warnings"])
        return result
    return command


class BackendOps:
    """The ops contract over simulation_backend (passed in, never imported at module level)."""

    def __init__(self, backend, app):
        self._b, self._app = backend, app

    def blocks(self):
        """Every block, lean: objectIDNext(.., 0) as block_list walks it, plus BlockName. The label is read
        only for Data Import Export blocks - this runs before every guarded run, so it must not cost
        block_list's ~10 COM calls per block of the whole model."""
        out, current = [], -1
        while True:
            self._app.Execute(f"global0 = objectIDNext({current}, 0);")
            bid = int(self._b.parse_float(self._app.Request("System", "global0+:0:0:0")))
            if bid == -1:
                return out
            self._app.Execute(f"globalStr0 = BlockName({bid});")
            name = self._b._read_str0(self._app) or ""
            label = ""
            if name == DIE:
                self._app.Execute(f"globalStr0 = GetBlockLabel({bid});")
                label = self._b._read_str0(self._app) or ""
            out.append({"blockId": bid, "blockName": name, "label": label})
            current = bid

    def _num(self, code):
        self._app.Execute("global0 = -1;")
        self._app.Execute(f"global0 = {code};")
        return self._b.parse_float(self._app.Request("System", "global0+:0:0:0"))

    def _str(self, code):
        self._app.Execute('globalStr0 = "";')
        self._app.Execute(f"globalStr0 = {code};")
        return self._b._read_str0(self._app)

    def parent(self, bid):
        return int(self._num(f"GetEnclosingHblockNum2({bid})"))

    def get_text(self, bid, var):
        return self._str(f'GetDialogVariable({bid}, "{var}", 0, 0)')

    def get_number(self, bid, var):
        return self._num(f'GetVariableNumeric({bid}, "{var}", 0, 0)')

    def set_text(self, bid, var, text):
        self._app.Execute(f'SetDialogVariable({bid}, "{var}", "{self._b._escape_modl_string(str(text))}", 0, 0);')

    def _context_table(self):
        """(db, table, key field, value field) of the model's AI context table, or None when the model has
        none. Same database/table/field names as simulation_backend's AI context."""
        db = int(self._num(f'DBDatabaseGetIndex("{CONTEXT_DB}")'))
        if db <= 0:
            return None
        tbl = int(self._num(f'DBTableGetIndex({db}, "{CONTEXT_TABLE}")'))
        if tbl <= 0:
            return None
        key_fld = int(self._num(f'DBFieldGetIndex({db}, {tbl}, "key")'))
        val_fld = int(self._num(f'DBFieldGetIndex({db}, {tbl}, "value")'))
        if key_fld <= 0 or val_fld <= 0:
            raise RuntimeError("the AI context table has no key/value fields")
        return db, tbl, key_fld, val_fld

    def _context_keys(self, where):
        """[(record index, 1-based, key)] of the AI context table at `where` (see _context_table)."""
        db, tbl, key_fld, _val = where
        n = int(self._num(f"DBRecordsGetNum({db}, {tbl})"))
        return [(i, self._str(f"DBDataGetAsString({db}, {tbl}, {key_fld}, {i})") or "") for i in range(1, n + 1)]

    def read_targets(self):
        """{block id string: {"database", "table"}} - only the dataFileTargets.<id> keys of the AI context
        are read (one string read per context record, plus one per target), never the rest of the context
        or its change history. {} when the model has no AI context."""
        where = self._context_table()
        if where is None:
            return {}
        db, tbl, _key, val_fld = where
        found = {}
        for i, key in self._context_keys(where):
            if key.startswith(INTENT_PREFIX):
                raw = self._str(f"DBDataGetAsString({db}, {tbl}, {val_fld}, {i})")
                try:
                    found[key] = json.loads(raw)
                except (ValueError, TypeError):
                    continue
        return stored_targets(found)

    def model_folder(self):
        return self._str("GetModelPath(GetModelName())")

    def db_name(self, idx):
        if not 1 <= idx <= int(self._num("DBDatabasesGetNum()")):
            return None
        return self._str(f"DBDatabaseGetName({idx})") or None

    def table_name(self, db, t):
        if not 1 <= t <= int(self._num(f"DBTablesGetNum({db})")):
            return None
        return self._str(f"DBTableGetName({db}, {t})") or None


def _with_ops(fn, params, ops, fail_code, ops_class=None):
    if ops is None:
        import simulation_backend as sb
        app = sb.get_extendsim_app()
        check = sb._validate_model_open(app)
        if not check.get("success"):
            return check
        ops = (ops_class or BackendOps)(sb, app)
    try:
        return fn(ops, {k: v for k, v in params.items() if k != "modelId"})
    except Exception as e:      # a COM failure mid-way: report, never a success
        return _err(fail_code, f"stopped: {e}")


def model_files_list_command(params, ops=None):
    return _with_ops(list_files, params, ops, "COMMAND_FAILED")
