"""Answers ExtendSim's "User Parameter" prompts.

Adding a Scenario Manager factor/response or an Optimizer parameter the documented Shift-click way makes
ExtendSim ask for a name in a modal Qt window titled "User Parameter" (manual scenarios: the number of
scenarios); the COM call returns only after it is answered. PromptAnswerer answers such windows ONLY in
ExtendSim's own process and ONLY with texts queued by the caller, in order. A prompt with nothing queued
is left alone and reported. From the deadline on, every open prompt is cancelled - also one that opens
later - so a hanging COM call returns and the caller's read-back fails closed; the polling thread keeps
doing that until stop(). A stopped answerer never presses a button: a thread that was still inside a UI
Automation call when stop() returned must not touch a prompt that belongs to the next call. The UI is an
injected object (WindowsUi below).
"""
import threading
import time

TITLE = "User Parameter"


class PromptAnswerer:
    def __init__(self, ui, timeout_s, poll_s=0.3, clock=time.monotonic, sleep=time.sleep):
        self._ui, self._poll, self._clock, self._sleep = ui, poll_s, clock, sleep
        self._deadline = clock() + timeout_s
        self._queue = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self.answered, self.unexpected, self.cancelled, self.errors = [], [], [], []
        bind = getattr(ui, "bind_stop", None)
        if bind is not None:
            bind(self._stop.is_set)          # lets the UI refuse a press that comes after stop()

    def expect(self, text):
        with self._lock:
            self._queue.append(str(text))

    def pending(self):
        with self._lock:
            return len(self._queue)

    def past_deadline(self):
        """True once prompts are being cancelled instead of answered."""
        return self._clock() > self._deadline

    def step(self):
        # find_prompts() itself can raise (e.g. EnumWindows failing transiently); that must not
        # kill the loop, or a prompt could go unanswered and uncancelled forever.
        try:
            hwnds = self._ui.find_prompts()
        except Exception as e:
            self.errors.append(str(e))
            return
        for hwnd in hwnds:
            # A failure reading or cancelling THIS window must not stop the others from being
            # handled - one stuck window is exactly the case the deadline/cancel exists for.
            try:
                label = self._ui.read_label(hwnd)
            except Exception as e:
                self.errors.append(str(e))
                continue
            if self._stop.is_set():
                return                       # stopped while reading: press nothing
            if self.past_deadline():
                try:
                    self._ui.cancel(hwnd)
                except Exception as e:
                    self.errors.append(str(e))
                    continue
                self.cancelled.append(label)
                continue
            with self._lock:
                text = self._queue[0] if self._queue else None
            if text is None:
                if label not in self.unexpected:
                    self.unexpected.append(label)
                continue
            if self._stop.is_set():
                return
            try:
                self._ui.answer(hwnd, text)
            except Exception:
                continue                     # keep it queued; try again on the next poll
            with self._lock:
                self._queue.pop(0)
            self.answered.append({"prompt": label, "answer": text})

    def run(self):
        while not self._stop.is_set():
            try:
                self.step()
            except Exception as e:
                # step() already guards find_prompts()/read_label()/cancel() internally; this is
                # a backstop so nothing unanticipated can ever kill the polling thread.
                self.errors.append(str(e))
            # No exit at the deadline: a prompt may still open afterwards (a late Shift-click), and
            # only this thread can cancel it. The caller's finally always calls stop().
            self._sleep(self._poll)

    def start(self):
        def target():
            try:
                import comtypes
                comtypes.CoInitialize()
            except Exception:
                pass
            self.run()
        self._thread = threading.Thread(target=target, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)


def extendsim_pid():
    """Process id of the running ExtendSim (its main window), or None."""
    import dialog_watcher
    import win32process
    _, main = dialog_watcher._find_extendsim_dialog_windows()
    return win32process.GetWindowThreadProcessId(main)[1] if main else None


class WindowsUi:
    """The ui contract over win32gui (finding the windows) and UI Automation (reading/answering)."""

    EDIT, TEXT, BUTTON = 50004, 50020, 50000          # UIA control type ids

    def __init__(self, pid):
        self._pid = pid
        self._stopped = lambda: False

    def bind_stop(self, stopped):
        self._stopped = stopped

    def find_prompts(self):
        import win32gui
        import win32process
        found = []
        win32gui.EnumWindows(lambda h, f: f.append(h) if win32gui.IsWindowVisible(h)
                             and win32gui.GetWindowText(h) == TITLE
                             and win32process.GetWindowThreadProcessId(h)[1] == self._pid else None, found)
        return found

    def _elements(self, hwnd):
        import dialog_watcher as dw
        uia = dw._get_uia()
        box = uia.ElementFromHandle(hwnd)
        found = box.FindAll(dw.UIAutomationClient.TreeScope_Descendants, uia.CreateTrueCondition())
        return dw, [found.GetElement(i) for i in range(found.Length)]

    def read_label(self, hwnd):
        _, elems = self._elements(hwnd)
        return next((e.CurrentName for e in elems if e.CurrentControlType == self.TEXT and e.CurrentName), "")

    def _press(self, dw, elems, name):
        if self._stopped():
            raise RuntimeError("the prompt answerer was stopped; nothing pressed")
        btn = next(e for e in elems if e.CurrentControlType == self.BUTTON and e.CurrentName == name)
        pat = btn.GetCurrentPattern(dw.UIAutomationClient.UIA_InvokePatternId)
        pat.QueryInterface(dw.UIAutomationClient.IUIAutomationInvokePattern).Invoke()

    def answer(self, hwnd, text):
        dw, elems = self._elements(hwnd)
        if self._stopped():
            raise RuntimeError("the prompt answerer was stopped; nothing typed")
        edit = next(e for e in elems if e.CurrentControlType == self.EDIT)
        pat = edit.GetCurrentPattern(dw.UIAutomationClient.UIA_ValuePatternId)
        pat.QueryInterface(dw.UIAutomationClient.IUIAutomationValuePattern).SetValue(text)
        self._press(dw, elems, "OK")

    def cancel(self, hwnd):
        dw, elems = self._elements(hwnd)
        self._press(dw, elems, "Cancel")
