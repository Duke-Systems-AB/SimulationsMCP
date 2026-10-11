"""Set up Data Import Export blocks for Excel or text import/export (model_files_configure).

Measured live on ExtendSim 2024 and 2026, 2026-10-09/10:
- Write order: ImportExport_pop (1 import, 2 export), ExternalDataType_pop (1 Excel, 4 Text; writing it
  CLEARS Dlg_FileName_txt), DLG_AutoImportExport_chk, Dlg_FileName_txt, XLDBADONewName_txt (sheet), then
  SetDialogVariableNoMsg on XD_DBIndex_prm, XD_tableIndex_prm, XD_TableName_prm together.
- XD_DatabaseName_pop / XD_TableName_pop are NEVER written: that opens a modal "Database Selector" that
  blocks COM. XL_CloseWorksheetAfterUse_chk is never turned on (Excel.Quit at run start hangs on Excel's
  own save prompt when an export workbook is unsaved).
- Popup titles read "" while the dialog is closed, so the VARIABLES are read back, never the labels.
- The block keys on the table INDEX and silently retargets after a table is deleted and recreated, so the
  intended {database, table} is stored in the model's AI context, one key per block
  (dataFileTargets.<block id>, see data_files.stored_targets), for the run guard to compare against. Keys of
  block ids that are no longer Data Import Export blocks in the model are removed when the tool writes.

Everything is validated before anything is written (INVALID_PARAMETER, nothing changed). A value that does
not read back stops the run of writes with MODEL_FILES_CONFIGURE_FAILED and truthful `steps`.

The text delimiter (TextFtpDelimiter_pop) is left as the block has it: its option list has not been
measured, so writing a guessed index could silently pick the wrong delimiter.

Pure: COM is reached only through an injected `ops` object; BackendOps adapts simulation_backend.
"""
import json
import math
import os

import data_files as df
import excel_exports

MAX_ENTRIES = 20
MODL_STRING_MAX = 255
LIBRARY = "Value.lbr"
DIRECTIONS = {"import": 1, "export": 2}
FILE_TYPES = {"excel": 1, "text": 4}
ENTRY_KEYS = {"blockId", "place", "direction", "fileType", "filePath", "sheet", "database", "table", "auto",
              "verifyImport"}
FAIL = "MODEL_FILES_CONFIGURE_FAILED"
INVALID = "INVALID_PARAMETER"

SAVE_NOTE = "the model is not saved - save it with model_save to keep the new setup"
TEXT_NOTE = ("a text export is tab-separated as the block has it; numbers use the decimal separator of the "
             "Windows regional settings")


def _name_problem(what, value):
    if not isinstance(value, str) or not value:
        return f"{what} must be a non-empty name"
    if len(value) > MODL_STRING_MAX:
        return f"{what} is {len(value)} characters; ExtendSim takes at most {MODL_STRING_MAX}"
    if '"' in value:
        return f"{what} contains a double quote, which ExtendSim cannot store"
    return None


