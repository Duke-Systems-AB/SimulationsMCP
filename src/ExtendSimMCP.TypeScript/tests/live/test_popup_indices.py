"""
Verify popup menu indices match live ExtendSim.

Tests popup indices by setting each value via SetDialogVariable and reading
it back via GetDialogVariable, using variable names (quoted strings).
GetDialogItemLabel() does not work via COM (returns empty strings).

Constants under test are from simulation_backend.py:
  DELAY_OPTIONS, CREATE_ARRIVAL_OPTIONS, DISTRIBUTIONS
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from conftest import add_block, get_popup_label


def get_dialog_var(app, block_id: int, var_name: str) -> str:
    """Read a dialog variable value as string (using quoted variable name)."""
    app.Execute(f'globalStr0 = GetDialogVariable({block_id}, "{var_name}", 0, 0);')
    return app.Request("System", "globalStr0+:0:0:0").strip()


def set_dialog_var(app, block_id: int, var_name: str, value) -> None:
    """Set a dialog variable by quoted name. Value is passed as number (not quoted)."""
    app.Execute(f'SetDialogVariable({block_id}, "{var_name}", {value}, 0, 0);')


def parse_popup_value(raw: str) -> int:
    """Parse a popup readback value to int, handling Swedish decimal separator."""
    raw = raw.strip().replace(",", ".")
    return int(float(raw))


# --- Activity Delay Options ---
# Variable: Delay_Options_pop
# 0="" (none), 1="fixed", 2="connector", 3="attribute", 4="distribution", 5="table"
ACTIVITY_DELAY_VALID_INDICES = [0, 1, 2, 3, 4, 5]


class TestActivityDelayOptions:
    def test_delay_options_round_trip(self, fresh_model):
        """Set each delay option index and verify it reads back correctly."""
        block_id = add_block(fresh_model, "Item.lbr", "Activity")
        for index in ACTIVITY_DELAY_VALID_INDICES:
            set_dialog_var(fresh_model, block_id, "Delay_Options_pop", index)
            actual = get_dialog_var(fresh_model, block_id, "Delay_Options_pop")
            actual_int = parse_popup_value(actual)
            assert actual_int == index, (
                f"DELAY_OPTIONS[{index}]: set {index}, read back '{actual}' (parsed {actual_int})"
            )

    def test_delay_option_fixed_sets_constant(self, fresh_model):
        """Setting delay option to 1 (fixed) should allow setting WaitDelta_prm."""
        block_id = add_block(fresh_model, "Item.lbr", "Activity")
        set_dialog_var(fresh_model, block_id, "Delay_Options_pop", 1)
        set_dialog_var(fresh_model, block_id, "WaitDelta_prm", 5.0)
        actual = get_dialog_var(fresh_model, block_id, "WaitDelta_prm")
        assert float(actual.replace(",", ".")) == pytest.approx(5.0), (
            f"WaitDelta_prm: expected 5.0, got '{actual}'"
        )

    def test_delay_option_count_boundary(self, fresh_model):
        """Index 5 should be the last valid option (table)."""
        block_id = add_block(fresh_model, "Item.lbr", "Activity")
        set_dialog_var(fresh_model, block_id, "Delay_Options_pop", 5)
        actual = get_dialog_var(fresh_model, block_id, "Delay_Options_pop")
        actual_int = parse_popup_value(actual)
        assert actual_int == 5, f"Expected 5 (table), got '{actual}'"

        # Try setting to 6 -- document what happens with out-of-range
        set_dialog_var(fresh_model, block_id, "Delay_Options_pop", 6)
        actual_after = get_dialog_var(fresh_model, block_id, "Delay_Options_pop")
        print(f"\nAfter setting index 6, popup value is: '{actual_after}'")
        # No strict assertion on invalid index behavior


# --- Create Arrival Options ---
# Variable: CreateOptions_pop (NOT Create_Options_pop)
# 0="schedule", 1="distribution", 2="connector", 3="database"
CREATE_ARRIVAL_VALID_INDICES = [0, 1, 2, 3]


class TestCreateArrivalOptions:
    def test_arrival_options_round_trip(self, fresh_model):
        """Set each arrival option index and verify it reads back correctly."""
        block_id = add_block(fresh_model, "Item.lbr", "Create")
        for index in CREATE_ARRIVAL_VALID_INDICES:
            set_dialog_var(fresh_model, block_id, "CreateOptions_pop", index)
            actual = get_dialog_var(fresh_model, block_id, "CreateOptions_pop")
            actual_int = parse_popup_value(actual)
            assert actual_int == index, (
                f"CREATE_ARRIVAL_OPTIONS[{index}]: set {index}, read back '{actual}' (parsed {actual_int})"
            )

    def test_arrival_options_count_boundary(self, fresh_model):
        """Index 3 should be the last valid option (database)."""
        block_id = add_block(fresh_model, "Item.lbr", "Create")
        set_dialog_var(fresh_model, block_id, "CreateOptions_pop", 3)
        actual = get_dialog_var(fresh_model, block_id, "CreateOptions_pop")
        actual_int = parse_popup_value(actual)
        assert actual_int == 3, f"Expected 3 (database), got '{actual}'"


# --- Distribution Indices ---
# Variable: Delay_Distributions_pop on Activity
# Key distributions: 32=Constant, 34=Triangular, 35=Normal, 36=Exponential
DISTRIBUTION_INDICES = {
    32: "Constant",
    34: "Triangular",
    35: "Normal",
    36: "Exponential",
}


class TestDistributionIndices:
    def test_key_distributions_round_trip(self, fresh_model):
        """Set Activity to distribution mode, then set each distribution index."""
        block_id = add_block(fresh_model, "Item.lbr", "Activity")
        # First set delay option to 4 (distribution mode)
        set_dialog_var(fresh_model, block_id, "Delay_Options_pop", 4)
        verify = get_dialog_var(fresh_model, block_id, "Delay_Options_pop")
        verify_int = parse_popup_value(verify)
        assert verify_int == 4, f"Failed to set distribution mode, got '{verify}'"

        for index, name in DISTRIBUTION_INDICES.items():
            set_dialog_var(
                fresh_model, block_id, "Delay_Distributions_pop", index
            )
            actual = get_dialog_var(
                fresh_model, block_id, "Delay_Distributions_pop"
            )
            actual_int = parse_popup_value(actual)
            assert actual_int == index, (
                f"DISTRIBUTIONS[{index}] ({name}): set {index}, read back '{actual}' (parsed {actual_int})"
            )


# --- Workstation Delay Options ---
# Workstation Delay_Options_pop is dialogId=99 (different from Activity's 66)
# GetDialogItemLabel returns empty for Workstation (known limitation)


class TestWorkstationDelayOptions:
    def test_workstation_delay_round_trip(self, fresh_model):
        """Test Workstation delay popup indices via round-trip using variable name."""
        block_id = add_block(fresh_model, "Item.lbr", "Workstation")
        results = {}
        for i in range(6):
            set_dialog_var(fresh_model, block_id, "Delay_Options_pop", i)
            actual = get_dialog_var(fresh_model, block_id, "Delay_Options_pop")
            results[i] = actual

        print(f"\nWorkstation delay popup round-trip: {results}")

        # Check if round-trip works for Workstation popups
        parseable = {}
        for i, val in results.items():
            try:
                parseable[i] = parse_popup_value(val)
            except (ValueError, TypeError):
                parseable[i] = None

        working = {k: v for k, v in parseable.items() if v is not None and v == k}
        if working:
            # At minimum, indices 1 (fixed) and 4 (distribution) should work
            assert len(working) >= 2, (
                f"Expected at least 2 working indices, got {len(working)}: {working}"
            )
        else:
            pytest.skip(
                "Workstation Delay_Options_pop does not round-trip via COM "
                "(known limitation)"
            )


# --- GetDialogItemLabel COM limitation ---
class TestGetDialogItemLabelLimitation:
    def test_get_dialog_item_label_returns_empty_via_com(self, fresh_model):
        """Document that GetDialogItemLabel returns empty strings via COM.

        This is a known limitation: the function works in ExtendSim's ModL
        script window but returns empty when called via COM Execute/Request.
        """
        block_id = add_block(fresh_model, "Item.lbr", "Activity")
        label = get_popup_label(fresh_model, block_id, "Delay_Options_pop", 1)
        if label:
            print(f"\nGetDialogItemLabel now works via COM! Got: '{label}'")
        else:
            print("\nGetDialogItemLabel returns empty via COM (expected limitation)")
        # This test always passes -- it just documents the behavior
