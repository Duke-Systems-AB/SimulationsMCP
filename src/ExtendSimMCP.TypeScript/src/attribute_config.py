# src/attribute_config.py
"""Set-block attribute configuration over COM (ExtendSim 2024 and 2026).

The Set block keeps each row's attribute in its own static arrays - attribNamesChosen[],
attribType[] and attribSetValues[] - and AttribsTable_ttbl is only a view of them, rebuilt
when the dialog draws. So the recipe, verified live on 2026-09-26 on ExtendSim 2024 R1 and
2026.1.0.36 (a Get block downstream read the value from the items in a run):

  1. The attribute must be in the model's registry (global arrays _AttributeList and
     _attribType). If it is not, a helper Get block gets the name in its statics and is
     duplicated: the copy's `on PasteBlock` calls Attrib_AddName inside ExtendSim. Both
     helper blocks are removed. NEVER resize _AttributeList from COM - GAResizeByIndex on
     it crashed ExtendSim 2024 twice (access violation).
  2. SetDialogVariable writes the Set block's statics; each is read back as a string and a
     write that did not take returns ATTRIBUTE_WRITE_REJECTED, never a false success.

Pure core: ExtendSim is reached only through a `_Com` object (injected in tests) and the
backend's model/block checks. Only value attributes in row 0 are supported: the row count
changes only through an interactive prompt, and writing a missing row risks a modal.
"""
import re

# AttribsTable_ttbl view columns (Set block ModL source, 2024 and 2026): name 0, value 1,
# type 2, then table/field/record 3..5. Kept for readers of the view (pattern mining).
ATTR_NAME_COL = 0
ATTR_VALUE_COL = 1

ATTRIB_TYPE_VALUE = 1   # ExtendSim Includes/Constants.h

# ExtendSim's name prompt: "limited to 15 characters with no spaces". Names starting with
# "_" are ExtendSim's own (_Item quantity, _Batch size ...), "None" means no attribute, and a
# double quote would end the ModL string literal.
_NAME_OK = re.compile(r'^[^\s"_][^\s"]{0,14}$')
_RESERVED = {"none", "new attribute"}


# Local copy of simulation_backend._error (also duplicated in
# resource_pool_config.py and dialog_table.py) — deliberate: keeps this
# zero-dep module importable without a module-level import of
# simulation_backend (win32com) or a shared helper module.
def _err(code, message, **extra):
    result = {"success": False, "errorCode": code, "error": message}
    result.update(extra)
    return result


def _num_eq(a, b):
    """True if a and b are equal as numbers (tolerant), else as strings."""
    try:
        return abs(float(a) - float(b)) < 1e-9
    except (TypeError, ValueError):
        return str(a) == str(b)


class _Com:
    """The few ExtendSim operations the recipe needs, over the backend's COM helpers."""

    def __init__(self, backend, app):
        self._b, self._app = backend, app

    def _num(self, expr):
        self._app.Execute("global0 = -1;")
        self._app.Execute(f"global0 = {expr};")
        return int(self._b.parse_float(self._app.Request("System", "global0+:0:0:0")))

    def get_static(self, bid, var, row=0):
        # GetDialogVariable returns a string: assigning it to global0 fails silently, so
        # always go through globalStr0.
        self._app.Execute('globalStr0 = "";')
        self._app.Execute(f'globalStr0 = GetDialogVariable({bid}, "{var}", {row}, 0);')
        return self._b._read_str0(self._app)

    def set_static(self, bid, var, value, row=0):
        if isinstance(value, str):
            value = f'"{self._b._escape_modl_string(value)}"'
        self._app.Execute(f'SetDialogVariable({bid}, "{var}", {value}, {row}, 0);')

    def registry(self):
        """Names in _AttributeList. Read-only; [] when the model has none yet."""
        index = self._num('GaGetIndex("_AttributeList")')
        if index < 0:
            return []
        names = []
        for r in range(self._num(f"GaGetRowsByIndex({index})")):
            self._app.Execute(f"globalStr0 = GaGetString15({index}, {r}, 0);")
            names.append(self._b._read_str0(self._app))
        return names

    def add_block(self, lib, type_):
        r = self._b.block_add(lib, type_)
        if not r.get("success") or "blockId" not in r:
            raise RuntimeError(f"could not place a {type_} block: {r.get('error')}")
        return r["blockId"]

    def duplicate(self, bid):
        return self._num(f"DuplicateBlock({bid})")

    def remove_block(self, bid):
        self._b.block_remove(bid)


def _register(com, name):
    """Register `name` as a value attribute via a duplicated helper Get block."""
    helpers = []
    try:
        get_id = com.add_block("Item.lbr", "Get")
        helpers.append(get_id)
        com.set_static(get_id, "attribNamesChosen", name)
        com.set_static(get_id, "attribType", ATTRIB_TYPE_VALUE)
        copy_id = com.duplicate(get_id)
        if copy_id > 0:
            helpers.append(copy_id)
    finally:
        for bid in reversed(helpers):
            try:
                com.remove_block(bid)
            except Exception:
                pass
    return name in com.registry()


