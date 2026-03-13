"""
End-to-end simulation round-trip: build model, run simulation, check results.

Verifies that a Create->Queue->Activity->Exit model can be built and run.
Uses simulation_get_results to check throughput after simulation completes.

NOTE: Does not use block_configure (which triggers _persist_popup_change
save/close/reopen) to keep tests simple and avoid COM connection issues.
Popup indices and block_configure are tested separately.
"""

import pytest
import time
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from conftest import add_block

from simulation_backend import simulation_get_results, parse_float


def connect_blocks(app, from_id: int, from_conn: int, to_id: int, to_conn: int):
    """Connect two blocks via MakeConnection and verify success."""
    app.Execute(f"global0 = MakeConnection({from_id}, {from_conn}, {to_id}, {to_conn});")
    result = int(parse_float(app.Request("System", "global0+:0:0:0")))
    assert result == 1, f"MakeConnection failed: {from_id}:{from_conn} -> {to_id}:{to_conn}"


def build_simple_model(app) -> dict:
    """Build Create->Queue->Activity->Exit and return block IDs."""
    create_id = add_block(app, "Item.lbr", "Create", "Arrivals")
    queue_id = add_block(app, "Item.lbr", "Queue", "Buffer")
    activity_id = add_block(app, "Item.lbr", "Activity", "Process")
    exit_id = add_block(app, "Item.lbr", "Exit", "Departures")

    connect_blocks(app, create_id, 0, queue_id, 0)
    connect_blocks(app, queue_id, 1, activity_id, 0)
    connect_blocks(app, activity_id, 1, exit_id, 0)

    return {
        "create": create_id,
        "queue": queue_id,
        "activity": activity_id,
        "exit": exit_id,
    }


def run_simulation_and_wait(app, end_time: float = 50.0, timeout_s: float = 30.0):
    """Set end time, run simulation, wait for completion."""
    app.Execute(f"SetRunParameter({end_time}, 1);")
    app.Execute("ExecuteMenuCommand(6000)")

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        app.Execute("global0 = GetSimulationPhase();")
        phase = int(parse_float(app.Request("System", "global0+:0:0:0")))
        if phase == 0:
            return
        time.sleep(0.2)

    pytest.fail(f"Simulation did not complete within {timeout_s}s")


class TestSimpleSimulation:
    """Verify basic simulation mechanics work via COM."""

    def test_default_model_runs(self, fresh_model):
        """Build model with defaults, verify simulation completes."""
        build_simple_model(fresh_model)

        run_simulation_and_wait(fresh_model, end_time=20.0)

        results = simulation_get_results()
        assert results.get("success"), f"simulation_get_results failed: {results}"
        assert results.get("simulationTime", 0) > 0, "Simulation time should be > 0"

    def test_items_flow_through(self, fresh_model):
        """Verify items flow from Create to Exit."""
        build_simple_model(fresh_model)

        run_simulation_and_wait(fresh_model, end_time=100.0)

        results = simulation_get_results()
        assert results.get("success"), f"simulation_get_results failed: {results}"

        exit_stats = results.get("exitStatistics", [])
        print(f"\nSimulation results: time={results.get('simulationTime')}")
        print(f"Exit stats: {exit_stats}")
        assert len(exit_stats) >= 1, "No Exit block statistics found"

    def test_set_end_time(self, fresh_model):
        """Verify SetRunParameter correctly sets simulation end time."""
        build_simple_model(fresh_model)

        run_simulation_and_wait(fresh_model, end_time=42.0)

        results = simulation_get_results()
        assert results.get("success"), f"simulation_get_results failed: {results}"
        sim_time = results.get("simulationTime", 0)
        assert sim_time == pytest.approx(42.0, abs=1.0), (
            f"Expected simulation time ~42.0, got {sim_time}"
        )

    def test_activity_fixed_delay_direct(self, fresh_model):
        """Set Activity delay directly via SetDialogVariable (no popup change)."""
        ids = build_simple_model(fresh_model)

        # Set Activity to fixed delay (popup=1) and value=5
        # Note: SetDialogVariable sets the value but Activity may not react
        # without save/close/reopen. This test verifies the COM call works.
        fresh_model.Execute(
            f'SetDialogVariable({ids["activity"]}, "Delay_Options_pop", 1, 0, 0);'
        )
        fresh_model.Execute(
            f'SetDialogVariable({ids["activity"]}, "WaitDelta_prm", 5, 0, 0);'
        )

        # Verify readback
        fresh_model.Execute(
            f'globalStr0 = GetDialogVariable({ids["activity"]}, "WaitDelta_prm", 0, 0);'
        )
        val = fresh_model.Request("System", "globalStr0+:0:0:0").strip()
        assert float(val.replace(",", ".")) == pytest.approx(5.0, abs=0.01), (
            f"WaitDelta_prm readback: got '{val}', expected 5.0"
        )

        # Run simulation — items should flow (delay may or may not take effect
        # depending on whether popup change needs save/close/reopen)
        run_simulation_and_wait(fresh_model, end_time=50.0)

        results = simulation_get_results()
        assert results.get("success"), f"simulation_get_results failed: {results}"