def _number(v):
    """A real, finite number - not a bool, not NaN, not +-Infinity (a coordinate of either is meaningless
    and `int(inf)` raises, which would surface as COMMAND_FAILED instead of a clean INVALID_PARAMETER)."""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _entry_plan(n, e, ops, die_blocks, seen):
    """(plan dict, None) or (None, problem) for entry number n (1-based)."""
    if not isinstance(e, dict):
        return None, f"entry {n} is not an object"
    unknown = sorted(set(e) - ENTRY_KEYS)
    if unknown:
        return None, f"entry {n} has unknown field(s): {', '.join(unknown)}"
    has_id, has_place = e.get("blockId") is not None, e.get("place") is not None
    if has_id == has_place:
        return None, f"entry {n} needs exactly one of blockId (an existing block) or place (a new block)"
    plan = {"n": n}
    if has_id:
        bid = e["blockId"]
        if not isinstance(bid, int) or isinstance(bid, bool):
            return None, f"entry {n}: blockId must be an integer"
        if bid not in die_blocks:
            return None, f"entry {n}: block {bid} is not a {df.DIE} block in this model"
        parent = ops.parent(bid)
        if parent is not None and parent != -1:
            return None, (f"entry {n}: block {bid} is inside hierarchical block {parent}; only top-level "
                          f"{df.DIE} blocks can be set up")
        if bid in seen:
            return None, f"entry {n}: block {bid} is given more than once"
        seen.add(bid)
        plan["blockId"], plan["label"] = bid, f"block {bid}"
    else:
        p = e["place"]
        if not isinstance(p, dict) or not _number(p.get("x")) or not _number(p.get("y")):
            return None, f"entry {n}: place must be {{x, y}} with numbers"
        plan["place"], plan["label"] = (int(p["x"]), int(p["y"])), f"the new block of entry {n}"
    direction, ftype = e.get("direction"), e.get("fileType")
    if direction not in DIRECTIONS:
        return None, f"entry {n}: direction must be 'import' or 'export'"
    if ftype not in FILE_TYPES:
        return None, f"entry {n}: fileType must be 'excel' or 'text'"
    path = e.get("filePath")
    problem = df._path_problem({"direction": direction, "blockId": plan["label"]}, path)
    if problem:
        return None, f"entry {n}: {problem}"
    sheet = e.get("sheet")
    if ftype == "excel":
        problem = _name_problem("sheet", sheet)
        if problem:
            return None, f"entry {n}: {problem} (an Excel entry needs the sheet name)"
    elif sheet is not None:
        return None, f"entry {n}: sheet applies to Excel only"
    for what in ("database", "table"):
        problem = _name_problem(what, e.get(what))
        if problem:
            return None, f"entry {n}: {problem}"
    db = ops.db_index(e["database"])
    if not db:
        return None, f"entry {n}: database '{e['database']}' does not exist in this model"
    table = ops.table_index(db, e["table"])
    if not table:
        return None, f"entry {n}: table '{e['table']}' does not exist in database '{e['database']}'"
    auto = e.get("auto", True)
    if not isinstance(auto, bool):
        return None, f"entry {n}: auto must be true or false"
    verify = e.get("verifyImport", False)
    if not isinstance(verify, bool):
        return None, f"entry {n}: verifyImport must be true or false"
    if verify and direction != "import":
        return None, f"entry {n}: verifyImport applies to import entries only"
    plan.update(direction=direction, fileType=ftype, filePath=path, sheet=sheet if ftype == "excel" else None,
                database=e["database"], table=e["table"], dbIndex=int(db), tableIndex=int(table), auto=auto,
                verifyImport=verify)
    return plan, None


def _compact(value):
    return json.dumps(value, separators=(",", ":"))


def _plans(entries, ops):
    """(plans, None, Data Import Export block ids) or (None, problem, ids)."""
    if not isinstance(entries, list) or not entries:
        return None, "blocks must be a non-empty list of entries", set()
    if len(entries) > MAX_ENTRIES:
        return None, f"at most {MAX_ENTRIES} entries per call (got {len(entries)})", set()
    die_blocks = {b["blockId"] for b in ops.blocks() if b["blockName"] == df.DIE}
    seen, plans = set(), []
    for n, e in enumerate(entries, 1):
        plan, problem = _entry_plan(n, e, ops, die_blocks, seen)
        if problem:
            return None, problem, die_blocks
        plans.append(plan)
    for p in plans:
        size = len(_compact(_intent(p)))
        if size > MODL_STRING_MAX:
            return None, (f"entry {p['n']}: the intended database and table take {size} characters in the "
                          f"model's AI context; ExtendSim stores at most {MODL_STRING_MAX} - use shorter "
                          "names"), die_blocks
    return plans, None, die_blocks


def _intent(plan):
    return {"database": plan["database"], "table": plan["table"]}


def validate(entries, ops):
    """None when every entry may be written, else the first problem (nothing is written either way)."""
    return _plans(entries, ops)[1]


