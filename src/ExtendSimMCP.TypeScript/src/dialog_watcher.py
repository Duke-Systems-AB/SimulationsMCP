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
import win32process

import dialog_report


def _progress(payload):
    """One progress line on stderr, at once (see dialog_report): if the caller kills this pass
    before it prints its JSON, these still say which boxes ExtendSim showed and what they said."""
    try:
        sys.stderr.write(dialog_report.progress_line(payload) + "\n")
        sys.stderr.flush()
    except Exception:
        pass

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


# Rounds of "answer/dismiss, wait, look again" in one pass. ExtendSim chains boxes - e.g. the
# unused-pools prompt, then two confirmation boxes (measured live 2026-10-05) - and once a box has
# been dismissed the TypeScript layer only waits for the command's answer; it does not look for
# further boxes. So one pass keeps going while each round still finds something.
CHAINED_ROUNDS = 5


def _uia_round(uia, main_win):
    """Dismiss every QMessageBox open right now. Returns the report entries."""
    dismissed = []
    for b in _uia_find_message_boxes(uia, main_win):
        # Start-up reminders are clicked but never reported: the 2026 renewal box carries
        # the activation key in its text, which must not reach the AI.
        if _is_startup_reminder(b["title"] or ""):
            _uia_dismiss_box(b)
            continue
        _progress({"dialog": {"title": b["title"], "texts": b["texts"],
                              "buttons": [btn["name"] for btn in b["buttons"]]}})
        ok = _uia_dismiss_box(b)
        dismissed.append({
            "title": b["title"],
            "texts": b["texts"],
            "buttons": [btn["name"] for btn in b["buttons"]],
            "dismissed": ok,
            "method": "UIA"
        })
    return dismissed


def _plain_box_buttons(hwnd):
    """(texts, buttons) for a plain box, read-only - QLabel text and the box's own QPushButton
    names. Deliberately narrower than _uia_read_and_press's scan: a QMessageBox exposes the
    title bar's own system Close button as another ControlType=Button element alongside its real
    QDialogButtonBox buttons (measured live 2026-10-08 on the Optimizer's "Optimization converged"
    box: buttons by _uia_read_and_press's scan were ["Stäng", "OK"], "Stäng" being that system
    button, class ""); counting it as a real button would stop "exactly one OK button" ever
    matching and this would never run. Only class "QPushButton" counts here."""
    if not _uia_available:
        return None
    try:
        uia = _get_uia()
        box = uia.ElementFromHandle(hwnd)
        found = box.FindAll(UIAutomationClient.TreeScope_Descendants, uia.CreateTrueCondition())
        texts, buttons = [], []
        for i in range(found.Length):
            e = found.GetElement(i)
            try:
                name = e.CurrentName or ""
                if not name:
                    continue
                if e.CurrentControlType == 50000 and e.CurrentClassName == "QPushButton":
                    buttons.append(name)
                elif e.CurrentControlType == 50020:
                    texts.append(name)
            except Exception:
                pass
        return texts, buttons
    except Exception:
        return None


