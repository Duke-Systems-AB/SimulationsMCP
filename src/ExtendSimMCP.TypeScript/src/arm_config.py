"""Advanced Resource Management (ARM) through COM - resources_configure (spec 2026-10-04).

ExtendSim's Resource Manager builds resources and requirements from two tables of the Advanced
Resources database when its Auto-Generate Components button is pressed - and only with the block's
dialog open (without it the filters get no property source and nothing is ever allocated; measured
on ExtendSim 2026). This module validates the AI's description, builds the table rows, runs the
sequence through an injected `ops` object and verifies what ExtendSim generated. No COM import here;
BackendOps (below) adapts simulation_backend.
"""

DB = "Advanced Resources"
CLASSES = "Resource Classes"
EXPRESSIONS = "Resource Requirement Expressions"
REQUIRED_CLASS_FIELDS = ("Name", "Pool", "Number of Distinct Resource Instances",
                         "Maximum Quantity per Resource Instance", "Minimum Allocation Quantity")
EXPRESSION_FIELDS = ("Resource Requirement ID", "Property", "Operator", "Value",
                     "Relationship with Next Row", "Expression Group ID")
OPS = ("=", ">", ">=", "<", "<=", "!=", "In", "BitAND")
# Only these fields of the generated tables are read after the press: the whole post-press tail
# has to finish inside the TypeScript layer's dialog grace window (finding 1, 2026-10-05).
RESOURCE_FIELDS = ("Name", "Pool")
REQUIREMENT_FIELDS = ("Name",)
FILTER_FIELDS = ("Filtering Conditions Name", "Filtering Option", "Filtering Operation",
                 "Filtering Property Source")


