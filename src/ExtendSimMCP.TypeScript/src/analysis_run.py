"""Outcome of Optimizer and Scenario Manager runs.

Measured live on ExtendSim 2024/2026 (2026-10-05): ExecuteMenuCommand(6002) returns with the run already
finished - the next COM call waits until ExtendSim is free - so watching GetSimulationPhase never sees a
run in progress. Whether a run finished or was aborted therefore comes from what the blocks leave behind:
- Optimizer: its Generation counter (0 when ExtendSim stopped the run before the first generation);
- Scenario Manager: the response cells of each selected scenario (empty for a scenario that never ran).
No COM import here; callers pass the readings in.
"""


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


def optimizer_outcome(results):
    """results: the Optimizer block's readings (Generation, Cost, Convergence)."""
    gen = results.get("Generation")
    if not gen or gen <= 0:
        return {"ok": False,
                "message": "ExtendSim stopped the optimization before the first generation completed "
                           "(an error in the model or the optimizer settings - ExtendSim's own messages are in dialogs)"}
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
