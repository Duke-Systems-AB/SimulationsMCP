"""Save the Excel workbooks a run exported to, and close the workbooks a run opened.

Measured live on ExtendSim 2024 and 2026, 2026-10-09/10: a Data Import Export block's Excel export
reaches Excel's in-memory workbook only (Saved=False) - ExtendSim never saves it. Binding the open workbook
through the Running Object Table by full path and calling Save() took 0.03-0.05 s and updated the file on
disk; binding never starts Excel. Workbooks the block opened stay open and locked until wb.Close.

This module is pure planning. Every Excel call lives in excel_save_worker.py, run as a separate process with
a deadline, so an Excel-side box (read-only file, compatibility check, sync conflict) can never hang the
server. The worker is injected (`worker(payload) -> dict`); run_worker is the real one.

Rules: never start Excel; only bind workbooks already in the ROT; never save or close a
workbook that is not an export target of this model, except closing workbooks this run opened - and of
those, only ones the model's own Excel blocks point at, so a workbook the user opened during the run is
never closed unsaved.
"""
import json
import os
import subprocess
import sys

import data_files as df

DEADLINE_S = 20
WORKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "excel_save_worker.py")
NOT_OPEN = ("the workbook is not open in Excel after the run, so there was nothing to save (the export did not "
            "run, or Excel was closed)")
NOT_ON_DISK = "Excel reported the save, but the file on disk did not change"


def norm(path):
    """Comparison key for a Windows path: case-insensitive, one separator style."""
    return os.path.normcase(os.path.normpath(path))


def _excel_entries(entries):
    return [e for e in entries or [] if e.get("fileType") == df.EXCEL_TYPE]


def _block_path(entry, model_folder):
    """The full path ExtendSim uses for a block's file: absolute as stored, or a bare name in the model
    folder (the resolution data_files measured). A relative path with a folder is never found by ExtendSim,
    so it has no workbook to save."""
    stored = entry.get("storedPath")
    if not stored or not (os.path.isabs(stored) or df.is_bare_name(stored)):
        return None
    path = entry.get("resolvedPath")
    if not path:
        path, _exists = df.resolve(stored, model_folder)
    return path


def export_target_blocks(entries, model_folder=None):
    """[(blockId, path)] for every Excel export block with auto export on, in block order."""
    out = []
    for e in _excel_entries(entries):
        if e.get("direction") == "export" and e.get("autoImportExport"):
            path = _block_path(e, model_folder)
            if path:
                out.append((e["blockId"], path))
    return out


def export_targets(entries, model_folder=None):
    """The workbook paths a run's auto Excel exports write to (each once)."""
    seen, out = set(), []
    for _bid, path in export_target_blocks(entries, model_folder):
        if norm(path) not in seen:
            seen.add(norm(path))
            out.append(path)
    return out


def model_workbooks(entries, model_folder=None):
    """Every workbook path any Excel block of the model (import or export, auto or not) points at."""
    return [p for p in (_block_path(e, model_folder) for e in _excel_entries(entries)) if p]


def plan(before, after, targets):
    """{save, close}: save = targets open after the run (in the ROT's spelling, which the worker binds by);
    close = workbooks open after the run that were not open before it - only those. A target that was open
    before the run is saved, never closed."""
    before_keys = {norm(p) for p in before or []}
    target_keys = {norm(p) for p in targets or []}
    save = [p for p in after or [] if norm(p) in target_keys]
    close = [p for p in after or [] if norm(p) not in before_keys]
    return {"save": save, "close": close}