def _dismiss_plain_boxes():
    """Plain QMessageBox-style boxes - win32-titled exactly "ExtendSim", no known answer or
    prompt (KNOWN_BOXES/KNOWN_PROMPTS) and not a startup reminder - dismissed directly by hwnd via
    UIA, the same method _uia_read_and_press already uses for KNOWN_PROMPTS. Only boxes belonging
    to the SAME process as the main window are touched - with no main window found at all, or
    with its pid unreadable (so no process can be confirmed either way), nothing is touched - and
    only when the box's own buttons are exactly ["OK"]; any other shape
    (OK/Cancel, Yes/No, no OK at all) is reported, never pressed - this function only knows one
    safe answer, and pressing the wrong button on an unexpected box could be destructive.

    _uia_round (below) can only find a box that is a UIA *descendant* of the element
    _uia_find_main_window returns, and that lookup matches the main window by its UIA
    CurrentName containing "[" or "Pro". Bug found live 2026-10-08: an Optimizer run's own
    "Optimization converged to X%." box (exactly this box shape) was never dismissed - over an
    hour, needing a person - because the running main window's UIA CurrentName was plain
    "ExtendSim" (no "[" or "Pro", unlike its win32 window TEXT), so _uia_find_main_window
    returned None and _uia_round was never even reached. _find_extendsim_dialog_windows already
    finds this box correctly (by win32 title/class, independent of the main window's UIA name);
    this dismisses it straight from that hwnd, not through the main-window search.
    """
    dialogs, main_hwnd = _find_extendsim_dialog_windows()
    if main_hwnd is None:
        return []
    main_pid = _window_pid(main_hwnd)
    if main_pid is None:
        return []  # the main window's own pid couldn't be read - no process can be confirmed
    out = []
    for d in dialogs:
        if d.get("answer") or d.get("prompts") or d.get("is_maintenance"):
            continue
        if _window_pid(d["hwnd"]) != main_pid:
            continue
        read = _plain_box_buttons(d["hwnd"])
        if read is None:
            continue
        texts, buttons = read
        # Written before pressing: the press is what can run long (a UIA Invoke on such a box
        # took ~2 s to return, measured live 2026-10-08), and a pass killed then loses its JSON.
        _progress({"dialog": {"title": d["title"], "texts": texts, "buttons": buttons}})
        if buttons != ["OK"]:
            out.append({"title": d["title"], "texts": texts, "buttons": buttons,
                        "dismissed": False, "method": "UIA"})
            continue
        pressed = _uia_read_and_press(d["hwnd"], "OK")
        out.append({"title": d["title"], "texts": texts, "buttons": buttons,
                    "dismissed": bool(pressed and pressed[2]), "method": "UIA"})
    return out


def try_uia_strategy():
    """
    Strategy 1: Use UI Automation to find and dismiss dialogs.
    Returns list of dismissed dialogs, or empty list if UIA can't find anything.

    Known boxes (KNOWN_BOXES / KNOWN_PROMPTS) are answered first, then plain "ExtendSim"
    QMessageBox-style boxes get OK directly by hwnd (_dismiss_plain_boxes - only in ExtendSim's
    own process, only when its buttons are exactly ["OK"]), then any remaining message boxes
    found as descendants of the main window get OK; chained boxes are followed for up to
    CHAINED_ROUNDS rounds. A box that could not be answered ends the pass at once - it needs a
    person, and nothing behind it can appear. With no main window found at all, the
    descendant-search step is skipped (nothing to search from) but known/plain boxes are still
    followed for the same CHAINED_ROUNDS - this is how the live bug's box was actually dismissed.
    """
    if not _uia_available:
        return []

    dismissed = []
    uia = main_win = None
    main_window_missing = False   # _uia_round needs main_win; without it, skip just that step
    final = False      # set after a round of ordinary boxes: one more look, then report
    try:
        for _ in range(CHAINED_ROUNDS):
            known = answer_known_boxes()
            dismissed.extend(known)
            if any(not d.get("dismissed") for d in known):
                return dismissed
            plain = _dismiss_plain_boxes()
            dismissed.extend(plain)
            if any(not d.get("dismissed") for d in plain):
                return dismissed
            boxes = []
            if not main_window_missing:
                if uia is None:
                    uia = _get_uia()
                    main_win = _uia_find_main_window(uia)
                    main_window_missing = not main_win
                if not main_window_missing:
                    boxes = _uia_round(uia, main_win)
                    dismissed.extend(boxes)
                    if any(not d.get("dismissed") for d in boxes):
                        return dismissed
            if not known and not plain and (final or not boxes):
                # Report as soon as the chain is over: the command's own answer usually follows
                # within a fraction of a second, and a result printed after it is dropped by the
                # TypeScript layer (the dialog would be missing from dialogsDuringCommand).
                break
            final = not known and not plain
            time.sleep(0.5)
    except Exception:
        return dismissed
    return dismissed


