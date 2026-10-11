"""Safe model handling: the decisions for model_close/model_list/model_open/model_new.

Measured live on ExtendSim 2024 and 2026, 2026-10-10:

- Closing a dirty model with ExecuteMenuCommand(4) and *without* SetDirty(False) shows a QMessageBox
  titled "Worksheet": "The model '<name>' has been modified. Do you want to save your changes?"
  (Yes/No/Cancel, Yes focused), blocking COM until answered. Cancel leaves the model open and
  unchanged; a clean model closes with no prompt. dialog_watcher answers exactly that prompt with
  Cancel and reports its text - that text is the only evidence this module has of unsaved changes in
  a *saved* model, so the "nothing given" close is a probe: close without SetDirty(False) while the
  dismisser runs, and read what it saw.
- Never-saved models: ModelSettingGet(name, 2) (the dirty flag) is exact there (SaveModel() on a
  never-saved model opens the native "Save Model As" dialog and must never be called - SaveModelAs
  works). For a *saved* model the flag is unreadable (-1), so dirty is unknown there - only the probe
  can tell.
- A block dialog in front of the model takes the first close (model stays open, no prompt); the
  model's own retry count (CLOSE_ATTEMPTS) absorbs that.
- model_open of a model that is open but not active pops a single-OK "Already Open" box; answering it
  brings that model forward - but (measured live on 2024, 2026-10-10) the model that was
  active disappears from the window list while it is still open (GetModelPath still answers), and
  the next close of the model brought forward closes it too: silently when it is clean, and with a
  save prompt that Cancel does not end (it reappears at once, blocking COM) when it has changes. So
  open_model refuses that case instead of activating; ExtendSim's own window switch is the user's.
- Open model windows are listed (by UI Automation, elsewhere) as EMdiSubWindow elements named after
  the model.

Pure: COM is reached only through an injected `ops` object (duck-typed; see the plan's "Interfaces"
section for the full contract). This module never imports simulation_backend, win32com or comtypes.
"""
import os
import threading

CLOSE_ATTEMPTS = 3

# How long the open-model window read (UI Automation, see BackendOps.window_names) may take before
# the caller goes on without it, as after a failed read. Measured 2026-10-10 on 2024: 8.1-8.3 s
# during a running simulation (UI Automation waits for ExtendSim's busy UI thread - see
# extendsim_probe.check), well under a second when idle.
WINDOW_READ_DEADLINE_SEC = 5

_CLOSE_SUGGESTION = "close any open dialogs of the model in ExtendSim, then try again"


def _err(code, message, **extra):
    out = {"success": False, "errorCode": code, "error": message}
    out.update(extra)
    return out


def is_save_prompt(text):
    """The "Worksheet" save prompt's text (see the module docstring); any other box is not evidence
    of unsaved changes."""
    return isinstance(text, str) and text.startswith("The model '") and "has been modified" in text


def _file_name(path):
    return (path or "").replace("\\", "/").rsplit("/", 1)[-1]


def _norm_model_path(path):
    """One spelling per file, for comparing model paths only - copies simulation_backend's helper of
    the same name (PR #101): absolute, "." / ".." and doubled or mixed separators resolved (2024
    reports forward slashes), case folded (Windows)."""
    return os.path.normcase(os.path.normpath(os.path.abspath(path.replace("/", os.sep))))


def _same_name_conflict(file_path, active_name, active_folder):
    """None when file_path IS the model named active_name (same full path, or a bare file name with
    no folder - which can only mean that model itself); otherwise why file_path conflicts with it.

    Copies simulation_backend._same_name_conflict (PR #101): a never-saved model has no folder
    (active_folder == ""), so any non-bare path naming it the same is a conflict."""
    if "/" not in file_path.replace("\\", "/"):
        return None
    if not active_folder:
        return (f"a model named {active_name} is already open and has never been saved; ExtendSim "
                "cannot hold two models with the same name - close it first")
    if _norm_model_path(os.path.join(active_folder, active_name)) == _norm_model_path(file_path):
        return None
    return (f"a model named {active_name} is already open from {active_folder}; ExtendSim cannot "
            "hold two models with the same name - close it first")


