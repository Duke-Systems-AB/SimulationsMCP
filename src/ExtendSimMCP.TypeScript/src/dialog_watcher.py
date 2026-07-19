"""
ExtendSim Dialog Watcher - Detects and dismisses blocking popup dialogs.

When ExtendSim shows a modal dialog (e.g., UserError, compile error), COM calls
block indefinitely. This script uses two strategies to find and dismiss dialogs:

  Strategy 1 (UIA): Windows UI Automation - works when ExtendSim is responsive.
    Finds QMessageBox elements, reads their text, clicks OK via InvokePattern.

  Strategy 2 (win32gui fallback): Raw Windows API - works in Ghost/hung state.
    Finds Qt5QWindowIcon windows titled "ExtendSim", uses SetForegroundWindow +
    keyboard simulation to press Enter and dismiss the dialog.

Architecture: Runs as a SEPARATE PROCESS from the Python COM backend.
This is required because the COM backend's main thread is blocked on the
dialog and cannot interact with it.

Usage:
  python dialog_watcher.py [timeout_sec] [poll_sec]

Output: JSON to stdout
  {"found": true, "dialogs": [{"title": "...", "texts": ["..."], "dismissed": true}]}
  {"found": false, "timeout": true}

Requires: pywin32, comtypes
"""
import json
import sys
import time
import ctypes
import ctypes.wintypes
import win32gui
import win32con

# ─── Strategy 1: UI Automation (for responsive ExtendSim) ─────────────────────

_uia_available = False
try:
    import comtypes
    comtypes.CoInitialize()
    import comtypes.client
    UIAutomationClient = comtypes.client.GetModule("UIAutomationCore.dll")
    _uia_available = True
except Exception:
    pass


def _get_uia():
    return comtypes.CoCreateInstance(
        UIAutomationClient.CUIAutomation._reg_clsid_,
        interface=UIAutomationClient.IUIAutomation,
        clsctx=comtypes.CLSCTX_INPROC_SERVER
    )


def _uia_find_main_window(uia):
    """Find ExtendSim main window via UIA."""
    root = uia.GetRootElement()
    cond = uia.CreateTrueCondition()
    children = root.FindAll(UIAutomationClient.TreeScope_Children, cond)
    for i in range(children.Length):
        elem = children.GetElement(i)
        try:
            name = elem.CurrentName or ""
            if "ExtendSim" in name and ("[" in name or "Pro" in name):
                return elem
        except Exception:
            pass
    return None


def _uia_find_message_boxes(uia, main_win):
    """Find QMessageBox dialogs inside ExtendSim via UIA."""
    cond = uia.CreateTrueCondition()
    try:
        descendants = main_win.FindAll(
            UIAutomationClient.TreeScope_Descendants, cond
        )
    except Exception:
        return []

    boxes = []
    for i in range(descendants.Length):
        elem = descendants.GetElement(i)
        try:
            cls = elem.CurrentClassName or ""
            if cls != "QMessageBox":
                continue

            title = elem.CurrentName or ""
            children = elem.FindAll(
                UIAutomationClient.TreeScope_Children, cond
            )
            texts = []
            buttons = []

            for j in range(children.Length):
                child = children.GetElement(j)
                try:
                    cn = child.CurrentName or ""
                    cc = child.CurrentClassName or ""
                    if cc == "QLabel" and cn:
                        texts.append(cn)
                    elif cc == "QDialogButtonBox":
                        btns = child.FindAll(
                            UIAutomationClient.TreeScope_Children, cond
                        )
                        for k in range(btns.Length):
                            b = btns.GetElement(k)
                            try:
                                if b.CurrentName:
                                    buttons.append({
                                        "name": b.CurrentName,
                                        "elem": b
                                    })
                            except Exception:
                                pass
                    elif child.CurrentControlType == 50000 and cn:
                        buttons.append({"name": cn, "elem": child})
                except Exception:
                    pass

            boxes.append({
                "title": title,
                "texts": texts,
                "buttons": buttons
            })
        except Exception:
            pass
    return boxes