def _writes(plan):
    """[(kind, variable, value)] in the measured order; kind: num | text | nomsg."""
    out = [("num", "ImportExport_pop", DIRECTIONS[plan["direction"]]),
           ("num", "ExternalDataType_pop", FILE_TYPES[plan["fileType"]]),
           ("num", "DLG_AutoImportExport_chk", 1 if plan["auto"] else 0),
           ("text", "Dlg_FileName_txt", plan["filePath"])]
    if plan["fileType"] == "excel":
        out.append(("text", "XLDBADONewName_txt", plan["sheet"]))
    out += [("nomsg", "XD_DBIndex_prm", plan["dbIndex"]),
            ("nomsg", "XD_tableIndex_prm", plan["tableIndex"]),
            ("nomsg", "XD_TableName_prm", plan["table"])]
    return out


_NUMERIC_READBACK = {"ImportExport_pop", "ExternalDataType_pop", "DLG_AutoImportExport_chk", "XD_DBIndex_prm",
                     "XD_tableIndex_prm"}


def _read_back(ops, bid, writes):
    mismatch = []
    for _kind, var, expected in writes:
        if var in _NUMERIC_READBACK:
            actual = df._int(ops.get_number(bid, var), None)
            ok = actual == int(expected)
        else:
            actual = ops.get_text(bid, var)
            ok = actual == expected
        if not ok:
            mismatch.append({"variable": var, "expected": expected, "actual": actual})
    return mismatch


def _write_block(ops, plan, step):
    """Write one block and read it back; fills `step`. True when every value read back."""
    if "place" in plan:
        x, y = plan["place"]
        bid = ops.place_block(x, y)
        step["blockId"], step["placed"] = bid, True
        parent = ops.parent(bid)
        if parent is not None and parent != -1:
            step["error"] = (f"the new block {bid} was placed inside hierarchical block {parent}, not at top "
                             "level; it was left unconfigured")
            return False
    bid = step["blockId"]
    writes = _writes(plan)
    for kind, var, value in writes:
        if kind == "num":
            ops.set_number(bid, var, value)
        elif kind == "text":
            ops.set_text(bid, var, value)
        else:
            ops.set_nomsg(bid, var, str(value))
        step["written"].append(var)
    mismatch = _read_back(ops, bid, writes)
    if mismatch:
        step["mismatch"] = mismatch
        return False
    return True


def _store_intent(ops, verified_plans):
    """Upsert one AI context key per verified block (dataFileTargets.<id>) and read them back; the user's
    other context keys, and other blocks' targets, are never touched. (all read back, stored targets)."""
    new = {str(p["blockId"]): _intent(p) for p in verified_plans}
    for bid, intent in new.items():
        ops.write_context_custom(df.intent_key(bid), intent)
    stored = ops.read_targets()
    return all(stored.get(k) == v for k, v in new.items()), stored


def _prune_stale(ops, stored, die_ids):
    """Remove the dataFileTargets.<id> keys whose id is not a Data Import Export block in the model any more
    (a deleted block, or an id reused by another block). ([pruned ids], warning or None); never raises."""
    stale = sorted((k for k in stored if int(k) not in die_ids), key=int)
    if not stale:
        return [], None
    try:
        ops.delete_context_keys([df.intent_key(k) for k in stale])
        left = ops.read_targets()
    except Exception as e:
        return [], f"stored targets of blocks no longer in the model could not be removed: {e}"
    still = [k for k in stale if k in left]
    gone = [int(k) for k in stale if k not in left]
    warning = (f"stored targets of blocks {', '.join(still)} (no longer in the model) could not be removed"
               if still else None)
    return gone, warning


def _same_file(after, expected, ops):
    """Whether the block's file path after an import still names `expected`. 2024 rewrites an absolute path
    into the model folder to a bare name after an import (measured 2026-10-10), so a bare name is resolved
    against the model folder; both sides are compared case- and separator-insensitively, as the Excel worker
    compares paths."""
    if after == expected:
        return True
    if not after or not (df.is_bare_name(after) or os.path.isabs(after)):
        return False
    resolved, _exists = df.resolve(after, ops.model_folder() if df.is_bare_name(after) else None)
    return resolved is not None and excel_exports.norm(resolved) == excel_exports.norm(expected)