def _name_error(name, ctx):
    if not isinstance(name, str) or not _NAME_OK.match(name) or name.lower() in _RESERVED:
        return _err("ATTRIBUTE_NAME_INVALID",
                    "attribute name must be 1-15 characters, no spaces or quotes, not "
                    "starting with '_' and not 'None'", **ctx)
    return None


def _checked(backend, block_id, block_type):
    """Model open and block of the right type, else the backend's own error dict."""
    app = backend.get_extendsim_app()
    model_check = backend._validate_model_open(app)
    if not model_check.get("success"):
        return app, model_check
    type_check = backend._validate_block_type(app, block_id, block_type)
    if not type_check.get("success"):
        return app, type_check
    return app, None


def _ensure_registered(com, name, ctx):
    """(registered_now, error) - registers `name` when the model does not have it yet."""
    try:
        registered_now = name not in com.registry()
        if registered_now and not _register(com, name):
            return registered_now, _err("ATTRIBUTE_REGISTER_FAILED",
                                        f"could not register attribute '{name}' in the model", **ctx)
        return registered_now, None
    except Exception as e:
        return False, _err("ATTRIBUTE_REGISTER_FAILED", str(e), **ctx)


def _write_verified(com, block_id, wanted, ctx):
    """Write each static and read it back; an error dict, or None when all persisted."""
    try:
        for var, v in wanted.items():
            com.set_static(block_id, var, v)
    except Exception as e:
        return _err("ATTRIBUTE_WRITE_FAILED", str(e), row=0, **ctx)
    try:
        actual = {var: com.get_static(block_id, var) for var in wanted}
    except Exception as e:
        return _err("ATTRIBUTE_READ_FAILED", str(e), row=0, **ctx)
    for var, v in wanted.items():
        ok = actual[var] == v if isinstance(v, str) else _num_eq(actual[var], v)
        if not ok:
            return _err("ATTRIBUTE_WRITE_REJECTED",
                        f"{var} write to block {block_id} row 0 did not persist",
                        row=0, variable=var, requested=str(v), actual=str(actual[var]), **ctx)
    return None


def configure_get(backend, block_id, name, com=None):
    """Point a Get block at value attribute `name` (its first row), registering the
    attribute when it is new. Same static arrays as the Set block; read back, fail closed.
    The value itself arrives only when items pass through during a run."""
    app, error = _checked(backend, block_id, "Get")
    if error:
        return error
    ctx = {"blockId": block_id, "attributeName": name}
    error = _name_error(name, ctx)
    if error:
        return error
    com = com or _Com(backend, app)
    registered_now, error = _ensure_registered(com, name, ctx)
    if error:
        return error
    error = _write_verified(com, block_id, {"attribNamesChosen": name,
                                            "attribType": ATTRIB_TYPE_VALUE}, ctx)
    if error:
        return error
    return {"success": True, "blockId": block_id, "attributeName": name, "row": 0,
            "registeredNow": registered_now}


def set_attribute(backend, block_id, name, value, value_type="constant", row=0, com=None):
    """Configure a Set block to assign constant `value` to value attribute `name`.

    Registers the attribute in the model first when it is new. Effect-verified: every
    static is read back; a write that does not persist returns ATTRIBUTE_WRITE_REJECTED.
    """
    app, error = _checked(backend, block_id, "Set")
    if error:
        return error
    ctx = {"blockId": block_id, "attributeName": name}
    if value_type != "constant":
        return _err("ATTRIBUTE_VALUETYPE_UNSUPPORTED",
                    f"value_type '{value_type}' not supported yet (only 'constant')",
                    valueType=value_type, **ctx)
    if row != 0:
        return _err("ATTRIBUTE_ROW_UNSUPPORTED",
                    "only row 0 can be set: the Set block changes its row count only "
                    "through an interactive prompt", row=row, **ctx)
    error = _name_error(name, ctx)
    if error:
        return error
    try:
        value = float(value)
    except (TypeError, ValueError):
        return _err("ATTRIBUTE_VALUE_INVALID",
                    f"value must be a number for a value attribute, got {value!r}", **ctx)
    if value.is_integer():
        value = int(value)

    com = com or _Com(backend, app)
    registered_now, error = _ensure_registered(com, name, ctx)
    if error:
        return error
    error = _write_verified(com, block_id, {"attribNamesChosen": name,
                                            "attribType": ATTRIB_TYPE_VALUE,
                                            "attribSetValues": value}, ctx)
    if error:
        return error
    return {"success": True, "blockId": block_id, "attributeName": name, "value": value,
            "valueType": value_type, "row": row, "registeredNow": registered_now}


def set_attribute_entry(block_id, name, value, value_type="constant", row=0):
    import simulation_backend as backend
    return set_attribute(backend, block_id, name, value, value_type, row)


def configure_get_entry(block_id, name):
    import simulation_backend as backend
    return configure_get(backend, block_id, name)