def _bad_file_path(path):
    """None when `path` is usable as model_close's filePath; otherwise why not."""
    if not os.path.isabs(path):
        return f"filePath must be an absolute path ('{path}' is not)"
    if not path.lower().endswith(".mox"):
        return f"filePath must end in .mox ('{path}' does not)"
    return None


def _close_loop(ops, name, before_each=None, after_save=False):
    """Repeat close_window() (a block dialog of the model may take the first one or more) until the
    model is no longer active, or CLOSE_ATTEMPTS is used up. Returns the attempt count on success, or
    an error dict (MODEL_CLOSE_FAILED, or MODEL_SAVE_FAILED - see below).

    after_save: the close follows a save that cannot be read back (SaveModel answers nothing). If
    that save silently did not take, the close raises the "Worksheet" save prompt, which blocks COM
    with nothing in Python to answer it - so each attempt runs under the dismisser (it answers that
    prompt with Cancel: the model stays open), and a save prompt seen means the save failed."""
    attempts = 0
    while True:
        attempts += 1
        if before_each is not None:
            before_each()
        if after_save:
            handle = ops.start_dismisser()
            try:
                ops.close_window()
            finally:
                texts = ops.stop_dismisser(handle)
            if any(is_save_prompt(t) for t in texts):
                return _err("MODEL_SAVE_FAILED",
                            f"the save did not take; the model is still open (closing '{name}' "
                            "asked to save its changes)",
                            dialogs=texts)
        else:
            ops.close_window()
        if ops.active_name() != name:
            return attempts
        if attempts >= CLOSE_ATTEMPTS:
            return _err("MODEL_CLOSE_FAILED", f"'{name}' is still open after {attempts} close attempts",
                        suggestion=_CLOSE_SUGGESTION)


def _finish_close(ops, name, extra, before_each=None, after_save=False):
    attempts = _close_loop(ops, name, before_each, after_save)
    if isinstance(attempts, dict):
        return attempts
    result = {"success": True, "closed": True}
    result.update(extra)
    if attempts > 1:
        result["closeAttempts"] = attempts
    return result


def _probe_close(ops, name):
    """Close a saved model with no saveFirst/discard: the only way to learn whether it has unsaved
    changes. Each attempt gets its own dismisser pass, so that a dismissed save prompt - proof the
    model is dirty - stops the probe immediately instead of repeating the same prompt up to
    CLOSE_ATTEMPTS times (closing, cancelling and reopening it changes nothing)."""
    all_texts = []
    attempts = 0
    closed = False
    while attempts < CLOSE_ATTEMPTS:
        attempts += 1
        handle = ops.start_dismisser()
        try:
            ops.close_window()
        finally:
            all_texts.extend(ops.stop_dismisser(handle))
        if ops.active_name() != name:
            closed = True
            break
        if any(is_save_prompt(t) and name in t for t in all_texts):
            break

    save_prompts = [t for t in all_texts if is_save_prompt(t) and name in t]
    if save_prompts:
        return _err("MODEL_HAS_UNSAVED_CHANGES",
                    "the model has unsaved changes; close it with saveFirst=true or discard=true",
                    dialogs=all_texts)
    if closed:
        result = {"success": True, "closed": True, "saved": False, "hadUnsavedChanges": False}
        if attempts > 1:
            result["closeAttempts"] = attempts
        if all_texts:
            result["dialogs"] = all_texts
        return result
    return _err("MODEL_CLOSE_FAILED", f"'{name}' is still open after {attempts} close attempts",
                suggestion=_CLOSE_SUGGESTION)