def _uia_dismiss_box(box):
    """Dismiss QMessageBox via UIA InvokePattern on OK button."""
    for btn in box["buttons"]:
        if btn["name"] in ("OK", "Ok", "ok"):
            try:
                pat = btn["elem"].GetCurrentPattern(
                    UIAutomationClient.UIA_InvokePatternId
                )
                inv = pat.QueryInterface(
                    UIAutomationClient.IUIAutomationInvokePattern
                )
                inv.Invoke()
                return True
            except Exception:
                pass
    return False


def try_uia_strategy():
    """
    Strategy 1: Use UI Automation to find and dismiss dialogs.
    Returns list of dismissed dialogs, or empty list if UIA can't find anything.
    """
    if not _uia_available:
        return []

    try:
        uia = _get_uia()
        main_win = _uia_find_main_window(uia)
        if not main_win:
            return []

        boxes = _uia_find_message_boxes(uia, main_win)
        if not boxes:
            return []

        dismissed = []
        for b in boxes:
            is_maintenance = "Maintenance" in (b["title"] or "")
            if is_maintenance:
                _uia_dismiss_box(b)
                continue

            ok = _uia_dismiss_box(b)
            dismissed.append({
                "title": b["title"],
                "texts": b["texts"],
                "buttons": [btn["name"] for btn in b["buttons"]],
                "dismissed": ok,
                "method": "UIA"
            })

        # Check for chained dialogs
        if dismissed:
            time.sleep(0.5)
            boxes2 = _uia_find_message_boxes(uia, main_win)
            for b2 in boxes2:
                if "Maintenance" not in (b2["title"] or ""):
                    ok = _uia_dismiss_box(b2)
                    dismissed.append({
                        "title": b2["title"],
                        "texts": b2["texts"],
                        "buttons": [btn["name"] for btn in b2["buttons"]],
                        "dismissed": ok,
                        "method": "UIA"
                    })

        return dismissed
    except Exception:
        return []


# ─── Strategy 2: win32gui fallback (for Ghost/hung state) ─────────────────────

# SendInput structures for keyboard simulation
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
VK_RETURN = 0x0D


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.wintypes.WORD),
        ("wScan", ctypes.wintypes.WORD),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("time", ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class INPUT(ctypes.Structure):
    class _INPUT(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT)]

    _fields_ = [
        ("type", ctypes.wintypes.DWORD),
        ("_input", _INPUT),
    ]


def _send_enter_key():
    """Simulate physical Enter key press via SendInput."""
    inputs = (INPUT * 2)()

    # Key down
    inputs[0].type = INPUT_KEYBOARD
    inputs[0]._input.ki.wVk = VK_RETURN

    # Key up
    inputs[1].type = INPUT_KEYBOARD
    inputs[1]._input.ki.wVk = VK_RETURN
    inputs[1]._input.ki.dwFlags = KEYEVENTF_KEYUP

    ctypes.windll.user32.SendInput(2, ctypes.pointer(inputs[0]),
                                    ctypes.sizeof(INPUT))


def _find_extendsim_dialog_windows():
    """Find ExtendSim dialog windows (not the main window) via win32gui."""
    dialogs = []
    main_hwnd = None

    def enum_callback(hwnd, _):
        nonlocal main_hwnd
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd)
        cls = win32gui.GetClassName(hwnd)

        if not title or "ExtendSim" not in title:
            return True

        # Main window has model name in brackets or "Pro" in title
        if "[" in title and ("Pro" in title or "DE" in title or "CP" in title):
            main_hwnd = hwnd
        elif title == "ExtendSim" and cls in ("Qt5QWindowIcon", "Ghost"):
            # This is likely a QMessageBox dialog
            dialogs.append({"hwnd": hwnd, "title": title, "class": cls})
        elif "Maintenance" in title:
            dialogs.append({
                "hwnd": hwnd, "title": title, "class": cls,
                "is_maintenance": True
            })
        return True

    win32gui.EnumWindows(enum_callback, None)
    return dialogs, main_hwnd


