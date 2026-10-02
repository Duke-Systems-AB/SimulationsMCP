# src/block_com_profile.py
"""Reading an ExtendSim block through COM only - never from its library file.

Measured on ExtendSim 2024/2026 (2026-09-30, 2026-10-02):
- DIGetName(block, id) names the dialog items, id 0 upwards;
- GetDialogItemInfo(block, name, which): 4 type, 11 tab, 1 hidden, 2 enabled, 3 display only,
  5 rows, 6 columns - on a name the block does not have it raises a MODAL box, so it is only
  asked about names DIGetName returned;
- DITitleGet(block, name): a popup's options joined by ';' (empty for dynamic list popups), a
  button's or checkbox's label, a label's text.
A ModL string holds at most 255 characters, so a title or text value of that length may be cut;
it is flagged (optionsTruncated / defaultTruncated) instead of presented as complete.
The block is placed in a temporary model of our own, which is the only model ever closed.
"""
import os
import re

KINDS = {1: "button", 2: "checkbox", 3: "radio", 4: "meter", 5: "parameter", 6: "slider", 7: "table",
         8: "edittext", 9: "static_text", 12: "switch", 13: "text_table", 14: "plot", 16: "popup",
         18: "dtxt", 19: "frame", 20: "calendar", 21: "edittext"}
SCALAR_KINDS = {"parameter", "popup", "checkbox", "radio", "edittext", "switch", "slider"}
NOT_SETTING_KINDS = {"static_text", "frame", "meter", "plot"}
_NOT_SETTING_NAMES = {"ok_btn", "cancel_btn", "ok", "cancel"}
_MODL_STRING_MAX = 255   # a ModL string value never holds more (simulation_backend._MODL_STRING_MAX)
_UNSET = -12345          # written to global0 before a numeric read; still there => the read did not happen
_STEM = re.compile(r"(\d+)?_(lbl|pop|prm|chk|cbx|rbt|btn|txt|dtbl|ttbl|dtxt)$", re.I)


def _err(code, message, **extra):
    r = {"success": False, "errorCode": code, "error": message}
    r.update(extra)
    return r


class Com:
    """The COM calls block_profile needs, over the backend's helpers."""

    def __init__(self, backend, app):
        self._b, self._app = backend, app

    def _s(self, expr):
        self._app.Execute('globalStr0 = "";')
        self._app.Execute(f"globalStr0 = {expr};")
        return self._b._read_str0(self._app)

    def _n(self, expr):
        self._app.Execute(f"global0 = {_UNSET};")
        self._app.Execute(f"global0 = {expr};")
        v = self._b.parse_float(self._app.Request("System", "global0+:0:0:0"))
        if v == _UNSET:
            raise RuntimeError(f"ExtendSim did not answer {expr}")
        return v

    def model_name(self):
        return self._s("GetModelName()")

    def new_model(self, path):
        r = self._b.model_new(path)
        if not r.get("success"):
            raise RuntimeError(r.get("error") or "could not create the temporary model")

    def add_block(self, library, block):
        r = self._b.block_add(library, block, x=100, y=100)
        if not r.get("success"):
            raise RuntimeError(r.get("error") or "block_add failed")
        return r["blockId"]

    def close_model(self):
        return self._b.model_close(None, False)

    def open_library(self, path):
        """Ask ExtendSim to open a library (File > Open; ExtendSim reads it, not us). ModL strings
        have no escapes and a 255-character cap, so the path goes with forward slashes and a
        path ModL cannot carry is refused. Returns OpenExtendFile's result: 0 = opened."""
        path = path.replace("\\", "/")
        if '"' in path or len(path) > _MODL_STRING_MAX:
            raise ValueError("the library path cannot be passed to ExtendSim (a quote, or longer than 255 characters)")
        return int(self._n(f'OpenExtendFile("{path}")'))

    def dialog_name(self, bid, did):
        return self._s(f"DIGetName({bid}, {did})")

    def item_info(self, bid, name, which):
        return self._n(f'GetDialogItemInfo({bid}, "{self._b._escape_modl_string(name)}", {which})')

    def title(self, bid, name):
        return self._s(f'DITitleGet({bid}, "{self._b._escape_modl_string(name)}")')

    def value(self, bid, name):
        return self._s(f'GetDialogVariable({bid}, "{self._b._escape_modl_string(name)}", 0, 0)')

    def library_dir(self, bid):
        return self._s(f"GetLibraryPathName({bid}, 1)")


def enumerate_names(com, bid, max_id=1000, stop_after=50):
    names, empty = [], 0
    for did in range(max_id):
        nm = (com.dialog_name(bid, did) or "").strip()
        if not nm:
            empty += 1
            if empty >= stop_after:
                break
            continue
        empty = 0
        names.append(nm)
    return names


def _stem(name):
    return _STEM.sub("", name).lower()


def pair_labels(items):
    labels = {}
    for it in items:
        if it["kind"] == "static_text" and it.get("title"):
            labels.setdefault(_stem(it["name"]), it["title"])
    for it in items:
        if it["setting"] and _stem(it["name"]) in labels:
            it["label"] = labels[_stem(it["name"])]