def close_model(ops, spec):
    """model_close's decision: refuse a changed model unless saveFirst, discard or (never-saved
    model) filePath says what to do with the change. See the module and plan docstrings for the
    branches; `spec` carries saveFirst, discard, filePath exactly as the tool's parameters."""
    save_first = bool(spec.get("saveFirst"))
    discard = bool(spec.get("discard"))
    file_path = spec.get("filePath") or None

    if save_first and discard:
        return _err("INVALID_PARAMETER", "saveFirst and discard cannot both be given")
    if file_path:
        problem = _bad_file_path(file_path)
        if problem:
            return _err("INVALID_PARAMETER", problem)

    name = ops.active_name()
    if not name:
        return _err("MODEL_NOT_OPEN", "no model is open, so there is nothing to close")

    never_saved, dirty = ops.unsaved_state()

    if never_saved:
        if dirty is False:
            ops.set_clean()
            return _finish_close(ops, name, {"saved": False, "hadUnsavedChanges": False})
        # dirty is True, or None (ExtendSim could not tell) - fail closed: either way, treat as dirty.
        if file_path:
            # Never an overwrite: SaveModelAs would replace the file
            # without asking, and model_close has no overwrite flag.
            if os.path.exists(file_path):
                return _err("INVALID_PARAMETER", f"{file_path} already exists; choose a new file name",
                            filePath=file_path)
            ops.save_as(file_path)
            if not ops.active_path():
                return _err("MODEL_SAVE_FAILED",
                            f"SaveModelAs('{file_path}') did not give the model a folder - the save "
                            "did not take",
                            filePath=file_path)
            # The model is now named after its file: the close is verified against that name, not
            # the old "Model-1" (which is gone whether or not the close took).
            name = ops.active_name()
            if not name:
                return _err("MODEL_SAVE_FAILED",
                            f"after SaveModelAs('{file_path}') no model is active - the save cannot "
                            "be confirmed",
                            filePath=file_path)
            return _finish_close(ops, name, {"saved": True, "filePath": file_path}, after_save=True)
        if discard:
            ops.set_clean()
            return _finish_close(ops, name, {"saved": False, "discardedChanges": True})
        if save_first:
            return _err("MODEL_SAVE_FAILED",
                        "this model has never been saved, so it has no file to save to",
                        suggestion="pass filePath so model_close can save it before closing")
        return _err("MODEL_HAS_UNSAVED_CHANGES",
                    "this model has never been saved and has unsaved changes; pass filePath to save "
                    "it first, discard=true to close without saving, or save it yourself before "
                    "closing")

    # A saved model. A filePath is for a never-saved model only (spec 3.1): here it is ignored -
    # never a silent Save As somewhere else - and the answer says so.
    result = _close_saved(ops, name, save_first, discard)
    if file_path:
        _add_note(result, f"filePath ('{file_path}') was ignored: it is only used for a model that "
                          "has never been saved, and this one has a file")
    return result


def _close_saved(ops, name, save_first, discard):
    if save_first:
        ops.save()
        return _finish_close(ops, name, {"saved": True}, after_save=True)
    if discard:
        return _finish_close(ops, name,
                             {"saved": False, "discardedChanges": "unknown",
                              "note": "ExtendSim cannot tell whether this saved model had unsaved "
                                      "changes before it was closed"},
                             before_each=ops.set_clean)
    return _probe_close(ops, name)


def _add_note(result, note):
    result["note"] = f"{result['note']}; {note}" if result.get("note") else note


def list_models(ops):
    """model_list's decision: every open model when window_names() can be read, else today's answer
    (the active model only) with a warning."""
    active = ops.active_name()
    try:
        names = _read_window_names(ops)
    except Exception:
        names = None          # the read failed or timed out - unlike an empty list, a true answer

    if names is None:
        result = list_models(_NoWindows(ops))
        result["warning"] = "only the active model could be listed"
        return result

    models = []
    counter = 1
    if active:
        entry = {"modelId": "model_1", "name": active, "active": True}
        path = ops.active_path()
        if path:
            entry["path"] = path
        models.append(entry)
        counter = 2
    for n in names:
        if active and n == active:
            continue
        models.append({"modelId": f"model_{counter}", "name": n, "active": False})
        counter += 1
    return {"success": True, "count": len(models), "models": models}


class _NoWindows:
    """ops whose window list reads as empty: list_models' answer when the real read failed."""

    def __init__(self, ops):
        self._ops = ops

    def active_name(self):
        return self._ops.active_name()

    def active_path(self):
        return self._ops.active_path()

    def window_names(self):
        return []