def _verify_import(ops, plan, step):
    """Press Import now (dismisser running); True unless the block emptied or changed its file path."""
    bid = plan["blockId"]
    _result, texts = ops.with_dismisser(lambda: ops.press_import(bid))
    if texts:
        step["importDialogs"] = list(texts)
    step["importedRecords"] = int(ops.table_records(plan["dbIndex"], plan["tableIndex"]))
    after = ops.get_text(bid, df.FILE_VAR)
    if not _same_file(after, plan["filePath"], ops):
        step["mismatch"] = [{"variable": df.FILE_VAR, "expected": plan["filePath"], "actual": after}]
        return False
    return True


def _excel_before(ops, plans):
    """Before verifyImport presses Import on an Excel block: (worker, workbooks open now), so the workbooks
    the presses open can be closed afterwards - an import leaves its workbook open and locked
    in Excel otherwise. None when there is no Excel verifyImport, no worker, or Excel could not be asked."""
    worker = getattr(ops, "excel_worker", None)
    if worker is None or not any(p["verifyImport"] and p["fileType"] == "excel" for p in plans):
        return None
    try:
        before, error = excel_exports.open_workbooks(worker)
    except Exception as e:
        before, error = None, str(e)
    return {"worker": worker, "before": before, "error": error}


def _excel_close_opened(excel, plans):
    """{} when nothing was to be closed; else {"closedWorkbooks": [...]} plus "warnings" when something
    could not be closed. Only the verified imports' own workbooks that were not open before are closed."""
    if excel is None:
        return {}
    if excel["before"] is None:
        return {"closedWorkbooks": [], "warnings": [
            f"could not tell which workbooks were open before the import ({excel['error']}), so the workbook "
            "verifyImport read was left open in Excel"]}
    allowed = [p["filePath"] for p in plans if p["verifyImport"] and p["fileType"] == "excel"]
    try:
        done = excel_exports.close_opened(excel["before"], excel["worker"], allowed)
    except Exception as e:
        done = {"closedWorkbooks": [], "warnings": [f"the workbook verifyImport read could not be closed: {e}"]}
    out = {"closedWorkbooks": done["closedWorkbooks"]}
    if done["warnings"]:
        out["warnings"] = done["warnings"]
    return out


def configure(ops, spec):
    entries = spec.get("blocks") if isinstance(spec, dict) else None
    plans, problem, die_ids = _plans(entries, ops)
    if problem:
        return df._err(INVALID, f"nothing was changed: {problem}")

    steps, verified = [], []
    failure = None
    for plan in plans:
        step = {"blockId": plan.get("blockId"), "written": [], "verified": False}
        steps.append(step)
        try:
            ok = _write_block(ops, plan, step)
        except Exception as e:      # COM failed half way: say exactly what was written
            step["error"] = str(e)
            ok = False
        if not ok:
            failure = (f"{plan['label']} did not take every value (see steps); blocks after it were not "
                       "touched")
            break
        step["verified"] = True
        plan["blockId"] = step["blockId"]
        verified.append(plan)

    pruned, prune_warning = [], None
    if verified:
        try:
            stored, targets = _store_intent(ops, verified)
        except Exception as e:
            stored, failure = False, failure or f"the intended tables could not be stored in the model: {e}"
        if stored:
            pruned, prune_warning = _prune_stale(ops, targets, die_ids | {p["blockId"] for p in verified})
        for s in steps[:len(verified)]:
            s["intentStored"] = stored
        if not stored and failure is None:
            failure = ("every block was set up, but the intended tables did not read back from the model's AI "
                       "context, so the run guard cannot detect a silent table change")
    if failure:
        return df._err(FAIL, failure, steps)

    excel = _excel_before(ops, plans)
    for plan, step in zip(plans, steps):
        if plan["verifyImport"]:
            try:
                ok = _verify_import(ops, plan, step)
            except Exception as e:
                step["error"], ok = f"verifyImport failed: {e}", False
            # importVerified is its own flag: whether the import now, pressed to verify, kept the table and
            # file path it was set up with. It never overwrites `verified` (the write/read-back of the
            # block's dialog values, already confirmed above) or `intentStored` (the AI context write) -
            # three different things can each be true or false independently.
            step["importVerified"] = ok
            if not ok:
                failure = (f"block {plan['blockId']} was set up, but verifyImport failed or the import changed the "
                           "block's file path (see steps; any ExtendSim box text is in importDialogs)")
                break
    closed = _excel_close_opened(excel, plans)
    if failure:
        return df._err(FAIL, failure, steps, **closed)

    configured = []
    for plan, step in zip(plans, steps):
        c = {"blockId": plan["blockId"], "direction": plan["direction"], "fileType": plan["fileType"],
             "filePath": plan["filePath"], "sheet": plan["sheet"], "database": plan["database"],
             "table": plan["table"], "auto": plan["auto"], "verified": True}
        if step.get("placed"):
            c["placed"] = True
        if "importedRecords" in step:
            c["importedRecords"] = step["importedRecords"]
        if "importVerified" in step:
            c["importVerified"] = step["importVerified"]
        if step.get("importDialogs"):
            c["importDialogs"] = step["importDialogs"]
        configured.append(c)
    notes = [SAVE_NOTE]
    if any(p["fileType"] == "text" for p in plans):
        notes.append(TEXT_NOTE)
    out = {"success": True, "configured": configured, "note": SAVE_NOTE, "notes": notes, **closed}
    if pruned:
        out["prunedTargets"] = pruned
    if prune_warning:
        out["warnings"] = list(out.get("warnings") or []) + [prune_warning]
    return out


