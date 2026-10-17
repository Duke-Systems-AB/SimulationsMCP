"""Outcome of Optimizer and Scenario Manager runs.

Measured live on ExtendSim 2024/2026 (2026-10-05): ExecuteMenuCommand(6002) returns with the run already
finished - the next COM call waits until ExtendSim is free - so watching GetSimulationPhase never sees a
run in progress. Whether a run finished or was aborted therefore comes from what the blocks leave behind:
- Optimizer: its Generation counter (0 when ExtendSim stopped the run before the first generation);
- Scenario Manager: the response cells of each selected scenario (empty for a scenario that never ran).
Both also fail when one of the boxes ExtendSim showed during the run says the run was aborted, or
when a box's text could not be read (then nobody knows what it said, so the run is not confirmed).
No COM import here; callers pass the readings in, and the Scenarios table as an object with
table_rows(), table_cols(), get_cell(row, col) and set_cell(row, col, text). get_cell raises for a
cell it could not read.
"""
import dialog_report


def wait_until_idle(read_phase, sleep, now, timeout, poll=3.0):
    """True once two readings in a row say ExtendSim is idle (phase 0); False at the timeout.
    A reading that raises (COM busy between scenarios) counts as busy."""
    start = now()
    idle_in_a_row = 0
    while now() - start <= timeout:
        try:
            idle = int(read_phase() or 0) == 0
        except Exception:
            idle = False
        idle_in_a_row = idle_in_a_row + 1 if idle else 0
        if idle_in_a_row >= 2:
            return True
        sleep(poll)
    return False


def abort_texts(dialogs):
    """The texts of the boxes ExtendSim showed that say a run was aborted (e.g. "Selected block
    aborted during SIMULATE message", or a run stopped by the user)."""
    return [t for t in (dialogs or []) if isinstance(t, str) and "abort" in t.lower()]


def unreadable_boxes(dialogs):
    """The entries for boxes ExtendSim showed whose text could not be read."""
    return [t for t in (dialogs or []) if t == dialog_report.UNREADABLE_BOX]


def unconfirmed_run_message(dialogs, what):
    """Why the boxes ExtendSim showed during a run keep it from counting as finished, or None.
    what: "optimization" or "scenario run"."""
    if abort_texts(dialogs):
        return f"ExtendSim aborted the {what} - ExtendSim's own messages are in dialogs"
    if unreadable_boxes(dialogs):
        return (f"ExtendSim showed a message box during the {what} whose text could not be read, so it "
                f"cannot be confirmed that the {what} finished; the other boxes' texts are in dialogs")
    return None


def optimizer_outcome(results, dialogs=()):
    """results: the Optimizer block's readings (Generation, Cost, Convergence); dialogs: the texts of
    the boxes ExtendSim showed during the run. Generations completed before an error box or a Stop
    are the results of an unfinished optimization, so an abort text makes the run a failure."""
    gen = results.get("Generation")
    if not gen or gen <= 0:
        return {"ok": False,
                "message": "ExtendSim stopped the optimization before the first generation completed "
                           "(an error in the model or the optimizer settings - ExtendSim's own messages are in dialogs)"}
    if abort_texts(dialogs):
        return {"ok": False,
                "message": f"ExtendSim aborted the optimization after {int(gen)} generation(s), so the best "
                           "value is not the result of a finished optimization - ExtendSim's own messages are "
                           "in dialogs"}
    if unreadable_boxes(dialogs):
        return {"ok": False,
                "message": f"ExtendSim showed a message box during the optimization whose text could not be "
                           f"read, so the {int(gen)} generation(s) cannot be confirmed as a finished "
                           "optimization; the other boxes' texts are in dialogs"}
    return {"ok": True, "generations": int(gen), "best": results.get("Cost"),
            "convergence": results.get("Convergence")}


def scenarios_without_results(scenarios, selected, n_inputs, n_responses):
    """Names of selected scenarios with an empty response cell. None when there are no responses to check.
    scenarios: rows as read from Scenarios_tbl (values = factor cells then response cells; trailing empty
    cells may be trimmed)."""
    if n_responses <= 0:
        return None
    missing = []
    for i in selected:
        row = scenarios[i] if i < len(scenarios) else {}
        values = row.get("values") or []
        cells = [values[n_inputs + r] if n_inputs + r < len(values) else None for r in range(n_responses)]
        if "error" in row or any(c is None or c == "" for c in cells):
            missing.append(row.get("name") or f"scenario {i + 1}")
    return missing


def response_cells(selected, n_inputs, n_responses):
    """(row, column) of every response cell of the selected scenarios in Scenarios_tbl
    (column 0 = select, 1 = name, then the input columns, then the responses). Emptied before a run
    so that only results written by this run count (measured 2026-10-05: an aborted rerun left the
    previous run's results in place)."""
    return [(row, 2 + n_inputs + r) for row in selected for r in range(n_responses)]