def open_model(ops, path):
    """model_open's decision: already-open-and-active, already-open-but-inactive (refused, nothing
    opened - see the module docstring), or open as today."""
    active = ops.active_name()
    requested_name = _file_name(path)

    if active and active.lower() == requested_name.lower():
        conflict = _same_name_conflict(path, active, ops.active_path())
        if conflict:
            return _err("MODEL_OPEN_FAILED", conflict, filePath=path)
        return {"success": True, "alreadyOpen": True, "name": active}

    names_failed = False
    try:
        names = _read_window_names(ops)
    except Exception:
        names, names_failed = [], True

    match = None
    if not names_failed:
        for n in names:
            if n.lower() == requested_name.lower():
                match = n
                break

    if match is not None:
        # Never OpenExtendFile here: see the module docstring ("Already Open" hides the active
        # model and couples it to the one brought forward). Refuse and change nothing.
        return _err("MODEL_OPEN_FAILED",
                    f"{match} is already open in ExtendSim, but {active or 'another model'} is the "
                    "active model; model_open does not switch between open models",
                    filePath=path, alreadyOpen=True, activeModel=active,
                    suggestion=f"bring {match} to the front in ExtendSim (click its window), or close "
                               f"{active or 'the active model'} first with model_close")

    ops.open_file(path)
    # OpenExtendFile answers nothing a caller can trust: on a path that does not exist it fails
    # silently and the model that was active stays active. Reporting *that* model as opened let
    # extract_psg read it and then close it with discard=True (final review, finding 1) - so the
    # open counts only when the requested model is the active one now.
    opened = ops.active_name()
    if not opened or opened.lower() != requested_name.lower():
        return _err("MODEL_OPEN_FAILED",
                    f"ExtendSim did not open {path}; the active model is "
                    f"{opened or 'none (no model is open)'}",
                    filePath=path, activeModel=opened or "")
    folder = ops.active_path()
    requested_folder = os.path.dirname(path.replace("/", os.sep))
    if (folder and os.path.isabs(requested_folder)
            and _norm_model_path(folder) != _norm_model_path(requested_folder)):
        return _err("MODEL_OPEN_FAILED",
                    f"ExtendSim did not open {path}; the active model {opened} is from {folder}",
                    filePath=path, activeModel=opened)
    result = {"success": True, "alreadyOpen": False, "name": opened}
    if names_failed:
        result["warning"] = ("could not check whether this model was already open under another "
                             "window before opening it")
    return result


def new_model_conflict(ops, save_path):
    """model_new's decision: refuse a savePath that names a model already open (under any name it is
    open under, active or not), else None."""
    if not save_path:
        return None
    requested_name = _file_name(save_path)
    active = ops.active_name()
    if active and active.lower() == requested_name.lower():
        return _err("INVALID_PARAMETER", f"a model named {active} is already open; close it first")
    try:
        names = _read_window_names(ops)
    except Exception:
        return None
    for n in names:
        if n.lower() == requested_name.lower():
            return _err("INVALID_PARAMETER", f"a model named {n} is already open; close it first")
    return None


# ---------------------------------------------------------------------------
# Open model windows, from UI Automation
# ---------------------------------------------------------------------------

def _read_window_names(ops):
    """ops.window_names(), bounded by WINDOW_READ_DEADLINE_SEC: it runs on a daemon thread, and a
    read still going at the deadline raises TimeoutError (callers treat it as a failed read). The
    thread is left to finish on its own - it is read-only UI Automation and holds no lock of ours."""
    box = {}

    def run():
        try:
            box["names"] = ops.window_names()
        except BaseException as e:      # handed to the caller below
            box["error"] = e

    worker = threading.Thread(target=run, name="model-window-read", daemon=True)
    worker.start()
    worker.join(WINDOW_READ_DEADLINE_SEC)
    if worker.is_alive():
        raise TimeoutError(f"reading the open model windows took over {WINDOW_READ_DEADLINE_SEC} s")
    if "error" in box:
        raise box["error"]
    return box["names"]


_UIA_WINDOW = 50032          # UIA_WindowControlTypeId
_MDI_CLASS = "EMdiSubWindow"


