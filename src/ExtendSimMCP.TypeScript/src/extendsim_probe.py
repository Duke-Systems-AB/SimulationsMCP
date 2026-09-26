"""
ExtendSim probe - a long-lived helper that LOOKS at ExtendSim's windows while the backend
is busy inside a COM call (spec 2026-09-24-extendsim-stuck-recovery-design.md, §5.2).

It never talks to ExtendSim over COM - only reads windows. Checks are win32 first
(EnumWindows, IsHungAppWindow): instant, and safe to poll even while a simulation is
running. UI Automation is used only to read the text of an already-open dialog box - live
testing (2026-09-25) measured it blocking for the full length of a run (23s, 30s) when
queried with no dialog open, so it is only ever called once win32 has already found a
dialog window. When asked, it clicks OK on a blocking dialog, exactly as dialog_watcher.py
does. One JSON request per line on stdin, one JSON answer per line on stdout:

  {"id": 1, "op": "check"}   -> dialogs, windowFound, windowResponding (IsHungAppWindow)
  {"id": 2, "op": "dismiss"} -> what was clicked (nothing when ExtendSim is not running)
  {"id": 3, "op": "ping"}    -> {"id": 3, "ok": true}
"""
import ctypes
import json
import sys

import dialog_watcher as dw


def _is_hung(hwnd) -> bool:
    return bool(ctypes.windll.user32.IsHungAppWindow(hwnd))


def _uia_boxes() -> list:
    """QMessageBox dialogs inside ExtendSim via UI Automation, without clicking."""
    if not dw._uia_available:
        return []
    try:
        uia = dw._get_uia()
        main = dw._uia_find_main_window(uia)
        if main is None:
            return []
        # A start-up reminder's text is never passed on: the 2026 renewal box shows the
        # activation key (seen live 2026-09-26), and these results reach the AI and telemetry.
        return [{"title": b["title"],
                 "texts": [] if dw._is_startup_reminder(b["title"] or "") else b["texts"],
                 "buttons": [x["name"] for x in b["buttons"]]}
                for b in dw._uia_find_message_boxes(uia, main)]
    except Exception:
        return []


def check() -> dict:
    # win32 first: instant, and safe to call while a simulation is running. Only ask UIA
    # to read a dialog's text once win32 has confirmed one is actually open - querying it
    # with nothing open can block for as long as the run takes.
    win32_dialogs, main_hwnd = dw._find_extendsim_dialog_windows()
    pid = dw._window_pid(main_hwnd) if main_hwnd is not None else None
    win32_error_dialogs = dw._find_win32_error_dialogs(pid) if pid is not None else []
    all_win32_dialogs = win32_dialogs + win32_error_dialogs

    dialogs = _uia_boxes() if all_win32_dialogs else []
    if not dialogs:
        dialogs = [{"title": d["title"], "texts": [], "buttons": []} for d in all_win32_dialogs]
    return {
        "dialogs": dialogs,
        "windowFound": main_hwnd is not None,
        "windowResponding": (not _is_hung(main_hwnd)) if main_hwnd is not None else None,
    }


def dismiss() -> dict:
    _, main_hwnd = dw._find_extendsim_dialog_windows()
    if main_hwnd is None:
        return {"dismissed": []}          # never click anything when ExtendSim is not there
    return {"dismissed": dw.try_uia_strategy() or dw.try_win32gui_strategy()}


def handle(req: dict) -> dict:
    op = req.get("op")
    try:
        if op == "check":
            body = check()
        elif op == "dismiss":
            body = dismiss()
        elif op == "ping":
            body = {}
        else:
            return {"id": req.get("id"), "ok": False, "error": f"unknown op {op!r}"}
    except Exception as e:  # the probe must survive anything a window can throw at it
        return {"id": req.get("id"), "ok": False, "error": f"{type(e).__name__}: {e}"}
    return {"id": req.get("id"), "ok": True, **body}


def serve(stdin, stdout) -> None:
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            stdout.write(json.dumps({"ok": False, "error": "invalid JSON"}) + "\n")
            stdout.flush()
            continue
        stdout.write(json.dumps(handle(req)) + "\n")
        stdout.flush()


if __name__ == "__main__":
    # No comtypes.CoUninitialize() here (unlike dialog_watcher.main()): this process is
    # long-lived and may be killed rather than exited cleanly, so there is no reliable
    # place to pair it from; process exit reclaims the COM apartment either way.
    serve(sys.stdin, sys.stdout)
