"""The only code that talks to Excel: saves and closes workbooks that are already open, via the ROT.

Run as a separate process by excel_exports.run_worker, with a deadline, so an Excel-side box can never hang
the server:

    python excel_save_worker.py '{"action": "list"}'
        -> prints a JSON list of the full paths of the workbooks open in Excel ([] when Excel is not running)
    python excel_save_worker.py '{"action": "save_close", "before": [paths] | null, "targets": [paths],
                                  "allowedClose": [paths]}'
        -> reads the ROT once, plans with excel_exports.plan (save = targets open now; close = open now, not
           open before, and in allowedClose; nothing is closed when before is null), then saves and closes;
           prints {"open": [paths], "saved": [{path, saved, changed?, error?}],
                   "closed": [{path, closed, reason?}]}
    python excel_save_worker.py '{"action": "save_close", "save": [paths], "close": [paths]}'
        -> the same with explicit lists (no planning)

Measured live on ExtendSim 2024 and 2026, 2026-10-10: GetObject(full path) of a workbook that is in
the Running Object Table binds the open workbook in 0.03-0.05 s and never starts Excel; Save() writes it to
disk; Close(False) releases the file lock.

Rules:
- Never dispatches Excel.Application, never GetObject of a path that is not in the ROT (that WOULD start Excel
  and open the file). Every path - to save or to close - is looked up in the ROT again right before it is
  bound, so a workbook closed in Excel since the first read is never reopened.
- The workbook bound for the save is reused for the close (one bind per path).
- Close(False) never discards changes: Saved is re-read immediately before it, and a workbook that still has
  unsaved changes - a close-only import, a non-auto export, or a target whose save failed - is left open.
"""
import json
import os
import sys

EXTENSIONS = (".xlsx", ".xlsm", ".xls")
UNSAVED = "left open: it has unsaved changes"
SAVE_FAILED = "left open: saving it failed, so closing would lose the export"
NOT_OPEN = "not open in Excel"


def _key(path):
    return os.path.normcase(os.path.normpath(path))


def workbook_paths(display_names):
    """The ROT display names that are workbook files (by extension), each once, in order."""
    out, seen = [], set()
    for name in display_names:
        if isinstance(name, str) and name.lower().endswith(EXTENSIONS) and _key(name) not in seen:
            seen.add(_key(name))
            out.append(name)
    return out


def rot_display_names():
    """Display names of every moniker in the Running Object Table (no Excel needed to read it)."""
    import pythoncom
    rot = pythoncom.GetRunningObjectTable()
    ctx = pythoncom.CreateBindCtx(0)
    names = []
    for moniker in rot.EnumRunning():
        try:
            names.append(moniker.GetDisplayName(ctx, None))
        except Exception:       # a moniker that cannot name itself is not a workbook we can bind
            continue
    return names


def bind(path):
    """The open workbook at `path`. Only called for a path just seen in the ROT."""
    import win32com.client
    return win32com.client.GetObject(path)


def list_open(names_fn=rot_display_names):
    return workbook_paths(names_fn())


def _rot_now(path, names_fn):
    """`path` in the ROT's spelling if it is in the ROT right now, else None. Read immediately before every
    bind: GetObject of a path that is no longer in the ROT would start Excel and open the file."""
    return {_key(p): p for p in list_open(names_fn)}.get(_key(path))


def _close_one(path, wb, names_fn, bind_fn):
    """Close one workbook without ever discarding changes. `wb` is the object bound for its save, or None
    (close-only): then the ROT is read again and the path bound only if it is still there."""
    if wb is None:
        rot_path = _rot_now(path, names_fn)
        if rot_path is None:
            return {"path": path, "closed": False, "reason": NOT_OPEN}
        wb = bind_fn(rot_path)
    if not bool(wb.Saved):      # re-read right before the close: Close(False) would drop these changes
        return {"path": path, "closed": False, "reason": UNSAVED}
    wb.Close(False)
    return {"path": path, "closed": True}


def save_close(save, close, names_fn=rot_display_names, bind_fn=bind, open_paths=None):
    """Save each `save` path that is open (only when Excel holds unsaved changes), then close each `close` path
    that is open and has no unsaved changes. `open_paths` is the ROT's workbook list when the caller has
    just read it."""
    if open_paths is None:
        open_paths = list_open(names_fn)
    by_key = {_key(p): p for p in open_paths}
    saved, closed, books, save_ok = [], [], {}, {}
    for path in save or []:
        rot_path = _rot_now(path, names_fn) if _key(path) in by_key else None
        if rot_path is None:
            saved.append({"path": path, "saved": False, "error": NOT_OPEN})
            save_ok[_key(path)] = False
            continue
        try:
            wb = bind_fn(rot_path)
            books[_key(path)] = wb
            changed = not bool(wb.Saved)
            if changed:
                wb.Save()
            saved.append({"path": path, "saved": True, "changed": changed})
            save_ok[_key(path)] = True
        except Exception as e:
            saved.append({"path": path, "saved": False, "error": str(e)})
            save_ok[_key(path)] = False
    for path in close or []:
        if save_ok.get(_key(path)) is False:
            closed.append({"path": path, "closed": False, "reason": SAVE_FAILED})
            continue
        if _key(path) not in by_key:
            closed.append({"path": path, "closed": False, "reason": NOT_OPEN})
            continue
        try:
            closed.append(_close_one(path, books.get(_key(path)), names_fn, bind_fn))
        except Exception as e:
            closed.append({"path": path, "closed": False, "reason": str(e)})
    return {"saved": saved, "closed": closed}


def planned_save_close(before, targets, allowed_close, names_fn=rot_display_names, bind_fn=bind):
    """Read the ROT once, plan (excel_exports.plan), save and close. Nothing is closed when `before` is None
    (unknown which workbooks were open before the run)."""
    import excel_exports
    now = list_open(names_fn)
    p = excel_exports.plan(before or [], now, targets)
    allowed = {_key(a) for a in allowed_close}
    close = [] if before is None else [c for c in p["close"] if _key(c) in allowed]
    out = save_close(p["save"], close, names_fn, bind_fn, open_paths=now)
    return {"open": now, **out}


def _paths(value):
    return isinstance(value, list) and all(isinstance(p, str) for p in value)


def handle(payload, names_fn=rot_display_names, bind_fn=bind):
    if not isinstance(payload, dict):
        return {"error": "the payload must be a JSON object"}
    action = payload.get("action")
    if action == "list":
        return list_open(names_fn)
    if action == "save_close" and "targets" in payload:
        before, targets, allowed = payload.get("before"), payload.get("targets"), payload.get("allowedClose") or []
        if not (before is None or _paths(before)) or not _paths(targets) or not _paths(allowed):
            return {"error": "before, targets and allowedClose must be lists of paths"}
        return planned_save_close(before, targets, allowed, names_fn, bind_fn)
    if action == "save_close":
        save, close = payload.get("save") or [], payload.get("close") or []
        if not _paths(save) or not _paths(close):
            return {"error": "save and close must be lists of paths"}
        return save_close(save, close, names_fn, bind_fn)
    return {"error": f"unknown action {action!r}"}


def main(argv):
    try:
        payload = json.loads(argv[1]) if len(argv) > 1 else None
        answer = handle(payload)
    except Exception as e:      # always answer one JSON line, never a traceback the caller must parse
        answer = {"error": str(e)}
    print(json.dumps(answer))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