class BackendOps(df.BackendOps):
    """data_files.BackendOps plus the writes, placement, database lookup, AI context and import press."""

    def set_number(self, bid, var, value):
        self._app.Execute(f'SetVariableNumeric({bid}, "{var}", {value}, 0, 0, 1);')

    def set_nomsg(self, bid, var, text):
        self._app.Execute(f'SetDialogVariableNoMsg({bid}, "{var}", "{self._b._escape_modl_string(str(text))}", '
                          '0, 0);')

    def place_block(self, x, y):
        r = self._b.block_add(LIBRARY, df.DIE, x=x, y=y)
        if not r.get("success"):
            raise RuntimeError(r.get("error") or f"could not place a {df.DIE} block")
        return r["blockId"]

    def db_index(self, name):
        n = df._int(self._num(f'DBDatabaseGetIndex("{self._b._escape_modl_string(name)}")'), 0)
        return n if n >= 1 else None

    def table_index(self, db, name):
        n = df._int(self._num(f'DBTableGetIndex({db}, "{self._b._escape_modl_string(name)}")'), 0)
        return n if n >= 1 else None

    def table_records(self, db, table):
        return df._int(self._num(f"DBRecordsGetNum({db}, {table})"), 0)

    def write_context_custom(self, key, value):
        """Upsert ONE key of the AI context; every other key stays as it is."""
        text = _compact(value) if isinstance(value, (dict, list)) else str(value)
        if len(text) > MODL_STRING_MAX:
            raise ValueError(f"context value for '{key}' is {len(text)} characters (max {MODL_STRING_MAX})")
        db, ctx, _hist = self._b._ensure_context_db(self._app)
        self._b._context_upsert(self._app, db, ctx, key, text)

    def delete_context_keys(self, keys):
        """Delete the AI context records whose key is in `keys` (highest record first, so the indices of the
        records still to delete do not move). The number of records deleted."""
        where = self._context_table()
        if where is None:
            return 0
        db, tbl, _key, _val = where
        wanted = set(keys)
        rows = [i for i, key in self._context_keys(where) if key in wanted]
        for i in sorted(rows, reverse=True):
            self._app.Execute(f"DBRecordsDelete({db}, {tbl}, {i}, {i});")
        return len(rows)

    def press_import(self, bid):
        self._app.Execute(f'SetDialogVariable({bid}, "ImportExport_btn", "1", 0, 0);')

    @staticmethod
    def excel_worker(payload):
        """excel_save_worker.py as a subprocess with its deadline - the only way this module reaches Excel."""
        return excel_exports.run_worker(payload)

    def with_dismisser(self, fn):
        watcher = self._b._start_dialog_auto_dismisser(duration_sec=self._b._DISMISSER_SAFETY_CAP_SEC)
        try:
            result = fn()
        finally:
            texts = self._b._dismisser_texts(watcher)
        return result, texts


def model_files_configure_command(params, ops=None):
    return df._with_ops(configure, params, ops, FAIL, ops_class=BackendOps)