def _find_win32_error_dialogs():
    """Find standard Windows #32770 error dialogs."""
    dialogs = []

    def enum_callback(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return True
        cls = win32gui.GetClassName(hwnd)
        if cls != "#32770":
            return True

        title = win32gui.GetWindowText(hwnd)
        texts = []
        buttons = []

        def enum_children(child_hwnd, _):
            child_text = win32gui.GetWindowText(child_hwnd)
            child_cls = win32gui.GetClassName(child_hwnd)
            if child_cls == "Static" and child_text:
                texts.append(child_text.strip())
            elif child_cls == "Button" and child_text:
                buttons.append({"text": child_text, "hwnd": child_hwnd})
            return True

        try:
            win32gui.EnumChildWindows(hwnd, enum_children, None)
        except Exception:
            pass

        if texts or buttons:
            dialogs.append({
                "hwnd": hwnd, "title": title, "texts": texts,
                "buttons": buttons
            })
        return True

    win32gui.EnumWindows(enum_callback, None)
    return dialogs


def try_win32gui_strategy():
    """
    Strategy 2: Use raw win32gui to find and dismiss dialogs.
    Works even when ExtendSim is in Ghost (Not Responding) state.
    Returns list of dismissed dialogs.
    """
    dismissed = []

    # 1. Handle standard Win32 #32770 error dialogs (click OK button)
    win32_dialogs = _find_win32_error_dialogs()
    for d in win32_dialogs:
        for btn in d["buttons"]:
            if btn["text"] in ("OK", "Ok", "ok"):
                try:
                    win32gui.SendMessage(
                        btn["hwnd"], win32con.BM_CLICK, 0, 0
                    )
                    dismissed.append({
                        "title": d["title"],
                        "texts": d["texts"],
                        "dismissed": True,
                        "method": "win32gui BM_CLICK"
                    })
                except Exception:
                    pass
                break

    # 2. Handle Qt ExtendSim dialogs (SetForegroundWindow + SendInput Enter)
    qt_dialogs, _main_hwnd = _find_extendsim_dialog_windows()
    for d in qt_dialogs:
        if d.get("is_maintenance"):
            # Dismiss maintenance dialog too
            try:
                win32gui.SetForegroundWindow(d["hwnd"])
                time.sleep(0.2)
                _send_enter_key()
                time.sleep(0.3)
            except Exception:
                pass
            continue

        try:
            # Bring the dialog to foreground and press Enter
            win32gui.SetForegroundWindow(d["hwnd"])
            time.sleep(0.2)
            _send_enter_key()
            dismissed.append({
                "title": d["title"],
                "texts": ["(text not readable in Ghost state)"],
                "dismissed": True,
                "method": "win32gui SendInput"
            })
            time.sleep(0.3)
        except Exception:
            pass

    return dismissed


# ─── Main polling loop ─────────────────────────────────────────────────────────

def main():
    timeout_sec = float(sys.argv[1]) if len(sys.argv) > 1 else 8
    poll_sec = float(sys.argv[2]) if len(sys.argv) > 2 else 0.5

    deadline = time.time() + timeout_sec

    while time.time() < deadline:
        # Strategy 1: UI Automation (best - can read dialog text)
        uia_results = try_uia_strategy()
        if uia_results:
            print(json.dumps({"found": True, "dialogs": uia_results}))
            return 0

        # Strategy 2: win32gui fallback (Ghost state - limited text reading)
        win32_results = try_win32gui_strategy()
        if win32_results:
            print(json.dumps({"found": True, "dialogs": win32_results}))
            return 0

        time.sleep(poll_sec)

    # Timeout - no dialog found
    print(json.dumps({"found": False, "timeout": True}))
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        # M22: Pair CoInitialize with CoUninitialize
        if _uia_available:
            try:
                comtypes.CoUninitialize()
            except Exception:
                pass
