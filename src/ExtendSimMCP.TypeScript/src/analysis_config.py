"""Analysis tools through COM: Scenario Manager, Optimizer, Analysis Manager.

Inputs and results are added the documented Shift-click way (Technical Reference, "Shift-click"); the
"User Parameter" prompts that follow are answered by text_prompt.PromptAnswerer. This module validates
the AI's description, runs the per-block sequences through an injected `ops` object and reads every write
back (fail closed). No COM import here; BackendOps adapts simulation_backend.
"""
import math
import re
import time

MAX_SCENARIOS = 1000
# Time budget of one configure call: the TypeScript side gives it 180 s and the prompt answerer cancels
# any "User Parameter" prompt still open 150 s (PROMPT_TIMEOUT_S) after the call started. Measured on
# ExtendSim 2024: a Scenario Manager call with 2 prompts took 12.0 s and one with 3 prompts 16.0 s, so
# each prompt costs about 4 s on top of about 4 s of fixed work. 20 items is about 85 s - well inside
# the 150 s deadline - and leaves the rest of the 180 s for the read-back and the scenario cells.
MAX_ITEMS_PER_CALL = 20
# A named scenario costs (2 + factors) table cells, each written and read back; one cell read measured
# 9.2 ms, so a cell costs roughly 15 ms. 50 scenarios of 19 factors is 1,050 cells, about 16 s.
MAX_NAMED_SCENARIOS = 50
VAR_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
OBJ_RE = re.compile(r"^\s*((?:Max|Min)[A-Za-z0-9_]*)\s*=\s*(.+?)\s*;?\s*$")
OBJ_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+(?:\.\d+)?|[+\-*/()\s.]")