def _uia_read_and_press(hwnd, answer):
    """Read a box's texts and buttons through UIA and invoke the button named `answer`.
    Returns (texts, buttons, pressed), or None when UIA cannot reach the box."""
    if not _uia_available:
        return None
    try:
        uia = _get_uia()
        box = uia.ElementFromHandle(hwnd)
        found = box.FindAll(UIAutomationClient.TreeScope_Descendants, uia.CreateTrueCondition())
        texts, buttons, target = [], [], None
        for i in range(found.Length):
            e = found.GetElement(i)
            name = e.CurrentName or ""
            if not name:
                continue
            if e.CurrentControlType == 50000:          # button
                buttons.append(name)
                if name == answer:
                    target = e
            elif e.CurrentControlType == 50020:        # text
                texts.append(name)
        if target is None:
            return texts, buttons, False
        pat = target.GetCurrentPattern(UIAutomationClient.UIA_InvokePatternId)
        pat.QueryInterface(UIAutomationClient.IUIAutomationInvokePattern).Invoke()
        return texts, buttons, True
    except Exception:
        return None


def _answer_prompt(d):
    """A KNOWN_PROMPTS box: read its text, press the answer its text calls for, else nothing."""
    read = _uia_read_and_press(d["hwnd"], None)          # read only: no button is named None
    texts, buttons = (read[0], read[1]) if read is not None else ([], [])
    _progress({"dialog": {"title": d["title"], "texts": texts, "buttons": buttons}})
    answer = next((a for prefix, a in d["prompts"].items() if any(t.startswith(prefix) for t in texts)), None)
    if answer is None:
        return {"title": d["title"], "texts": texts, "buttons": buttons, "dismissed": False, "method": "UIA"}
    pressed = _uia_read_and_press(d["hwnd"], answer)
    return {"title": d["title"], "texts": texts, "buttons": buttons,
            "dismissed": bool(pressed and pressed[2]), "answered": answer, "method": "UIA"}


def answer_known_boxes():
    """Answer every open KNOWN_BOXES box of ExtendSim with its safe button. Returns the
    report entries (empty when none is open)."""
    dialogs, _ = _find_extendsim_dialog_windows()
    answered = []
    for d in dialogs:
        if d.get("prompts"):
            answered.append(_answer_prompt(d))
            continue
        answer = d.get("answer")
        if not answer:
            continue
        read = _uia_read_and_press(d["hwnd"], answer)
        if read is not None and read[2]:
            texts, buttons, _ = read
            method = "UIA"
        else:
            texts, buttons = (read[0], read[1]) if read is not None else ([], [])
            try:
                win32gui.SetForegroundWindow(d["hwnd"])
                time.sleep(0.2)
                _send_escape_key()
                time.sleep(0.3)
            except Exception:
                pass
            method = "win32gui Escape"
        _progress({"dialog": {"title": d["title"], "texts": texts, "buttons": buttons}})
        answered.append({"title": d["title"], "texts": texts, "buttons": buttons,
                         "dismissed": True, "answered": answer, "method": method})
    return answered


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


VK_ESCAPE = 0x1B


def _send_escape_key():
    """Simulate an Escape key press - a Qt dialog's reject, i.e. its Cancel button."""
    inputs = (INPUT * 2)()
    inputs[0].type = INPUT_KEYBOARD
    inputs[0]._input.ki.wVk = VK_ESCAPE
    inputs[1].type = INPUT_KEYBOARD
    inputs[1]._input.ki.wVk = VK_ESCAPE
    inputs[1]._input.ki.dwFlags = KEYEVENTF_KEYUP
    ctypes.windll.user32.SendInput(2, ctypes.pointer(inputs[0]), ctypes.sizeof(INPUT))