def describe(com, bid, names):
    items = []
    for nm in names:
        kind = KINDS.get(int(com.item_info(bid, nm, 4)), "other")
        it = {"name": nm, "kind": kind, "tab": int(com.item_info(bid, nm, 11)),
              "hidden": bool(com.item_info(bid, nm, 1)), "enabled": bool(com.item_info(bid, nm, 2)),
              "displayOnly": bool(com.item_info(bid, nm, 3))}
        title = com.title(bid, nm)
        if kind == "popup":
            segments = title.split(";") if title else []
            if len(title or "") >= _MODL_STRING_MAX:
                segments = segments[:-1]        # the last option may be cut part-way
                it["optionsTruncated"] = True
            opts = [o.strip() for o in segments if o.strip()]
            it["options"] = opts
            if not opts and not it.get("optionsTruncated"):
                it["dynamicOptions"] = True
        elif title:
            it["title"] = title
        if kind in ("table", "text_table"):
            it["rows"], it["columns"] = int(com.item_info(bid, nm, 5)), int(com.item_info(bid, nm, 6))
        if kind in SCALAR_KINDS:
            it["default"] = com.value(bid, nm)
            if len(it["default"] or "") >= _MODL_STRING_MAX:
                it["defaultTruncated"] = True
        it["setting"] = kind not in NOT_SETTING_KINDS and nm.lower() not in _NOT_SETTING_NAMES
        items.append(it)
    pair_labels(items)
    return items


def _key(name):
    base = os.path.basename((name or "").replace("\\", "/")).strip().lower()
    return base[:-4] if base.endswith(".mox") else base


def _close_own(com, own_name):
    """Close only our temporary model, known by the name ExtendSim gave it right after File >
    New (an untitled "Model-N" when the save did not take is still ours). Checks the name
    before closing (refusing if another model is in front), closes once, and relies on the
    backend's own model_close to verify by name and retry (_CLOSE_ATTEMPTS) - never a second
    closing loop on top of it."""
    own = _key(own_name)
    if _key(com.model_name()) != own:
        return f"close refused: active model is {com.model_name()!r}, not the temporary model"
    r = com.close_model()
    if not r.get("success"):
        return f"close failed: {r.get('error') or 'unknown error'}"
    if _key(com.model_name()) == own:
        return "close failed: temporary model still open"
    return None


def _new_own_model(com, temp_path):
    """File > New + save. Returns (name of the new model or None, error result or None).
    The active model name is read before and after: a model that came to the front is ours
    even when creating it reported a failure (File > New worked, SaveModelAs did not)."""
    before = com.model_name()
    failure = None
    try:
        com.new_model(temp_path)
    except Exception as e:
        failure = _err("BLOCK_PROFILE_FAILED", f"ExtendSim could not create the temporary model ({e})")
    after = com.model_name()
    own = after if after and _key(after) != _key(before) else None
    if failure is None and own is None:
        failure = _err("BLOCK_PROFILE_FAILED", "ExtendSim did not open the temporary model; nothing was placed")
    return own, failure


def _place(com, library, block, library_path):
    """Place the block; when that fails and the library's full path is known, ask ExtendSim to
    open the library and try once more. Returns (block id or None, opened, error result or None)."""
    try:
        return com.add_block(library, block), False, None
    except RuntimeError as e:
        first = e
    if not library_path:
        return None, False, _err("BLOCK_ADD_FAILED",
                                 f"ExtendSim could not place '{block}' from '{library}' ({first}). Check the block "
                                 "name; for a library outside ExtendSim's Libraries folder give its full path "
                                 "(it is then opened in ExtendSim) or open it in ExtendSim first.")
    try:
        opened = com.open_library(library_path)
    except Exception as e:
        return None, False, _err("BLOCK_ADD_FAILED", f"ExtendSim could not open '{library_path}' ({e})")
    if opened != 0:
        return None, False, _err("BLOCK_ADD_FAILED",
                                 f"ExtendSim could not open '{library_path}' (OpenExtendFile returned {opened})")
    try:
        return com.add_block(library, block), True, None
    except RuntimeError as e:
        return None, True, _err("BLOCK_ADD_FAILED",
                                f"'{library}' is open in ExtendSim but '{block}' could not be placed from it ({e}). "
                                "Check the block name (as shown in ExtendSim's library window).",
                                libraryOpened=True)


def profile_via_com(com, library, block, temp_path, library_path=None):
    own, result = None, None
    try:
        own, result = _new_own_model(com, temp_path)
        if result is None:
            bid, opened, result = _place(com, library, block, library_path)
            if result is None:
                try:
                    names = enumerate_names(com, bid)
                    result = {"success": True, "block": block, "library": library,
                              "libraryDir": com.library_dir(bid), "items": describe(com, bid, names)}
                    if opened:
                        result["libraryOpened"] = True
                except Exception as e:
                    result = _err("BLOCK_PROFILE_FAILED",
                                  f"reading '{block}' through ExtendSim failed: {e}")
    finally:
        problem = _close_own(com, own) if own else None
    if problem:
        result = dict(result or {}, closeProblem=problem)
    elif own:
        try:
            os.remove(temp_path)
        except OSError:
            pass
    return result