def num(text):
    try:
        return float(str(text).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _check_refs(items, kind):
    if not items:
        return f"at least one {kind} is needed"
    seen = set()
    for it in items:
        name = it.get("name") or ""
        if not name:
            return f"every {kind} needs a name"
        if '"' in name:
            return f"{kind} name {name!r} cannot contain a quote character"
        if name in seen:
            return f"{kind} name {name!r} is used twice"
        seen.add(name)
        if not _is_num(it.get("blockId")):
            return f"{kind} {name!r} needs a blockId"
        if not VAR_RE.match(str(it.get("variable") or "")):
            return f"{kind} {name!r}: variable must be a dialog variable name (letters, digits, _)"
        for k in ("row", "column"):
            if k in it and (not isinstance(it[k], int) or it[k] < 0):
                return f"{kind} {name!r}: {k} must be a whole number >= 0"
    return None


def _check_item_cap(count, what):
    if count > MAX_ITEMS_PER_CALL:
        return (f"{count} {what} in one call; at most {MAX_ITEMS_PER_CALL} per call, so the call fits its "
                "time limit")
    return None


def full_factorial_count(factors):
    total = 1
    for f in factors:
        total *= int(math.floor((f["max"] - f["min"]) / f["step"] + 1e-9)) + 1
    return total


def validate_scenario_manager(spec):
    factors, responses = spec.get("factors") or [], spec.get("responses") or []
    problem = (_check_refs(factors, "factor") or _check_refs(responses, "response") or
               _check_item_cap(len(factors) + len(responses), "factors and responses"))
    if problem:
        return problem
    scen = spec.get("scenarios", "fullFactorial")
    if scen == "fullFactorial":
        for f in factors:
            if not all(_is_num(f.get(k)) for k in ("min", "max", "step")):
                return f"factor {f['name']!r}: full factorial needs min, max and step"
            if f["min"] >= f["max"]:
                return f"factor {f['name']!r}: min must be below max"
            if f["step"] <= 0:
                return f"factor {f['name']!r}: step must be above 0"
        if full_factorial_count(factors) > MAX_SCENARIOS:
            return f"full factorial gives {full_factorial_count(factors)} scenarios; at most {MAX_SCENARIOS}"
        return None
    if not isinstance(scen, list) or not scen:
        return "scenarios must be \"fullFactorial\" or a list of at least one scenario"
    if len(scen) > MAX_NAMED_SCENARIOS:
        return (f"{len(scen)} named scenarios; at most {MAX_NAMED_SCENARIOS} in one call (every cell is "
                "written and read back) - use \"fullFactorial\" for a larger sweep")
    names = [f["name"] for f in factors]
    seen = set()
    for s in scen:
        nm = s.get("name") or ""
        if not nm or '"' in nm:
            return "every scenario needs a name without quote characters"
        if nm in seen:
            return f"scenario name {nm!r} is used twice"
        seen.add(nm)
        values = s.get("values") or {}
        for n in names:
            if not _is_num(values.get(n)):
                return f"scenario {nm!r}: factor {n!r} needs a number"
        for extra in set(values) - set(names):
            return f"scenario {nm!r}: {extra!r} is not a factor of this call"
    return None


def normalize_objective(text):
    return text.strip().rstrip(";").strip() + ";"


def validate_optimizer(spec):
    variables, outputs = spec.get("variables") or [], spec.get("outputs") or []
    problem = (_check_refs(variables, "variable") or _check_refs(outputs, "output") or
               _check_item_cap(len(variables) + len(outputs), "variables and outputs"))
    if problem:
        return problem
    names = [v["name"] for v in variables] + [o["name"] for o in outputs]
    if len(set(names)) != len(names):
        return "variable and output names must all be different"
    for v in variables:
        if not (_is_num(v.get("min")) and _is_num(v.get("max"))) or v["min"] >= v["max"]:
            return f"variable {v['name']!r}: min must be a number below max"
    if spec.get("quality", "quick") not in ("quick", "better"):
        return "quality must be \"quick\" or \"better\""
    if "maxCases" in spec and (not isinstance(spec["maxCases"], int) or spec["maxCases"] < 1):
        return "maxCases must be a whole number >= 1"
    m = OBJ_RE.match(spec.get("objective") or "")
    if not m:
        return "objective must look like 'MaxProfit = ...;' or 'MinCost = ...;'"
    expr = m.group(2)
    if "".join(OBJ_TOKEN.findall(expr)) != expr:
        return "objective may only use the call's names, numbers, + - * / and parentheses"
    for ident in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", expr):
        if ident not in names:
            return f"objective uses {ident!r}, which is not a variable or output of this call"
    return None


def validate_analysis_manager(spec):
    responses = spec.get("responses") or []
    return _check_refs(responses, "response") or _check_item_cap(len(responses), "responses")


LIB = "Analysis.lbr"
PROMPT_TIMEOUT_S = 150
# The 150 s run from the start of the call, like the TypeScript timeout: time spent looking up blocks
# before the first prompt comes off it. With less than this left, nothing is added at all.
MIN_PROMPT_BUDGET_S = 10


def _err(code, message, steps=None, **extra):
    r = {"success": False, "errorCode": code, "error": message}
    if steps is not None:
        r["steps"] = steps
    r.update(extra)
    return r


def _resolve_block(ops, kind, given):
    """(block id or None to place, problem or None)."""
    found = [b["blockId"] for b in ops.blocks() if b["blockName"] == kind]
    if given is not None:
        return (given, None) if given in found else (None, f"block {given} is not a {kind}")
    if len(found) > 1:
        return None, f"the model has more than one {kind} (blocks {', '.join(map(str, found))}); give its block id"
    return (found[0] if found else None), None


def _check_targets(ops, items, own_kind):
    by_id = {b["blockId"]: b["blockName"] for b in ops.blocks()}
    for it in items:
        if it["blockId"] not in by_id:
            return f"{it['name']!r}: block {it['blockId']} is not in the model"
        if by_id[it["blockId"]] == own_kind:
            return f"{it['name']!r}: block {it['blockId']} is the {own_kind} itself"
    return None


def _shift_click(ops, answerer, block, prm, item):
    """The documented Shift-click: statics, then the hidden Add..._prm; the answerer types the name."""
    answerer.expect(item["name"])
    ops.set_text(block, "DialogVarName", item["variable"])
    ops.set_text(block, "RowColumnDialogVar", f"{item.get('row', 0)},{item.get('column', 0)}")
    ops.set_text(block, prm, str(item["blockId"]))


def _same(a, b):
    na, nb = num(a), num(b)
    return na == nb if na is not None and nb is not None else str(a).strip() == str(b).strip()


def _pending_item_name(items, answerer):
    """The name of the item whose "User Parameter" prompt is still queued (unanswered), or None.

    `items` is the combined, ordered list that `expect()` was called against; the queue is FIFO, so
    whatever is still queued is exactly its tail.
    """
    p = answerer.pending()
    if 0 < p <= len(items):
        return items[len(items) - p]["name"]
    return None


def _prompts_given_up(answerer):
    """True once the answerer has cancelled a prompt or passed its deadline. No further Shift-click may
    then be issued: its prompt would only be cancelled, and the queued names would no longer line up
    with the prompts."""
    return bool(answerer.cancelled) or answerer.past_deadline()


def _given_up_message(name):
    return (f"stopped at {name!r}: a User Parameter prompt was cancelled at the {PROMPT_TIMEOUT_S} s "
            "deadline (or the deadline passed), so no further item was added")


def _prompt_fields(answerer):
    return {"prompts": answerer.answered, "unexpectedPrompts": answerer.unexpected,
            "cancelledPrompts": answerer.cancelled}


def _read_back_rows(ops, block, table, items, start=0):
    """Problem or None: the table must have exactly start+len(items) rows (no missing or extra rows),
    and row start+i must hold item i's name, block number, variable and "row,col" static."""
    n = ops.rows(block, table)
    want = start + len(items)
    if n < 0:
        return f"{table} could not be read"
    if n > want:
        return f"{table} has {n} rows, expected {want}: {n - want} extra row(s) this call did not ask for"
    if n < want:
        idx = n - start
        hint = f"; the first missing one is {items[idx]['name']!r}, which did not take" if 0 <= idx < len(items) else ""
        return f"{table} has {n} rows, expected {want}{hint}"
    for i, it in enumerate(items):
        r = start + i
        want_rc = f"{it.get('row', 0)},{it.get('column', 0)}"
        got = (ops.get_text(block, table, r, 0), ops.get_text(block, table, r, 2),
               ops.get_text(block, table, r, 6), ops.get_text(block, table, r, 7))
        want = (it["name"], it["blockId"], it["variable"], want_rc)
        if not (got[0] == want[0] and _same(got[1], want[1]) and got[2] == want[2] and got[3] == want[3]):
            return f"{table} row {r} reads {got}, expected {want}"
    return None


def _prompt_budget(clock, started, steps):
    """(seconds left for the prompts, None) or (None, the error to return) - see MIN_PROMPT_BUDGET_S."""
    budget = PROMPT_TIMEOUT_S - (clock() - started)
    if budget < MIN_PROMPT_BUDGET_S:
        return None, _err("ANALYSIS_CONFIG_FAILED", f"only {max(budget, 0):.0f} s of the {PROMPT_TIMEOUT_S} s "
                          "prompt time was left after looking up the blocks - too little to add anything "
                          "safely, so nothing was added", steps)
    return budget, None


def configure_scenario_manager(ops, spec, clock=time.monotonic):
    started = clock()
    problem = validate_scenario_manager(spec)
    if problem:
        return _err("INVALID_PARAMETER", problem)
    kind = "Scenario Manager"
    given = spec.get("scenarioManagerBlockId")
    if given is not None:
        try:
            given = int(given)
        except (TypeError, ValueError):
            return _err("INVALID_PARAMETER", f"scenarioManagerBlockId must be a whole number, got {given!r}")
    sm, problem = _resolve_block(ops, kind, given)
    problem = problem or _check_targets(ops, spec["factors"] + spec["responses"], kind)
    if problem:
        return _err("INVALID_PARAMETER", problem)
    steps = []
    answerer = None
    items_added = spec["factors"] + spec["responses"]
    try:
        if sm is not None:
            factor_rows, response_rows = ops.rows(sm, "FactorVariable_tbl"), ops.rows(sm, "ResponseVariable_tbl")
            if factor_rows < 0 or response_rows < 0:
                return _err("INVALID_PARAMETER", "could not read the Scenario Manager's tables", steps)
            if factor_rows > 0 or response_rows > 0:
                return _err("INVALID_PARAMETER", f"Scenario Manager {sm} already has factors or responses; "
                            "changing them is not supported yet - use a new Scenario Manager", steps)
        if sm is None:
            sm = ops.add_block(LIB, kind)
            if sm is None:
                return _err("ANALYSIS_CONFIG_FAILED", "add_block did not return a block id", steps)
            steps.append({"step": "placed", "blockId": sm})
        budget, late = _prompt_budget(clock, started, steps)
        if late:
            return late
        answerer = ops.answerer(budget)
        add_step = {"step": "factors and responses", "attempted": {"factors": 0, "responses": 0},
                    "verified": False}
        steps.append(add_step)
        adds = ([("AddFactor_prm", "factors", f) for f in spec["factors"]] +
                [("AddResponse_prm", "responses", r) for r in spec["responses"]])
        given_up_at = None
        for prm, key, it in adds:
            _shift_click(ops, answerer, sm, prm, it)
            add_step["attempted"][key] += 1
            if _prompts_given_up(answerer):
                given_up_at = it["name"]
                break
        add_step["factorRows"] = ops.rows(sm, "FactorVariable_tbl")
        add_step["responseRows"] = ops.rows(sm, "ResponseVariable_tbl")
        if given_up_at is not None:
            return _err("ANALYSIS_CONFIG_FAILED", _given_up_message(given_up_at), steps, **_prompt_fields(answerer))
        problem = (_read_back_rows(ops, sm, "FactorVariable_tbl", spec["factors"]) or
                   _read_back_rows(ops, sm, "ResponseVariable_tbl", spec["responses"]))
        if problem:
            msg = f"the read-back failed: {problem}"
            pending_name = _pending_item_name(items_added, answerer)
            if pending_name is not None:
                msg += f"; {pending_name!r}'s User Parameter prompt was never answered"
            return _err("ANALYSIS_CONFIG_FAILED", msg, steps, **_prompt_fields(answerer))
        add_step["verified"] = True

        scen = spec.get("scenarios", "fullFactorial")
        if scen == "fullFactorial":
            ranges_done = []
            for i, f in enumerate(spec["factors"]):
                for col, key in ((9, "min"), (10, "max"), (11, "step")):
                    ops.set_text(sm, "FactorVariable_tbl", str(f[key]), i, col)
                    if not _same(ops.get_text(sm, "FactorVariable_tbl", i, col), f[key]):
                        steps.append({"step": "factor ranges", "written": ranges_done, "verified": False,
                                      "failed": {"factor": f["name"], "field": key}})
                        return _err("ANALYSIS_CONFIG_FAILED", f"factor {f['name']!r} {key} did not read back",
                                    steps, **_prompt_fields(answerer))
                    ranges_done.append({"factor": f["name"], "field": key, "value": f[key]})
            steps.append({"step": "factor ranges", "written": ranges_done, "verified": True})
            expected = full_factorial_count(spec["factors"])
            ops.set_text(sm, "CreateScenarios_pop", "2")
            ops.set_text(sm, "CreateDOE_btn", "1")
        else:
            expected = len(scen)
            if _prompts_given_up(answerer):
                steps.append({"step": "create scenarios", "mode": "manual", "createScenariosPopSet": False,
                              "createDoeBtnPressed": False})
                return _err("ANALYSIS_CONFIG_FAILED", f"the {PROMPT_TIMEOUT_S} s prompt deadline passed before "
                            "the scenario count could be given", steps, **_prompt_fields(answerer))
            ops.set_text(sm, "CreateScenarios_pop", "1")
            answerer.expect(str(expected))
            ops.set_text(sm, "CreateDOE_btn", "1")
        got = ops.rows(sm, "Scenarios_tbl")
        if got != expected:
            steps.append({"step": "create scenarios", "mode": "fullFactorial" if scen == "fullFactorial" else "manual",
                          "createScenariosPopSet": True, "createDoeBtnPressed": True,
                          "rowsCreated": got, "expected": expected})
            return _err("ANALYSIS_CONFIG_FAILED", f"ExtendSim created {got} scenarios, expected {expected}",
                        steps, **_prompt_fields(answerer))
        if scen != "fullFactorial":
            for r, s in enumerate(scen):
                cells = [(0, "1"), (1, s["name"])] + [(2 + i, str(s["values"][f["name"]]))
                                                      for i, f in enumerate(spec["factors"])]
                for col, text in cells:
                    ops.set_text(sm, "Scenarios_tbl", text, r, col)
                for col, text in cells:
                    if not _same(ops.get_text(sm, "Scenarios_tbl", r, col), text):
                        return _err("ANALYSIS_CONFIG_FAILED", f"scenario {s['name']!r} column {col} did not read back",
                                    steps, **_prompt_fields(answerer))
        steps.append({"step": "scenarios", "count": expected})

        settings_done = []
        for var, key in (("RunsPerScenario_prm", "runsPerScenario"), ("SimEnd_prm", "endTime")):
            if key in spec:
                ops.set_number(sm, var, spec[key])
                if not _same(ops.get_number(sm, var), spec[key]):
                    steps.append({"step": "settings", "written": settings_done, "verified": False, "failed": key})
                    return _err("ANALYSIS_CONFIG_FAILED", f"{key} did not read back",
                                steps, **_prompt_fields(answerer))
                settings_done.append(key)
        if settings_done:
            steps.append({"step": "settings", "written": settings_done, "verified": True})
        return {"success": True, "scenarioManagerBlockId": sm, "scenarios": expected,
                "factors": [f["name"] for f in spec["factors"]], "responses": [r["name"] for r in spec["responses"]],
                "steps": steps, **_prompt_fields(answerer)}
    except Exception as e:
        pending_name = _pending_item_name(items_added, answerer) if answerer is not None else None
        msg = f"stopped: {e}"
        if pending_name is not None:
            msg += f"; {pending_name!r}'s User Parameter prompt was never answered"
        extra = _prompt_fields(answerer) if answerer is not None else {}
        return _err("ANALYSIS_CONFIG_FAILED", msg, steps, **extra)
    finally:
        if answerer is not None:
            answerer.stop()


DEFAULT_BUTTONS = {(True, "quick"): "DefaultsRandom", (False, "quick"): "DefaultsNonRandom",
                   (True, "better"): "DefaultsStrictRandom", (False, "better"): "DefaultsStrictNonRandom"}
ADB = "Analysis"


def _rc_matches(got, want):
    """Measured live: ExtendSim leaves VariablesTableItem col 3 (the "row,col" static) EMPTY for the
    default row,col 0,0 even though RowColumnDialogVar was set beforehand; any other row,col must read
    back the exact "row,col" text - an empty col 3 there is a real miss."""
    return got == want or (want == "0,0" and got == "")


def configure_optimizer(ops, spec, clock=time.monotonic):
    started = clock()
    problem = validate_optimizer(spec)
    if problem:
        return _err("INVALID_PARAMETER", problem)
    kind = "Optimizer"
    given = spec.get("optimizerBlockId")
    if given is not None:
        try:
            given = int(given)
        except (TypeError, ValueError):
            return _err("INVALID_PARAMETER", f"optimizerBlockId must be a whole number, got {given!r}")
    op, problem = _resolve_block(ops, kind, given)
    problem = problem or _check_targets(ops, spec["variables"] + spec["outputs"], kind)
    if problem:
        return _err("INVALID_PARAMETER", problem)
    steps = []
    answerer = None
    items = spec["variables"] + spec["outputs"]
    n_vars = len(spec["variables"])
    try:
        # VariablesTableItem has a fixed number of rows (100 in ExtendSim 2024/2026). Reading a row past
        # it pops a modal box that blocks COM, so every read below stays inside the measured row count.
        if op is not None:
            table_rows = ops.rows(op, "VariablesTableItem")
            if table_rows < 1:
                return _err("INVALID_PARAMETER", f"could not read Optimizer {op}'s variables table", steps)
            if table_rows < len(items):
                return _err("INVALID_PARAMETER", f"Optimizer {op}'s variables table holds {table_rows} rows, "
                            f"fewer than the {len(items)} variables and outputs asked for", steps)
            has_variables = bool(ops.get_text(op, "VariablesTableItem", 0, 0).strip())
            if has_variables:
                return _err("INVALID_PARAMETER", f"Optimizer {op} already has variables; changing them is not "
                            "supported yet - use a new Optimizer", steps)
        else:
            op = ops.add_block(LIB, kind)
            if op is None:
                return _err("ANALYSIS_CONFIG_FAILED", "add_block did not return a block id", steps)
            steps.append({"step": "placed", "blockId": op})
            table_rows = ops.rows(op, "VariablesTableItem")
            if table_rows < len(items):
                return _err("ANALYSIS_CONFIG_FAILED", f"the new Optimizer's variables table holds {table_rows} "
                            f"rows, fewer than the {len(items)} variables and outputs asked for", steps)
        budget, late = _prompt_budget(clock, started, steps)
        if late:
            return late
        answerer = ops.answerer(budget)

        add_step = {"step": "variables and outputs", "attempted": {"variables": 0, "outputs": 0}, "verified": False}
        steps.append(add_step)
        for idx, it in enumerate(items):
            _shift_click(ops, answerer, op, "AddParameter_prm", it)
            add_step["attempted"]["variables" if idx < n_vars else "outputs"] += 1
            if _prompts_given_up(answerer):
                return _err("ANALYSIS_CONFIG_FAILED", _given_up_message(it["name"]), steps,
                            **_prompt_fields(answerer))
        for i, it in enumerate(items):
            want_rc = f"{it.get('row', 0)},{it.get('column', 0)}"
            got = (ops.get_text(op, "VariablesTableItem", i, 0), ops.get_text(op, "VariablesTableItem", i, 1),
                   ops.get_text(op, "VariablesTableItem", i, 2), ops.get_text(op, "VariablesTableItem", i, 3))
            if not (got[0] == it["name"] and _same(got[1], it["blockId"]) and got[2] == it["variable"]
                    and _rc_matches(got[3], want_rc)):
                msg = f"an add did not take (row {i} reads {got})"
                pending_name = _pending_item_name(items, answerer)
                if pending_name is not None:
                    msg += f"; {pending_name!r}'s User Parameter prompt was never answered"
                return _err("ANALYSIS_CONFIG_FAILED", msg, steps, **_prompt_fields(answerer))
        add_step["verified"] = True

        range_step = {"step": "variable ranges", "written": [], "verified": False}
        steps.append(range_step)
        for i, v in enumerate(spec["variables"]):
            for col, key in ((4, "min"), (5, "max")):
                ops.set_text(op, "VariablesTableItem", str(v[key]), i, col)
                if not _same(ops.get_text(op, "VariablesTableItem", i, col), v[key]):
                    range_step["failed"] = {"variable": v["name"], "field": key}
                    return _err("ANALYSIS_CONFIG_FAILED", f"variable {v['name']!r} {key} did not read back",
                                steps, **_prompt_fields(answerer))
                range_step["written"].append({"variable": v["name"], "field": key, "value": v[key]})
        range_step["verified"] = True

        quality, random_model = spec.get("quality", "quick"), spec.get("randomModel", True)
        # A button press has no read-back, so this step never claims "verified" - only whether the
        # press was attempted.
        defaults_step = {"step": "defaults", "quality": quality, "randomModel": random_model, "pressed": False}
        steps.append(defaults_step)
        ops.set_text(op, DEFAULT_BUTTONS[(random_model, quality)], "1")
        defaults_step["pressed"] = True

        objective = normalize_objective(spec["objective"])
        objective_step = {"step": "objective", "verified": False}
        steps.append(objective_step)
        ops.set_text(op, "equationArray", objective)
        if ops.get_text(op, "equationArray") != objective:
            return _err("ANALYSIS_CONFIG_FAILED", "the objective did not read back", steps,
                        **_prompt_fields(answerer))
        objective_step["verified"] = True

        if "maxCases" in spec:
            max_cases_step = {"step": "maxCases", "verified": False}
            steps.append(max_cases_step)
            ops.set_number(op, "TermGenerations", spec["maxCases"])
            if not _same(ops.get_number(op, "TermGenerations"), spec["maxCases"]):
                return _err("ANALYSIS_CONFIG_FAILED", "maxCases did not read back", steps,
                            **_prompt_fields(answerer))
            max_cases_step["verified"] = True

        return {"success": True, "optimizerBlockId": op, "objective": objective,
                "variables": [v["name"] for v in spec["variables"]], "outputs": [o["name"] for o in spec["outputs"]],
                "steps": steps, **_prompt_fields(answerer)}
    except Exception as e:
        pending_name = _pending_item_name(items, answerer) if answerer is not None else None
        msg = f"stopped: {e}"
        if pending_name is not None:
            msg += f"; {pending_name!r}'s User Parameter prompt was never answered"
        extra = _prompt_fields(answerer) if answerer is not None else {}
        return _err("ANALYSIS_CONFIG_FAILED", msg, steps, **extra)
    finally:
        if answerer is not None:
            answerer.stop()


def _response_is_blank(row):
    return not (row.get("Response_Name") or "").strip()


def configure_analysis_manager(ops, spec):
    """The responses table ("_Rsp_Block_Responses") is a model-wide database table, not scoped to a
    single Analysis Manager block, so even a block this call would place can inherit stale rows left
    behind by an earlier configuration. The already-configured check therefore reads the table BEFORE
    anything is placed (a refused call leaves the model unchanged; a missing Analysis database simply
    means no responses yet) and again after placing. Existing rows that are already blank (no Response_Name) are
    reused in place rather than left as dead rows ahead of the new ones: only the shortfall
    (len(responses) - len(existing)) is added via db_add, and every row beyond the ones this call
    writes must still be blank afterwards - that is the fail-closed "no stray blank or stale rows"
    check.
    """
    problem = validate_analysis_manager(spec)
    if problem:
        return _err("INVALID_PARAMETER", problem)
    kind = "Analysis Manager"
    given = spec.get("analysisManagerBlockId")
    if given is not None:
        try:
            given = int(given)
        except (TypeError, ValueError):
            return _err("INVALID_PARAMETER", f"analysisManagerBlockId must be a whole number, got {given!r}")
    am, problem = _resolve_block(ops, kind, given)
    problem = problem or _check_targets(ops, spec["responses"], kind)
    if problem:
        return _err("INVALID_PARAMETER", problem)
    steps = []
    try:
        before = ops.db_rows(ADB, "_Rsp_Block_Responses", missing_ok=am is None)
        if before is not None and any(not _response_is_blank(r) for r in before):
            owner = f"Analysis Manager {am}" if am is not None else "The model's Analysis database"
            return _err("INVALID_PARAMETER", f"{owner} already has responses; changing them is not "
                        "supported yet - use a new Analysis Manager in a model without responses", steps)
        if am is None:
            am = ops.add_block(LIB, kind)
            if am is None:
                return _err("ANALYSIS_CONFIG_FAILED", "add_block did not return a block id", steps)
            steps.append({"step": "placed", "blockId": am})
        existing = ops.db_rows(ADB, "_Rsp_Block_Responses")
        if any(not _response_is_blank(r) for r in existing):
            return _err("ANALYSIS_CONFIG_FAILED", f"Analysis Manager {am}'s responses table holds responses "
                        "this call did not write", steps)

        names = {b["blockId"]: b["blockName"] for b in ops.blocks()}
        rows = [{"ID": i + 1, "Response_Name": r["name"], "Block_Name": names[r["blockId"]],
                 "Block_Number": r["blockId"], "Variable_Name": r["variable"]}
                for i, r in enumerate(spec["responses"])]
        deficit = max(0, len(rows) - len(existing))
        # recordsAdded starts at 0 and is only set once db_add actually returns, so a raising
        # db_add does not claim records that were never added.
        step_entry = {"step": "responses", "recordsAdded": 0, "fieldsWritten": 0, "verified": False}
        steps.append(step_entry)
        if deficit:
            ops.db_add(ADB, "_Rsp_Block_Responses", deficit)
            step_entry["recordsAdded"] = deficit
        fields_written = 0
        for i, row in enumerate(rows):
            for field, value in row.items():
                ops.db_set(ADB, "_Rsp_Block_Responses", field, i, value)
                fields_written += 1
                step_entry["fieldsWritten"] = fields_written
        back = ops.db_rows(ADB, "_Rsp_Block_Responses")
        for i, row in enumerate(rows):
            got = back[i] if i < len(back) else {}
            for field, value in row.items():
                if not _same(got.get(field), value):
                    return _err("ANALYSIS_CONFIG_FAILED", f"response row {i} field {field!r} did not read back",
                                steps)
        for i in range(len(rows), len(back)):
            if not _response_is_blank(back[i]):
                return _err("ANALYSIS_CONFIG_FAILED", f"response row {i} is not blank after writing "
                            "(a stray or stale row)", steps)
        step_entry["verified"] = True
        return {"success": True, "analysisManagerBlockId": am, "responses": [r["name"] for r in spec["responses"]],
                "steps": steps, "howToRun": "simulation_run with numberOfRuns = the replications you want; then "
                                            "analysis_manager_get_results"}
    except Exception as e:
        return _err("ANALYSIS_CONFIG_FAILED", f"stopped: {e}", steps)


def analysis_manager_results(ops, experiment=None):
    """`experiment` is normalized to an int whether given directly (string/float) or computed from the
    history; a row whose Replication_ID cannot be read as a number is skipped (flagged via
    `skippedReplications`) rather than crashing `int(None)`. _Rslt_Block_Responses holds every experiment
    ever recorded, so only its Experiment_ID column is read in full; whole rows are read just for the
    span that holds the wanted experiment. Any other unexpected failure is reported closed."""
    try:
        reps = ops.db_rows(ADB, "_Rslt_Replication_History")
        if not reps:
            return _err("ANALYSIS_CONFIG_FAILED", "the Analysis Manager has recorded no experiment yet - run the "
                        "model first")
        if experiment is not None:
            exp_num = num(experiment)
            if exp_num is None:
                return _err("INVALID_PARAMETER", f"experiment must be a number, got {experiment!r}")
            exp = int(exp_num)
        else:
            exp = int(max((num(r.get("Experiment_ID")) or 0) for r in reps))
        values = {}
        ids = ops.db_column(ADB, "_Rslt_Block_Responses", "Experiment_ID")
        hits = [i for i, v in enumerate(ids) if num(v) == exp]
        span = ops.db_rows(ADB, "_Rslt_Block_Responses", start=hits[0], end=hits[-1] + 1) if hits else []
        for v in span:
            if num(v.get("Experiment_ID")) == exp:
                rid_num = num(v.get("Replication_ID"))
                if rid_num is None:
                    continue
                values.setdefault(int(rid_num), {})[v.get("Response_Name")] = num(v.get("Value"))
        out = []
        skipped = 0
        for r in reps:
            if num(r.get("Experiment_ID")) != exp:
                continue
            rid_num = num(r.get("Replication_ID"))
            if rid_num is None:
                skipped += 1
                continue
            rid = int(rid_num)
            out.append({"replication": rid, "run": int(num(r.get("Run_Number")) or 0),
                        "aborted": bool(num(r.get("Aborted"))), "responses": values.get(rid, {})})
        if not out:
            return _err("ANALYSIS_CONFIG_FAILED", f"experiment {exp} has no replications")
        result = {"success": True, "experiment": exp, "replications": out,
                  "aborted": sum(1 for r in out if r["aborted"])}
        if skipped:
            result["skippedReplications"] = skipped
        return result
    except Exception as e:
        return _err("ANALYSIS_CONFIG_FAILED", f"stopped: {e}")


class BackendOps:
    """The ops contract over simulation_backend (passed in, never imported at module level)."""

    def __init__(self, backend, app):
        self._b, self._app = backend, app

    def blocks(self):
        r = self._b.block_list()
        if r.get("success") is False:            # block_list has no "success" key when it works
            raise RuntimeError(r.get("error") or "block_list failed")
        return [{"blockId": b["blockId"], "blockName": b.get("blockName") or ""} for b in r.get("blocks", [])]

    def add_block(self, library, name):
        r = self._b.block_add(library, name, x=50, y=350)
        if not r.get("success"):
            raise RuntimeError(r.get("error") or "block_add failed")
        return r["blockId"]

    def get_text(self, bid, var, row=0, col=0):
        self._app.Execute('globalStr0 = "";')
        self._app.Execute(f'globalStr0 = GetDialogVariable({bid}, "{var}", {row}, {col});')
        return self._b._read_str0(self._app)

    def set_text(self, bid, var, text, row=0, col=0):
        self._app.Execute(f'SetDialogVariable({bid}, "{var}", "{self._b._escape_modl_string(str(text))}", {row}, {col});')

    def get_number(self, bid, var):
        self._app.Execute("global0 = -1;")
        self._app.Execute(f'global0 = GetVariableNumeric({bid}, "{var}", 0, 0);')
        return self._b.parse_float(self._app.Request("System", "global0+:0:0:0"))

    def set_number(self, bid, var, value):
        self._app.Execute(f'SetVariableNumeric({bid}, "{var}", {value}, 0, 0, 1);')

    def rows(self, bid, var):
        self._app.Execute("global0 = -1;")
        self._app.Execute(f'global0 = GetDialogItemInfo({bid}, "{var}", 5);')
        return int(self._b.parse_float(self._app.Request("System", "global0+:0:0:0")))

    def answerer(self, timeout_s):
        import text_prompt
        pid = text_prompt.extendsim_pid()
        if pid is None:
            raise RuntimeError("ExtendSim's process could not be found, so its prompts cannot be answered")
        return text_prompt.PromptAnswerer(text_prompt.WindowsUi(pid), timeout_s).start()

    def db_rows(self, db, table, start=0, end=None, missing_ok=False):
        """Records start..end-1 (default all). A failed read (db_table_info or db_get_records reporting
        success False) raises - never [] - so a caller's "table already has rows" guard cannot be
        bypassed by a read failure. An empty table (success True, 0 records) still returns []. With
        missing_ok, a database or table that does not exist returns None instead of raising."""
        info = self._b.db_table_info(db, table)
        if not info.get("success", True):
            if missing_ok and info.get("errorCode") in ("DATABASE_NOT_FOUND", "TABLE_NOT_FOUND"):
                return None
            raise RuntimeError(info.get("error") or "db_table_info failed")
        total = int(info.get("records") or 0)
        end = total if end is None else min(end, total)
        if end <= start:
            return []
        names = [f["name"] for f in info.get("fields", [])]
        recs_result = self._b.db_get_records(db, table, start_record=start, end_record=end,
                                             max_records=end - start)
        if not recs_result.get("success", True):
            raise RuntimeError(recs_result.get("error") or "db_get_records failed")
        recs = recs_result.get("records") or []
        return [{names[k]: r.get(f"field_{k}") for k in range(len(names))} for r in recs]

    def db_column(self, db, table, field):
        """One field of every record, in record order. Raises if the field is not in the table."""
        r = self._b.db_get_records(db, table, fields=[field], max_records=10 ** 9)
        if not r.get("success", True):
            raise RuntimeError(r.get("error") or "db_get_records failed")
        if r.get("fields") != [field]:
            raise RuntimeError(f"{table} has no field {field}")
        return [rec.get(field) for rec in r.get("records") or []]

    def db_add(self, db, table, n):
        r = self._b.db_add_records(db, table, n)
        if not r.get("success"):
            raise RuntimeError(r.get("error") or "db_add_records failed")

    def db_set(self, db, table, field, record, value):
        r = self._b.db_set_value(db, table, field, record, value)
        if not r.get("success"):
            raise RuntimeError(r.get("error") or "db_set_value failed")


def _with_ops(fn, params, ops):
    if ops is None:
        import simulation_backend as sb
        app = sb.get_extendsim_app()
        check = sb._validate_model_open(app)
        if not check.get("success"):
            return check
        ops = BackendOps(sb, app)
    try:
        return fn(ops, {k: v for k, v in params.items() if k != "modelId"})
    except Exception as e:      # a COM failure mid-sequence: report, never a success
        return _err("ANALYSIS_CONFIG_FAILED", f"stopped: {e}")


def scenario_manager_configure_command(params, ops=None):
    return _with_ops(configure_scenario_manager, params, ops)


def optimizer_configure_command(params, ops=None):
    return _with_ops(configure_optimizer, params, ops)


def analysis_manager_configure_command(params, ops=None):
    return _with_ops(configure_analysis_manager, params, ops)


def analysis_manager_results_command(params, ops=None):
    return _with_ops(lambda o, p: analysis_manager_results(o, p.get("experiment")), params, ops)