# Boxes whose title does not say "ExtendSim" and whose default button must NOT be pressed,
# with the one answer that changes nothing. Measured live 2026-10-03 on ExtendSim 2026:
# opening a library built before 2026.R1 shows "Unsigned Blocks Detected" with "Sign Blocks"
# (signs and SAVES the library files) and "Cancel" (leaves them unchanged; the blocks still
# work). Answered through UIA, else with Escape - never Enter.
KNOWN_BOXES = {"Unsigned Blocks Detected": "Cancel"}

# Yes/No prompts whose title does not say "ExtendSim", answered only when their TEXT starts
# with a known prefix; any other prompt with that title is reported, never clicked (and never
# Enter/Escape, whose meaning on a Yes/No box is unknown). Measured live 2026-10-05 on ExtendSim
# 2024: resources_configure on a model whose resource pools changed makes the Resource Manager
# ask whether to delete the now unused pools. "No" deletes nothing; the new resources and
# requirements are generated either way, the old empty pools stay listed.
KNOWN_PROMPTS = {
    "User Prompt": {"The model has unused resource pools": "No"},
}


def _is_startup_reminder(title):
    """ExtendSim's start-up reminders, which block COM until OK is pressed. Measured live
    2026-09-26: 2024 "Maintenance & Support Expired 452 Days Ago" (no "ExtendSim" in it),
    2026 "ExtendSim Subscription Renewal" (while the 2026 main window is "ExtendSim Pro
    Subscription", so "Subscription" alone is not enough). 2026-09-30: after the renewal date
    2026 starts with "ExtendSim Subscription Expired" (grace-period notice), same OK box."""
    return "Maintenance" in title or "Subscription Renewal" in title or "Subscription Expired" in title


def _find_extendsim_dialog_windows():
    """Find ExtendSim dialog windows (not the main window) via win32gui."""
    dialogs = []
    main_hwnd = None
    main_title = ""
    # Reminders without "ExtendSim" in the title; kept only if they belong to the main
    # window's process, so another program's "Maintenance" window is never touched.
    unnamed_reminders = []

    def enum_callback(hwnd, _):
        nonlocal main_hwnd, main_title
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd)
        cls = win32gui.GetClassName(hwnd)

        if not title:
            return True
        if "ExtendSim" not in title:
            if _is_startup_reminder(title):
                unnamed_reminders.append({"hwnd": hwnd, "title": title, "class": cls,
                                          "is_maintenance": True})
            elif title in KNOWN_BOXES:
                unnamed_reminders.append({"hwnd": hwnd, "title": title, "class": cls,
                                          "answer": KNOWN_BOXES[title]})
            elif title in KNOWN_PROMPTS:
                unnamed_reminders.append({"hwnd": hwnd, "title": title, "class": cls,
                                          "prompts": KNOWN_PROMPTS[title]})
            return True

        # Reminders first - "ExtendSim Maintenance ..." / "ExtendSim Subscription Renewal"
        # also start with "ExtendSim" and would otherwise be mistaken for the main window.
        if _is_startup_reminder(title):
            dialogs.append({
                "hwnd": hwnd, "title": title, "class": cls,
                "is_maintenance": True
            })
        elif title == "ExtendSim" and cls in ("Qt5QWindowIcon", "Ghost"):
            # This is likely a QMessageBox dialog
            dialogs.append({"hwnd": hwnd, "title": title, "class": cls})
        elif cls in ("Qt5QWindowIcon", "Ghost") and title.startswith("ExtendSim") and title != "ExtendSim":
            # Main window candidate. Covers "ExtendSim Pro" with no model open,
            # "ExtendSim Pro   [Model-1.mox]" with one, and DE/CP editions either way
            # (measured live 2026-09-25: a bare "ExtendSim Pro" window, no brackets, is
            # the real main window when no model is open). When several windows match,
            # prefer the one with a model open ("[" in the title); otherwise keep the
            # first match - never let a later, worse match overwrite an earlier one.
            if main_hwnd is None or ("[" in title and "[" not in main_title):
                main_hwnd = hwnd
                main_title = title
        return True

    win32gui.EnumWindows(enum_callback, None)
    if main_hwnd is not None and unnamed_reminders:
        main_pid = _window_pid(main_hwnd)
        dialogs.extend(d for d in unnamed_reminders
                       if main_pid is not None and _window_pid(d["hwnd"]) == main_pid)
    return dialogs, main_hwnd


