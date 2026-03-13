"""
Live COM tests for v1.16.2 _set_var/_get_var suffix routing.

Verifies SetVariableNumeric actually writes/reads correct values against ExtendSim.
Requires a running ExtendSim instance.
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from conftest import add_block

from simulation_backend import _set_var, _get_var, _set_var_string, parse_float


class TestSetVariableNumericRoundTrip:
    """Verify SetVariableNumeric writes real values that read back correctly."""

    def test_set_get_prm_via_variable_numeric(self, fresh_model):
        """Set WaitDelta_prm via SetVariableNumeric, read back → verify match."""
        app = fresh_model
        block_id = add_block(app, "Item.lbr", "Activity")

        # Write directly via SetVariableNumeric
        app.Execute(f'SetVariableNumeric({block_id}, "WaitDelta_prm", 42.5, 0, 0, 1);')
        # Read back via GetVariableNumeric
        app.Execute(f'global0 = GetVariableNumeric({block_id}, "WaitDelta_prm", 0, 0);')
        value = parse_float(app.Request("System", "global0+:0:0:0"))

        assert value == 42.5, f"Expected 42.5 but got {value}"

    def test_variable_numeric_vs_dialog_variable_differ(self, fresh_model):
        """SetVariableNumeric writes real value; GetDialogVariable reads shadow → may differ."""
        app = fresh_model
        block_id = add_block(app, "Item.lbr", "Activity")

        # Write via SetVariableNumeric (real variable)
        app.Execute(f'SetVariableNumeric({block_id}, "WaitDelta_prm", 99.0, 0, 0, 1);')

        # Read via GetVariableNumeric (real)
        app.Execute(f'global0 = GetVariableNumeric({block_id}, "WaitDelta_prm", 0, 0);')
        real_value = parse_float(app.Request("System", "global0+:0:0:0"))

        # Read via GetDialogVariable (shadow)
        app.Execute(f'globalStr0 = GetDialogVariable({block_id}, "WaitDelta_prm", 0, 0);')
        shadow_value = parse_float(app.Request("System", "globalStr0+:0:0:0"))

        # Real value should be what we set
        assert real_value == 99.0, f"GetVariableNumeric should return 99.0, got {real_value}"
        # Shadow may or may not match — this test documents the behavior
        # If they differ, it proves the two APIs access different storage
        if shadow_value != real_value:
            # This is the expected behavior that motivated v1.16.2
            pass
        # Either way, GetVariableNumeric must return the correct value
        assert real_value == 99.0

    def test_popup_via_variable_numeric(self, fresh_model):
        """Set Delay_Options_pop via SetVariableNumeric with msg=1 → popup takes effect."""
        app = fresh_model
        block_id = add_block(app, "Item.lbr", "Activity")

        # Set delay type to distribution (index 4)
        app.Execute(f'SetVariableNumeric({block_id}, "Delay_Options_pop", 4, 0, 0, 1);')

        # Read back
        app.Execute(f'global0 = GetVariableNumeric({block_id}, "Delay_Options_pop", 0, 0);')
        value = int(parse_float(app.Request("System", "global0+:0:0:0")))

        assert value == 4, f"Expected popup index 4 (distribution), got {value}"


class TestGetVariableNumericStats:
    """After simulation, verify stats are readable via GetVariableNumeric."""

    def test_activity_utilization_after_run(self, fresh_model):
        """Build Create→Queue→Activity→Exit, run, read utilization stats."""
        app = fresh_model

        create_id = add_block(app, "Item.lbr", "Create", "TestCreate")
        queue_id = add_block(app, "Item.lbr", "Queue", "TestQueue")
        activity_id = add_block(app, "Item.lbr", "Activity", "TestActivity")
        exit_id = add_block(app, "Item.lbr", "Exit", "TestExit")

        # Connect: Create → Queue → Activity → Exit
        app.Execute(f"MakeConnection({create_id}, 0, {queue_id}, 0);")
        app.Execute(f"MakeConnection({queue_id}, 0, {activity_id}, 0);")
        app.Execute(f"MakeConnection({activity_id}, 0, {exit_id}, 0);")

        # Configure Create with constant interarrival time
        _set_var(app, create_id, "CreateOptions_pop", 0)  # constant arrivals
        _set_var(app, create_id, "CInterval_prm", 2.0)    # every 2 time units

        # Configure Activity with fixed delay
        _set_var(app, activity_id, "Delay_Options_pop", 1)  # fixed delay
        _set_var(app, activity_id, "WaitDelta_prm", 1.0)    # 1 time unit

        # Run simulation
        app.Execute("SetRunParameter(0, 0);")    # start time
        app.Execute("SetRunParameter(1, 100);")  # end time
        app.Execute("RunSimulation();")

        # Read utilization via GetVariableNumeric
        app.Execute(f'global0 = GetVariableNumeric({activity_id}, "Utilization_prm", 0, 0);')
        utilization = parse_float(app.Request("System", "global0+:0:0:0"))

        assert utilization > 0, f"Utilization should be > 0 after simulation, got {utilization}"

    def test_exit_total_after_run(self, fresh_model):
        """Build Create→Exit, run, verify TotalExited_prm > 0."""
        app = fresh_model

        create_id = add_block(app, "Item.lbr", "Create", "TestCreate")
        exit_id = add_block(app, "Item.lbr", "Exit", "TestExit")

        app.Execute(f"MakeConnection({create_id}, 0, {exit_id}, 0);")

        _set_var(app, create_id, "CreateOptions_pop", 0)
        _set_var(app, create_id, "CInterval_prm", 1.0)

        app.Execute("SetRunParameter(0, 0);")
        app.Execute("SetRunParameter(1, 50);")
        app.Execute("RunSimulation();")

        app.Execute(f'global0 = GetVariableNumeric({exit_id}, "TotalExited_prm", 0, 0);')
        total = parse_float(app.Request("System", "global0+:0:0:0"))

        assert total > 0, f"TotalExited_prm should be > 0, got {total}"

    def test_queue_average_wait_after_run(self, fresh_model):
        """Build Create→Queue→Activity→Exit, run, read AverageWait_prm."""
        app = fresh_model

        create_id = add_block(app, "Item.lbr", "Create")
        queue_id = add_block(app, "Item.lbr", "Queue")
        activity_id = add_block(app, "Item.lbr", "Activity")
        exit_id = add_block(app, "Item.lbr", "Exit")

        app.Execute(f"MakeConnection({create_id}, 0, {queue_id}, 0);")
        app.Execute(f"MakeConnection({queue_id}, 0, {activity_id}, 0);")
        app.Execute(f"MakeConnection({activity_id}, 0, {exit_id}, 0);")

        _set_var(app, create_id, "CreateOptions_pop", 0)
        _set_var(app, create_id, "CInterval_prm", 1.0)
        _set_var(app, activity_id, "Delay_Options_pop", 1)
        _set_var(app, activity_id, "WaitDelta_prm", 2.0)  # Slow processing → queue builds

        app.Execute("SetRunParameter(0, 0);")
        app.Execute("SetRunParameter(1, 100);")
        app.Execute("RunSimulation();")

        app.Execute(f'global0 = GetVariableNumeric({queue_id}, "AverageWait_prm", 0, 0);')
        avg_wait = parse_float(app.Request("System", "global0+:0:0:0"))

        assert avg_wait >= 0, f"AverageWait_prm should be >= 0, got {avg_wait}"


class TestSetVarHelper:
    """Verify _set_var/_get_var helpers route correctly."""

    def test_set_get_prm_roundtrip(self, fresh_model):
        """_set_var with _prm suffix → SetVariableNumeric, _get_var reads back."""
        app = fresh_model
        block_id = add_block(app, "Item.lbr", "Activity")

        _set_var(app, block_id, "WaitDelta_prm", 42.0)
        raw = _get_var(app, block_id, "WaitDelta_prm")
        value = parse_float(raw)

        assert value == 42.0, f"Expected 42.0 but got {value}"

    def test_set_get_pop_roundtrip(self, fresh_model):
        """_set_var with _pop suffix → SetVariableNumeric, read back matches."""
        app = fresh_model
        block_id = add_block(app, "Item.lbr", "Activity")

        _set_var(app, block_id, "Delay_Options_pop", 4)  # distribution
        raw = _get_var(app, block_id, "Delay_Options_pop")
        value = int(parse_float(raw))

        assert value == 4, f"Expected 4 but got {value}"

    def test_set_get_dtxt_roundtrip(self, fresh_model):
        """_set_var with _dtxt suffix → SetDialogVariable for text variable."""
        app = fresh_model
        block_id = add_block(app, "Value.lbr", "Equation(I)")

        _set_var(app, block_id, "Equation_dtxt", "o0 = i0 * 2")
        raw = _get_var(app, block_id, "Equation_dtxt")
        value = raw.strip()

        assert value == "o0 = i0 * 2", f"Expected 'o0 = i0 * 2' but got '{value}'"


class TestSetVarStringHelper:
    """Verify _set_var_string always uses SetDialogVariable for string values."""

    def test_set_attribute_name_on_set_block(self, fresh_model):
        """_set_var_string writes a string attribute name, reads back correctly."""
        app = fresh_model
        block_id = add_block(app, "Item.lbr", "Set(I)")

        _set_var_string(app, block_id, "AttributeName_dtxt", "priority")
        raw = _get_var(app, block_id, "AttributeName_dtxt")
        value = raw.strip()

        assert value == "priority", f"Expected 'priority' but got '{value}'"

    def test_set_pool_name_on_resource_pool(self, fresh_model):
        """_set_var_string writes a pool name string on Resource Pool block."""
        app = fresh_model
        block_id = add_block(app, "Item.lbr", "Resource Pool")

        _set_var_string(app, block_id, "PoolName_dtxt", "TestPool")
        raw = _get_var(app, block_id, "PoolName_dtxt")
        value = raw.strip()

        assert value == "TestPool", f"Expected 'TestPool' but got '{value}'"