def scenario_outcome(results, selected, n_inputs, n_responses, dialogs, last_scenario):
    """Outcome of a checked Scenario Manager run: every selected scenario needs every response, and no
    box may say the run was aborted (an abort after the last results were written still means the
    run did not finish cleanly)."""
    missing = scenarios_without_results(results, selected, n_inputs, n_responses) or []
    counter = last_scenario or "unknown"
    if missing:
        return {"ok": False, "scenariosWithoutResults": missing,
                "message": f"{len(missing)} of {len(selected)} scenarios have no results - ExtendSim stopped "
                           f"the run (scenario counter {counter}); ExtendSim's own messages are in dialogs"}
    if abort_texts(dialogs):
        return {"ok": False, "scenariosWithoutResults": [],
                "message": f"ExtendSim reported the run aborted (scenario counter {counter}), so the results "
                           "cannot be trusted although every selected scenario has values; ExtendSim's own "
                           "messages are in dialogs"}
    if unreadable_boxes(dialogs):
        return {"ok": False, "scenariosWithoutResults": [],
                "message": f"ExtendSim showed a message box during the run whose text could not be read "
                           f"(scenario counter {counter}), so the run cannot be confirmed as finished although "
                           "every selected scenario has values; the other boxes' texts are in dialogs"}
    return {"ok": True}


def analysis_block_conflict(block_names):
    """A refusal text when the model holds both a Scenario Manager and an Optimizer, else None.
    Both runs start with ExecuteMenuCommand(6002), and ExtendSim decides which block it runs; with
    both present the other block may run and its old results would be read as this run's."""
    names = set(block_names or [])
    if {"Scenario Manager", "Optimizer"} <= names:
        return ("The model holds both a Scenario Manager and an Optimizer. ExtendSim starts both with the "
                "same command and picks the block itself, so the run could start the other one and old "
                "results would be reported; nothing was run. Remove (or move to another model) the block "
                "you do not want to run.")
    return None


def is_placeholder_table(n_rows, read_cell, n_inputs, n_responses, n_cols):
    """True when Scenarios_tbl reports one row whose name, input and response cells are all blank:
    the table of a Scenario Manager without scenarios, not a scenario. read_cell(row, col) -> str.
    Only the name column and the layout's columns that the table really has (n_cols, its own column
    count) are read: an out-of-range read pops a modal box that blocks COM, and an empty table can
    have fewer columns than the layout (measured on ExtendSim 2024: one factor and one response, no
    scenarios -> 2 columns). With an unknown layout or column count, or a cell that cannot be read,
    it is not a placeholder."""
    if n_rows != 1 or n_inputs is None or n_responses is None or n_cols is None:
        return False
    try:
        for col in range(1, min(2 + n_inputs + n_responses, int(n_cols))):
            if str(read_cell(0, col) or "").strip():
                return False
    except Exception:
        return False
    return True


def clear_previous_results(table, selected, layout):
    """Empty the selected scenarios' response cells before a checked run and read them back, so that
    only results written by this run count. None when cleared; otherwise a refusal
    {code, message, ...} - and nothing is written unless the layout and the selection are known
    to fit the table (rows and columns). layout: (input columns, response columns) or None when
    unknown. selected: the selected rows; empty when the selection could not be read - then the
    results cannot be told apart from the previous run's, so the run is refused."""
    if not selected:
        return {"code": "OPTIMIZER_FAILED",
                "message": "Could not read which scenarios are selected in the Scenario Manager, so the run's "
                           "results could not be told apart from the previous run's; nothing was run.",
                "suggestion": "Check the selection on the Scenarios tab, or run with waitForCompletion=false "
                              "to run without the check."}
    if layout is None:
        return {"code": "OPTIMIZER_FAILED",
                "message": "Could not read the Scenario Manager's factor and response counts, so the run's "
                           "results could not be checked; nothing was run.",
                "suggestion": "Run with waitForCompletion=false to run without the check."}
    n_inputs, n_responses = layout
    if n_responses == 0:
        return {"code": "OPTIMIZER_FAILED",
                "message": "The Scenario Manager has no block responses, so whether the scenarios ran could "
                           "not be checked; nothing was run.",
                "suggestion": "Add a block response, or run with waitForCompletion=false to run without "
                              "the check."}
    try:
        rows = int(table.table_rows())
    except Exception:
        rows = -1
    if rows < 0 or any(r < 0 or r >= rows for r in selected):
        shown = f"{rows} rows" if rows >= 0 else "row count unreadable"
        return {"code": "OPTIMIZER_FAILED",
                "message": f"The selected scenarios do not match the Scenarios table ({shown}), so the "
                           "results could not be checked; nothing was run."}
    try:
        cols = int(table.table_cols())
    except Exception:
        cols = -1
    needed = 2 + n_inputs + n_responses
    if cols < needed:
        shown = f"{cols} columns" if cols >= 0 else "column count unreadable"
        return {"code": "OPTIMIZER_FAILED",
                "message": f"The Scenarios table ({shown}) does not have the {needed} columns its factors and "
                           "responses need, so the results could not be checked; nothing was run."}
    cells = response_cells(selected, n_inputs, n_responses)
    for row, col in cells:
        table.set_cell(row, col, "")
    for row, col in cells:
        try:
            left = str(table.get_cell(row, col) or "").strip()
            reason = "a cell kept its value" if left else None
        except Exception:
            reason = "a cell could not be read back"
        if reason:
            return {"code": "SET_VALUE_FAILED",
                    "message": f"Could not clear the previous results before the run ({reason}); nothing "
                               "was run.",
                    "row": row, "column": col}
    return None