def _window_pid(hwnd):
    """Owning process id of a window, or None if it can't be determined."""
    try:
        return win32process.GetWindowThreadProcessId(hwnd)[1]
    except Exception:
        return None


def _find_win32_error_dialogs(extendsim_pid=None):
    """Find standard Windows #32770 error dialogs that belong to ExtendSim's process.

    Without a known ExtendSim pid we cannot tell a #32770 dialog apart from any other
    application's, so we return nothing rather than risk clicking OK on someone else's
    message box.
    """
    if extendsim_pid is None:
        return []

    dialogs = []

    def enum_callback(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return True
        cls = win32gui.GetClassName(hwnd)
        if cls != "#32770":
            return True
        if _window_pid(hwnd) != extendsim_pid:
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

    # 1. Handle standard Win32 #32770 error dialogs (click OK button) - only ones that
    # belong to ExtendSim's own process (reuses the main-window lookup below).
    qt_dialogs, main_hwnd = _find_extendsim_dialog_windows()
    extendsim_pid = _window_pid(main_hwnd) if main_hwnd else None
    win32_dialogs = _find_win32_error_dialogs(extendsim_pid)
    for d in win32_dialogs:
        for btn in d["buttons"]:
            if btn["text"] in ("OK", "Ok", "ok"):
                _progress({"dialog": {"title": d["title"], "texts": d["texts"]}})
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

    # 2. Boxes with a known safe answer: Cancel, never Enter (see KNOWN_BOXES / KNOWN_PROMPTS)
    if any(d.get("answer") or d.get("prompts") for d in qt_dialogs):
        dismissed.extend(answer_known_boxes())

    # 3. Handle Qt ExtendSim dialogs (SetForegroundWindow + SendInput Enter)
    for d in qt_dialogs:
        if d.get("answer") or d.get("prompts"):
            continue
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

        # Read the text first if UIA still can; only a box that cannot be read gets the
        # placeholder, which the dismisser reports as dialog_report.UNREADABLE_BOX.
        read = _plain_box_buttons(d["hwnd"])
        texts = [t for t in (read[0] if read else []) if t] or [dialog_report.GHOST_PLACEHOLDER]
        _progress({"dialog": {"title": d["title"], "texts": texts}})
        try:
            # Bring the dialog to foreground and press Enter
            win32gui.SetForegroundWindow(d["hwnd"])
            time.sleep(0.2)
            _send_enter_key()
            dismissed.append({
                "title": d["title"],
                "texts": texts,
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
        # win32 first: EnumWindows/GetWindowText answer in ~1ms even while ExtendSim is
        # busy inside a long ModL call or a simulation run. UI Automation blocks until
        # ExtendSim is free (measured live 2026-09-25), so it is only ever called once
        # win32 has confirmed a box is actually open.
        qt_dialogs, main_hwnd = _find_extendsim_dialog_windows()
        pid = _window_pid(main_hwnd) if main_hwnd is not None else None
        win32_error_dialogs = _find_win32_error_dialogs(pid) if pid is not None else []

        if qt_dialogs or win32_error_dialogs:
            if win32_error_dialogs or any(not d.get("is_maintenance") for d in qt_dialogs):
                # Before any UIA call, which blocks while ExtendSim is busy: a pass killed
                # from here on still tells the caller that ExtendSim showed a box.
                _progress({"seen": True})
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