def run_worker(payload, deadline_s=DEADLINE_S, runner=subprocess.run):
    """Run excel_save_worker.py with `payload`; its JSON answer, or {"error": ...}. Never raises."""
    try:
        done = runner([sys.executable, WORKER, json.dumps(payload)], capture_output=True, text=True,
                      timeout=deadline_s, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return {"error": f"Excel did not answer within {deadline_s} s - Excel may be showing a box (a read-only "
                         "file, a compatibility check, a sync conflict); check Excel"}
    except Exception as e:      # the worker could not even start
        return {"error": f"the Excel worker could not run: {e}"}
    lines = [ln for ln in (done.stdout or "").splitlines() if ln.strip()]
    if done.returncode != 0 or not lines:
        tail = (done.stderr or "").strip().splitlines()[-1:] or ["no output"]
        return {"error": f"the Excel worker failed (exit {done.returncode}): {tail[0]}"}
    try:
        answer = json.loads(lines[-1])
    except ValueError:
        return {"error": f"the Excel worker answered something that is not JSON: {lines[-1][:200]}"}
    if isinstance(answer, list):
        return {"workbooks": answer}
    if not isinstance(answer, dict):
        return {"error": "the Excel worker answered something unexpected"}
    return answer


def open_workbooks(worker):
    """(paths, error): the workbooks open in Excel right now ([] when Excel is not running)."""
    answer = worker({"action": "list"})
    books = answer.get("workbooks") if isinstance(answer, dict) else None
    if not isinstance(books, list) or not all(isinstance(p, str) for p in books):
        return None, (answer.get("error") if isinstance(answer, dict) and answer.get("error")
                      else "the Excel worker gave no workbook list")
    return books, None


def _mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def snapshot(entries, worker, model_folder=None):
    """Before a run: which workbooks are open and the targets' file times. None when the model has no
    Excel block at all (nothing to do, Excel is not asked)."""
    if not _excel_entries(entries):
        return None
    targets = export_target_blocks(entries, model_folder)
    before, error = open_workbooks(worker)
    return {"targets": targets, "allowedClose": model_workbooks(entries, model_folder),
            "before": before, "beforeError": error,
            "mtimes": {norm(p): _mtime(p) for _b, p in targets}}


def _planned(worker, before, targets, allowed):
    """One worker call: it reads the ROT, plans (see plan), saves and closes. (answer, error)."""
    answer = worker({"action": "save_close", "before": before, "targets": targets, "allowedClose": allowed})
    if (not isinstance(answer, dict) or answer.get("error") or not isinstance(answer.get("saved"), list)
            or not isinstance(answer.get("closed"), list) or not isinstance(answer.get("open"), list)):
        return None, (answer.get("error") if isinstance(answer, dict) and answer.get("error")
                      else "the Excel worker gave no usable answer")
    return answer, None


def close_opened(before, worker, allowed):
    """Close the workbooks opened since `before` that are among `allowed` paths (verifyImport's clean-up).
    {"closedWorkbooks", "warnings"}."""
    out = {"closedWorkbooks": [], "warnings": []}
    answer, error = _planned(worker, before, [], list(allowed or []))
    if error:
        out["warnings"].append(f"the workbooks opened here could not be closed: {error}")
        return out
    _collect_closed(answer, out)
    return out


def _collect_closed(answer, out):
    for c in answer["closed"]:
        if isinstance(c, dict) and c.get("closed"):
            out["closedWorkbooks"].append(c.get("path"))
        elif isinstance(c, dict):
            out["warnings"].append(f"{c.get('path')} was left open in Excel: "
                                   f"{c.get('reason') or c.get('error') or 'not closed'}")


def finish(state, worker):
    """After a finished run, in ONE worker call: save the export targets open in Excel, close what the run
    opened (only the model's own workbooks, only without unsaved changes), then verify the saves on disk.
    {"savedExports": [{blockId, path, saved, error?}], "closedWorkbooks": [...], "warnings": [...]}.
    Nothing here raises or changes the run's own outcome."""
    out = {"savedExports": [], "closedWorkbooks": [], "warnings": []}
    targets = state["targets"]
    if state["before"] is None:
        out["warnings"].append("could not tell which workbooks were open before the run "
                               f"({state['beforeError']}), so none were closed")
    answer, error = _planned(worker, state["before"], [t for _b, t in targets], state["allowedClose"])
    if error:
        out["savedExports"] = [{"blockId": b, "path": p, "saved": False, "error": error} for b, p in targets]
        out["warnings"].append(f"Excel exports were not saved: {error}")
        return out
    by_path = {norm(s.get("path", "")): s for s in answer["saved"] if isinstance(s, dict)}
    open_keys = {norm(a) for a in answer["open"] if isinstance(a, str)}
    for bid, path in targets:
        key = norm(path)
        if key not in open_keys:
            out["savedExports"].append({"blockId": bid, "path": path, "saved": False, "error": NOT_OPEN})
            continue
        s = by_path.get(key)
        if s is None or not s.get("saved"):
            out["savedExports"].append({"blockId": bid, "path": path, "saved": False,
                                        "error": (s or {}).get("error") or "the worker did not save it"})
            continue
        entry = {"blockId": bid, "path": path, "saved": True}
        if s.get("changed"):
            now, then = _mtime(path), state["mtimes"].get(key)
            if now is None or (then is not None and now <= then):
                entry = {"blockId": bid, "path": path, "saved": False, "error": NOT_ON_DISK}
        else:
            entry["note"] = "Excel had no unsaved changes in this workbook"
        out["savedExports"].append(entry)
    _collect_closed(answer, out)
    return out