def model_window_names(elements):
    """The open models' names from (class name, control type, name) triples of the main window's
    UI Automation descendants. Measured live 2026-10-10 on 2024: the Window-type descendants are the
    library dock widgets (EDockWidget, named after the library: "Item", "Value", ...) and one
    EMdiSubWindow per open model, named after it ("Model-1.mox"). Only names ending in .mox count, so
    a library or other MDI window is never listed as a model; order kept, duplicates dropped.
    EMdiSubWindow elements are preferred: when any is found, another Window element merely named
    *.mox (a floating window titled after the model, say) is not taken for a model; only when none
    is found do Window-type elements named *.mox count (a build whose model windows have another
    class)."""
    elements = list(elements)

    def pick(match):
        names = []
        for class_name, control_type, name in elements:
            name = (name or "").strip()
            if name.lower().endswith(".mox") and match(class_name, control_type) and name not in names:
                names.append(name)
        return names

    return (pick(lambda cls, _ct: cls == _MDI_CLASS)
            or pick(lambda _cls, ct: ct == _UIA_WINDOW))


# ---------------------------------------------------------------------------
# The ops contract over simulation_backend's COM helpers
# ---------------------------------------------------------------------------

class BackendOps:
    """The ops object the decisions above run on, over simulation_backend's helpers (`backend` is
    that module, injected - never imported here) and the ExtendSim COM `app`."""

    _PROBE_POLL_SEC = 0.5     # the save prompt appears in under 0.5 s (measured)

    def __init__(self, backend, app):
        self._b, self._app = backend, app

    def _modl_path(self, path):
        return self._b._escape_modl_string(path.replace("\\", "/"))

    def active_name(self):
        return self._b._active_model_name(self._app)

    def active_path(self):
        """The active model's folder (GetModelPath gives no trailing separator), "" for a model
        that was never saved or when none is open."""
        name = self.active_name()
        if not name:
            return ""
        self._app.Execute(f'globalStr0 = GetModelPath("{self._b._escape_modl_string(name)}");')
        return self._b._read_str0(self._app)

    def unsaved_state(self):
        return self._b._model_unsaved_state(self._app)

    def set_clean(self):
        self._app.Execute("SetDirty(False);")

    def close_window(self):
        self._app.Execute("ExecuteMenuCommand(4);")      # closes the active window

    def save(self):
        self._app.Execute("SaveModel();")

    def save_as(self, path):
        self._app.Execute(f'SaveModelAs("{self._modl_path(path)}");')

    def open_file(self, path):
        self._app.Execute(f'OpenExtendFile("{self._modl_path(path)}");')

    def start_dismisser(self):
        # The close it covers is a blocking ExecuteMenuCommand with no bound of its own, so the
        # watcher's dead-man's switch is the backend's safety cap (see _start_dialog_auto_dismisser);
        # stop_dismisser ends it right after the close returns.
        return self._b._start_dialog_auto_dismisser(duration_sec=self._b._DISMISSER_SAFETY_CAP_SEC,
                                                    poll_sec=self._PROBE_POLL_SEC)

    def stop_dismisser(self, handle):
        return self._b._dismisser_texts(handle)

    def window_names(self):
        """Open model names via UI Automation on ExtendSim's own main window, read-only. Raises on
        any failure (list_models then falls back to the active model only). It runs on the worker
        thread of _read_window_names, so it initialises COM for that thread itself."""
        import dialog_watcher as dw
        if not dw._uia_available:
            raise RuntimeError("UI Automation is not available")
        dw.comtypes.CoInitialize()
        try:
            return self._uia_model_names(dw)
        finally:
            dw.comtypes.CoUninitialize()

    @staticmethod
    def _uia_model_names(dw):
        _, main = dw._find_extendsim_dialog_windows()
        if not main:
            raise RuntimeError("ExtendSim's main window was not found")
        uia = dw._get_uia()
        c = dw.UIAutomationClient
        cond = uia.CreateOrCondition(
            uia.CreatePropertyCondition(c.UIA_ClassNamePropertyId, _MDI_CLASS),
            uia.CreatePropertyCondition(c.UIA_ControlTypePropertyId, _UIA_WINDOW))
        found = uia.ElementFromHandle(main).FindAll(c.TreeScope_Descendants, cond)
        elements = []
        for i in range(found.Length):
            e = found.GetElement(i)
            elements.append((e.CurrentClassName, e.CurrentControlType, e.CurrentName))
        return model_window_names(elements)