def fmt(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return str(v)
    return str(int(v)) if float(v).is_integer() else repr(float(v))


def _num_ok(v, minimum_exclusive):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v > minimum_exclusive


def validate(spec):
    classes, reqs = spec.get("classes") or [], spec.get("requirements") or []
    if not classes:
        return "at least one class is needed"
    if not reqs:
        return "at least one requirement is needed"
    seen = set()
    for c in classes:
        name = c.get("name")
        if not name or not c.get("pool"):
            return "every class needs a name and a pool"
        if name in seen:
            return f"class name {name!r} is used twice"
        seen.add(name)
        if not _num_ok(c.get("count"), 0) or not float(c["count"]).is_integer():
            return f"class {name!r}: count must be a whole number, 1 or more"
        if not _num_ok(c.get("maxQuantity", 1), 0):
            return f"class {name!r}: maxQuantity must be above 0"
        if not _num_ok(c.get("minAllocation", 1), 0):
            return f"class {name!r}: minAllocation must be above 0"
        if '"' in (c.get("shift") or "") or '"' in name or '"' in c["pool"]:
            return f"class {name!r}: names cannot contain a quote character"
    types = {}
    for c in classes:
        for prop, value in (c.get("properties") or {}).items():
            if not prop:
                return f"class {c['name']!r}: a property name is empty"
            if '"' in prop:
                return f"class {c['name']!r}: property {prop!r} cannot contain a quote character"
            if prop in REQUIRED_CLASS_FIELDS or prop == "Shift":
                return f"class {c['name']!r}: {prop!r} is a reserved column name"
            kind = "string" if isinstance(value, str) else "real"
            if types.setdefault(prop, kind) != kind:
                return f"property {prop!r} is a number in one class and text in another"
    names = [r.get("name") for r in reqs]
    for r in reqs:
        name = r.get("name")
        if not name or '"' in name:
            return "every requirement needs a name without quote characters"
        if names.count(name) > 1:
            return f"requirement name {name!r} is used twice"
        has_q, any_of = "quantity" in r, r.get("anyOf")
        if has_q == bool(any_of):
            return f"requirement {name!r}: give quantity or anyOf (exactly one)"
        if any_of and r.get("where"):
            # The OR rows carry no filters of their own (spec section 4): a where here would be
            # dropped silently, so refuse it rather than generate a requirement that ignores it.
            return (f"requirement {name!r}: where cannot be combined with anyOf - put the filters on the "
                    "member requirements instead")
        if has_q and not _num_ok(r["quantity"], 0):
            return f"requirement {name!r}: quantity must be above 0"
        for member in any_of or []:
            if member == name:
                return f"requirement {name!r}: anyOf cannot name the requirement itself"
            if member not in names:
                return f"requirement {name!r}: anyOf names unknown requirement {member!r}"
            if next(x for x in reqs if x.get("name") == member).get("anyOf"):
                return f"requirement {name!r}: anyOf members cannot have anyOf themselves (one level of OR)"
        for w in r.get("where") or []:
            if w.get("op") not in OPS:
                return f"requirement {name!r}: op {w.get('op')!r} is not one of {', '.join(OPS)}"
            if not w.get("property") or '"' in str(w.get("property")) or '"' in fmt(w.get("value", "")):
                return f"requirement {name!r}: where needs a property and a value without quote characters"
    for q in spec.get("queues") or []:
        if q.get("requirement") not in names:
            return f"queue {q.get('blockId')}: requirement {q.get('requirement')!r} is not in requirements"
    return None


def class_columns(classes):
    cols = []
    if any(c.get("shift") for c in classes):
        cols.append(("Shift", "string"))
    for c in classes:
        for prop, value in (c.get("properties") or {}).items():
            if prop not in [n for n, _ in cols]:
                cols.append((prop, "string" if isinstance(value, str) else "real"))
    return cols


def class_rows(classes):
    rows = []
    for c in classes:
        row = dict(zip(REQUIRED_CLASS_FIELDS, (c["name"], c["pool"], c["count"],
                                               c.get("maxQuantity", 1), c.get("minAllocation", 1))))
        if c.get("shift"):
            row["Shift"] = c["shift"]
        row.update(c.get("properties") or {})
        rows.append(row)
    return rows


def expression_rows(requirements):
    """The Resource Requirement Expressions rows, requirements in call order.

    Every requirement carries its own Expression Group ID (EG_1, EG_2 ... in call order) on all
    its rows. ExtendSim refuses two requirements that share a group, the empty group included:
    "ERROR: Resource requirement expression group= is used in multiple resource requirements"
    (measured live 2026-10-05 with two plain requirements). An anyOf requirement points at its
    members' groups."""
    group = {r["name"]: f"EG_{i + 1}" for i, r in enumerate(requirements)}
    rows = []
    for r in requirements:
        name, eg = r["name"], group[r["name"]]
        if r.get("anyOf"):
            parts = r["anyOf"]
            for i, m in enumerate(parts):
                rows.append([name, "Expression Group ID", '"="', group[m], "OR" if i < len(parts) - 1 else "", eg])
            continue
        items = [("Quantity", "=", r["quantity"])] + [(w["property"], w["op"], w["value"])
                                                       for w in r.get("where") or []]
        for i, (prop, op, value) in enumerate(items):
            rows.append([name, prop, f'"{op}"', fmt(value), "AND" if i < len(items) - 1 else "", eg])
    return rows


QUEUE_SETTINGS = (("QueueType_pop", 3), ("AR_ReqirementSource_pop", 2))   # advanced resource queue, list
RELEASE_SETTING = ("ReleaseBehavior_POP", 2)                                # advanced resources
GENERATE_BUTTON = "AutogenerateResources_BTN"
RM_DB = "_rM_Database"


def _err(code, message, steps=None):
    r = {"success": False, "errorCode": code, "error": message}
    if steps is not None:
        r["steps"] = steps
    return r


def _same(a, b):
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return str(a) == str(b)


def _check_model(ops, spec):
    """Block checks against the open model. Returns (resource manager id or None, error or None)."""
    blocks = ops.blocks()
    by_id = {b["blockId"]: b["blockName"] for b in blocks}
    managers = [b["blockId"] for b in blocks if b["blockName"] == "Resource Manager"]
    rm = spec.get("resourceManagerBlockId")
    if rm is not None and by_id.get(rm) != "Resource Manager":
        return None, f"block {rm} is not a Resource Manager"
    if rm is None and len(managers) > 1:
        return None, "the model has more than one Resource Manager (blocks " + \
            ", ".join(str(m) for m in managers) + "); give resourceManagerBlockId"
    for q in spec.get("queues") or []:
        if by_id.get(q.get("blockId")) != "Queue":
            return None, f"block {q.get('blockId')} is not a Queue"
    for rel in spec.get("releases") or []:
        if by_id.get(rel) != "Resource Pool Release":
            return None, f"block {rel} is not a Resource Pool Release"
    shifts = {ops.shift_name(b["blockId"]) for b in blocks if b["blockName"] == "Shift"}
    for c in spec["classes"]:
        if c.get("shift") and c["shift"] not in shifts:
            return None, f"class {c['name']!r}: no Shift block is named {c['shift']!r}"
    return (rm if rm is not None else (managers[0] if managers else None)), None


def _write_table(ops, table, records):
    """Replace every record of a table and read each written cell back. Returns a problem or None."""
    ops.delete_all(table)
    ops.add_records(table, len(records))
    for i, rec in enumerate(records):
        for field, value in rec.items():
            ops.set_cell(table, field, i, value)
    back = ops.rows(DB, table)
    for i, rec in enumerate(records):
        for field, value in rec.items():
            got = back[i].get(field) if i < len(back) else None
            if not _same(got, value):
                return f"{table} record {i} field {field!r} reads back {got!r}, expected {value!r}"
    return None


# Steps before the Auto-Generate button has been pressed: an ops exception here is a plain
# COM failure (nothing ExtendSim-side has run yet). From "generate" onward, ExtendSim's own
# generation has already executed, so an ops exception there is reported as a generation
# failure instead (the press-failure branch inside configure() already does this for the
# press() call itself; this constant covers every OTHER ops call).
_PRE_GENERATION_STEPS = ("resourceManager", "classes", "expressions")


def configure(ops, spec):
    problem = validate(spec)
    if problem:
        return _err("INVALID_PARAMETER", problem)
    steps = []
    current_step = "resourceManager"
    try:
        rm, problem = _check_model(ops, spec)
        if problem:
            return _err("INVALID_PARAMETER", problem)
        if rm is None:
            rm = ops.add_block("Item.lbr", "Resource Manager")
            steps.append({"step": "resourceManager", "placed": rm})
        else:
            steps.append({"step": "resourceManager", "blockId": rm})

        current_step = "classes"
        existing = ops.fields(CLASSES)
        for name, kind in class_columns(spec["classes"]):
            if name not in existing:
                ops.add_field(CLASSES, name, kind)
        problem = _write_table(ops, CLASSES, class_rows(spec["classes"]))
        if problem:
            return _err("ARM_VERIFY_FAILED", problem, steps)
        steps.append({"step": "classes", "records": len(spec["classes"])})

        current_step = "expressions"
        rows = [dict(zip(EXPRESSION_FIELDS, r)) for r in expression_rows(spec["requirements"])]
        problem = _write_table(ops, EXPRESSIONS, rows)
        if problem:
            return _err("ARM_VERIFY_FAILED", problem, steps)
        steps.append({"step": "expressions", "records": len(rows)})

        current_step = "generate"
        ops.open_dialog(rm)
        try:
            ops.press(rm, GENERATE_BUTTON)
        except Exception as e:
            ops.close_dialog(rm)
            steps.append({"step": "generate", "ok": False})
            return _err("ARM_GENERATION_FAILED", f"pressing Auto-Generate Components failed: {e}", steps)
        ops.close_dialog(rm)
        resources = ops.rows(DB, "Resources", RESOURCE_FIELDS)
        generated = [r.get("Name") for r in ops.rows(DB, "Resource Requirements", REQUIREMENT_FIELDS)]
        wanted_count = sum(int(c["count"]) for c in spec["classes"])
        wanted_names = [r["name"] for r in spec["requirements"]]
        steps.append({"step": "generate", "resources": len(resources), "requirements": len(generated)})
        if len(resources) != wanted_count or generated != wanted_names:
            return _err("ARM_GENERATION_FAILED",
                        f"ExtendSim generated {len(resources)} resources (expected {wanted_count}) and the "
                        f"requirements {generated} (expected {wanted_names}); see dialogsDuringCommand for "
                        "ExtendSim's own message", steps)

        current_step = "queues"
        for q in spec.get("queues") or []:
            for name, value in QUEUE_SETTINGS + (("AR_ReqirementSource_ListPop",
                                                  generated.index(q["requirement"]) + 1),):
                ops.set_number(q["blockId"], name, value)
        steps.append({"step": "queues", "blocks": [q["blockId"] for q in spec.get("queues") or []]})

        current_step = "releases"
        for rel in spec.get("releases") or []:
            ops.set_number(rel, *RELEASE_SETTING)
        steps.append({"step": "releases", "blocks": list(spec.get("releases") or [])})

        current_step = "verify"
        filters = [{"name": f.get("Filtering Conditions Name"), "property": f.get("Filtering Option"),
                    "operation": f.get("Filtering Operation"), "source": f.get("Filtering Property Source")}
                   for f in ops.rows(RM_DB, "rM_Filtering Conditions", FILTER_FIELDS)]
        has_where = any(r.get("where") for r in spec["requirements"])
        if has_where and not filters:
            return _err("ARM_VERIFY_FAILED",
                        "requirements have filters but ExtendSim generated none", steps)
        empty = [f["name"] for f in filters if not f["source"]]
        if empty:
            return _err("ARM_VERIFY_FAILED", f"filters without a property source (nothing would be allocated): "
                        f"{empty}", steps)
        for q in spec.get("queues") or []:
            for name, value in QUEUE_SETTINGS + (("AR_ReqirementSource_ListPop",
                                                  generated.index(q["requirement"]) + 1),):
                if not _same(ops.get_number(q["blockId"], name), value):
                    return _err("ARM_VERIFY_FAILED", f"queue {q['blockId']}: {name} did not read back {value}",
                                steps)
        for rel in spec.get("releases") or []:
            if not _same(ops.get_number(rel, RELEASE_SETTING[0]), RELEASE_SETTING[1]):
                return _err("ARM_VERIFY_FAILED", f"release {rel}: {RELEASE_SETTING[0]} did not read back", steps)
        steps.append({"step": "verify", "ok": True})

        by_pool = {}
        for r in resources:
            by_pool.setdefault(r.get("Pool"), []).append(r.get("Name"))
        return {"success": True, "resourceManagerBlockId": rm, "resources": by_pool,
                "requirements": [{"name": n, "queuePosition": i + 1} for i, n in enumerate(generated)],
                "filters": filters, "queues": spec.get("queues") or [], "releases": spec.get("releases") or [],
                "steps": steps}
    except Exception as e:
        code = "COM_ERROR" if current_step in _PRE_GENERATION_STEPS else "ARM_GENERATION_FAILED"
        return _err(code, f"the {current_step} step failed: {e}", steps)


class BackendOps:
    """The ops contract over simulation_backend (passed in, never imported here)."""

    def __init__(self, backend, app):
        self._b, self._app = backend, app

    @staticmethod
    def _ok(r):
        if not r.get("success"):
            raise RuntimeError(r.get("error") or "ExtendSim refused the call")
        return r

    def blocks(self):
        # block_list() omits "success" entirely on its happy path (only sets it to False
        # on an error) - the one simulation_backend reader that does not follow the
        # {success: true/false} convention. _ok() would misread that omission as a
        # failure, so check explicitly for the error case instead.
        r = self._b.block_list()
        if r.get("success") is False:
            raise RuntimeError(r.get("error") or "ExtendSim refused the call")
        return [{"blockId": b["blockId"], "blockName": b.get("blockName") or ""}
                for b in r.get("blocks", [])]

    def add_block(self, library, name):
        return self._ok(self._b.block_add(library, name, x=50, y=400))["blockId"]

    def shift_name(self, bid):
        self._app.Execute(f'globalStr0 = GetDialogVariable({bid}, "ShiftName", 0, 0);')
        return self._b._read_str0(self._app)

    def fields(self, table):
        return [f["name"] for f in self._ok(self._b.db_table_info(DB, table)).get("fields", [])]

    def add_field(self, table, name, kind):
        self._ok(self._b.db_create(DB, [{"name": table, "fields": [{"name": name, "type": kind}]}]))

    def rows(self, db, table, fields=None):
        """Every record of a table as {field name: value}. With `fields`, only those fields are
        read (one db_get_records call, no field-type probing of the other columns); a field the
        table does not have is simply missing from the rows."""
        if fields:
            recs = self._ok(self._b.db_get_records(db, table, fields=list(fields),
                                                   max_records=1_000_000)).get("records") or []
            return [dict(r) for r in recs]
        info = self._ok(self._b.db_table_info(db, table))
        names = [f["name"] for f in info.get("fields", [])]
        if not info.get("records"):
            return []
        recs = self._ok(self._b.db_get_records(db, table, max_records=int(info["records"]))).get("records") or []
        return [{names[k]: r.get(f"field_{k}") for k in range(len(names))} for r in recs]

    def delete_all(self, table):
        n = int(self._ok(self._b.db_table_info(DB, table)).get("records") or 0)
        if n:
            self._ok(self._b.db_delete_records(DB, table, 0, n - 1))

    def add_records(self, table, n):
        self._ok(self._b.db_add_records(DB, table, n))

    def set_cell(self, table, field, record, value):
        self._ok(self._b.db_set_value(DB, table, field, record, value))

    def set_number(self, bid, name, value):
        self._b._set_var(self._app, bid, name, value)

    def get_number(self, bid, name):
        return float(self._b.parse_float(self._b._get_var(self._app, bid, name)))

    def open_dialog(self, bid):
        self._app.Execute(f"OpenBlockDialogBox({bid});")

    def close_dialog(self, bid):
        self._app.Execute(f"CloseBlockDialogBox({bid});")

    def press(self, bid, name):
        self._app.Execute(f'SetDialogVariable({bid}, "{name}", "1", 0, 0);')


def resources_configure_command(params, ops=None):
    if ops is None:
        import simulation_backend as sb
        app = sb.get_extendsim_app()
        check = sb._validate_model_open(app)
        if not check.get("success"):
            return check
        ops = BackendOps(sb, app)
    try:
        return configure(ops, params)
    except Exception as e:     # a COM failure mid-sequence: report, never a success
        return _err("ARM_GENERATION_FAILED", f"resources_configure stopped: {e}")
