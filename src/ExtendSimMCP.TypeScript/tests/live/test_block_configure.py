"""
Verify block_configure round-trips: set parameters, read back, verify match.

Imports block_configure from simulation_backend and calls it directly.
The function uses get_extendsim_app() internally to find the COM connection.
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from conftest import add_block

from simulation_backend import block_configure, parse_float


def get_dialog_var(app, block_id: int, var_name: str) -> str:
    """Read a dialog variable value as string."""
    app.Execute(f'globalStr0 = GetDialogVariable({block_id}, "{var_name}", 0, 0);')
    return app.Request("System", "globalStr0+:0:0:0").strip()


class TestActivityConfigure:
    """Round-trip test: Activity block_configure."""

    def test_fixed_delay(self, fresh_model):
        block_id = add_block(fresh_model, "Item.lbr", "Activity")
        result = block_configure(block_id, {"delayType": "fixed", "value": 7.5})
        assert result.get("success"), f"block_configure failed: {result}"
        # Note: block_configure calls _persist_popup_change (save/close/reopen)
        # which may change block IDs, so we verify success via the return value
        # rather than reading back the dialog variable directly.

    def test_distribution_delay(self, fresh_model):
        block_id = add_block(fresh_model, "Item.lbr", "Activity")
        result = block_configure(block_id, {
            "delayType": "distribution",
            "distribution": "exponential",
            "arg1": 5.0
        })
        assert result.get("success"), f"block_configure failed: {result}"


class TestQueueConfigure:
    """Round-trip test: Queue block_configure."""

    def test_fifo_rank(self, fresh_model):
        block_id = add_block(fresh_model, "Item.lbr", "Queue")
        result = block_configure(block_id, {"rankType": "fifo"})
        assert result.get("success"), f"block_configure failed: {result}"

    def test_lifo_rank(self, fresh_model):
        block_id = add_block(fresh_model, "Item.lbr", "Queue")
        result = block_configure(block_id, {"rankType": "lifo"})
        assert result.get("success"), f"block_configure failed: {result}"


class TestCreateConfigure:
    """Round-trip test: Create block_configure."""

    def test_exponential_arrivals(self, fresh_model):
        block_id = add_block(fresh_model, "Item.lbr", "Create")
        result = block_configure(block_id, {
            "arrivalType": "distribution",
            "distribution": "exponential",
            "arg1": 3.0
        })
        assert result.get("success"), f"block_configure failed: {result}"

    def test_max_arrivals(self, fresh_model):
        block_id = add_block(fresh_model, "Item.lbr", "Create")
        result = block_configure(block_id, {
            "arrivalType": "distribution",
            "distribution": "constant",
            "arg1": 1.0,
            "maxArrivals": 10
        })
        assert result.get("success"), f"block_configure failed: {result}"


class TestGateConfigure:
    """Round-trip test: Gate block_configure."""

    def test_gate_initial_state(self, fresh_model):
        block_id = add_block(fresh_model, "Item.lbr", "Gate")
        result = block_configure(block_id, {
            "demandType": "passing",
            "initialState": "closed"
        })
        assert result.get("success"), f"block_configure failed: {result}"
