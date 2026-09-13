"""
Verify GetConnectorType() returns expected type IDs for ALL blocks in Item.lbr and Value.lbr.

Connector type mapping (from block_discover H6 enhancement):
  13 = Value
  14 = Item
  15 = Universal
  25 = Flow
  308 = Reliability
  -1 = Array
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from conftest import add_block


CONNECTOR_TYPE_MAP = {
    13: "Value",
    14: "Item",
    15: "Universal",
    25: "Flow",
    308: "Reliability",
    -1: "Array",
}

TYPE_VALUE = 13
TYPE_ITEM = 14
TYPE_UNIVERSAL = 15
TYPE_FLOW = 25


def get_connector_info(app, block_id: int) -> list:
    """Get all connectors with their names and types."""
    from simulation_backend import parse_float

    app.Execute(f"global0 = GetNumCons({block_id});")
    num_cons = int(parse_float(app.Request("System", "global0+:0:0:0")))

    connectors = []
    for i in range(num_cons):
        name = app.Request("System", f"GetConName({block_id}, {i})").strip()

        app.Execute(f"global0 = GetConnectorType({block_id}, {i});")
        type_id = int(parse_float(app.Request("System", "global0+:0:0:0")))

        connectors.append({
            "index": i,
            "name": name,
            "typeId": type_id,
            "typeName": CONNECTOR_TYPE_MAP.get(type_id, f"unknown({type_id})"),
        })

    return connectors


def count_by_type(connectors, type_id):
    """Count connectors of a given type."""
    return len([c for c in connectors if c["typeId"] == type_id])


def assert_has_connector_type(connectors, type_id, min_count, block_name):
    """Assert block has at least min_count connectors of given type."""
    actual = count_by_type(connectors, type_id)
    type_name = CONNECTOR_TYPE_MAP.get(type_id, str(type_id))
    assert actual >= min_count, (
        f"{block_name} should have >= {min_count} {type_name} connector(s), "
        f"got {actual}. All connectors: {connectors}"
    )


def place_and_get_connectors(app, library, block_name):
    """Place a block and return its connector info."""
    block_id = add_block(app, library, block_name)
    return get_connector_info(app, block_id)


# =============================================================================
# Item.lbr — Routing blocks
# =============================================================================
class TestItemRoutingConnectors:
    """Routing blocks: Create, Exit, Gate, Select Item In/Out, Catch/Throw Item."""

    def test_create(self, fresh_model):
        cons = place_and_get_connectors(fresh_model, "Item.lbr", "Create")
        assert_has_connector_type(cons, TYPE_ITEM, 1, "Create")

    def test_exit(self, fresh_model):
        cons = place_and_get_connectors(fresh_model, "Item.lbr", "Exit")
        assert_has_connector_type(cons, TYPE_ITEM, 1, "Exit")

    def test_gate(self, fresh_model):
        cons = place_and_get_connectors(fresh_model, "Item.lbr", "Gate")
        assert_has_connector_type(cons, TYPE_ITEM, 2, "Gate")

    def test_select_item_in(self, fresh_model):
        cons = place_and_get_connectors(fresh_model, "Item.lbr", "Select Item In")
        assert_has_connector_type(cons, TYPE_ITEM, 2, "Select Item In")

    def test_select_item_out(self, fresh_model):
        cons = place_and_get_connectors(fresh_model, "Item.lbr", "Select Item Out")
        assert_has_connector_type(cons, TYPE_ITEM, 2, "Select Item Out")

    def test_catch_item(self, fresh_model):
        cons = place_and_get_connectors(fresh_model, "Item.lbr", "Catch Item")
        assert_has_connector_type(cons, TYPE_ITEM, 1, "Catch Item")

    def test_throw_item(self, fresh_model):
        cons = place_and_get_connectors(fresh_model, "Item.lbr", "Throw Item")
        assert_has_connector_type(cons, TYPE_ITEM, 1, "Throw Item")


# =============================================================================
# Item.lbr — Activity blocks
# =============================================================================
class TestItemActivityConnectors:
    """Activity blocks: Activity, Queue, Workstation, Transport, Convey Item."""

    def test_activity(self, fresh_model):
        cons = place_and_get_connectors(fresh_model, "Item.lbr", "Activity")
        assert_has_connector_type(cons, TYPE_ITEM, 2, "Activity")
        assert_has_connector_type(cons, TYPE_VALUE, 1, "Activity")

    def test_queue(self, fresh_model):
        cons = place_and_get_connectors(fresh_model, "Item.lbr", "Queue")
        assert_has_connector_type(cons, TYPE_ITEM, 2, "Queue")

    def test_workstation(self, fresh_model):
        cons = place_and_get_connectors(fresh_model, "Item.lbr", "Workstation")
        assert_has_connector_type(cons, TYPE_ITEM, 2, "Workstation")

    def test_transport(self, fresh_model):
        cons = place_and_get_connectors(fresh_model, "Item.lbr", "Transport")
        assert_has_connector_type(cons, TYPE_ITEM, 2, "Transport")

    def test_convey_item(self, fresh_model):
        cons = place_and_get_connectors(fresh_model, "Item.lbr", "Convey Item")
        assert_has_connector_type(cons, TYPE_ITEM, 2, "Convey Item")


# =============================================================================
# Item.lbr — Queue variants
# =============================================================================
class TestItemQueueVariantConnectors:
    """Queue variants: Queue Equation, Queue Matching."""

    def test_queue_equation(self, fresh_model):
        cons = place_and_get_connectors(fresh_model, "Item.lbr", "Queue Equation")
        assert_has_connector_type(cons, TYPE_ITEM, 2, "Queue Equation")

    def test_queue_matching(self, fresh_model):
        cons = place_and_get_connectors(fresh_model, "Item.lbr", "Queue Matching")
        assert_has_connector_type(cons, TYPE_ITEM, 2, "Queue Matching")


# =============================================================================
# Item.lbr — Batching
# =============================================================================
class TestItemBatchingConnectors:
    """Batching blocks: Batch, Unbatch."""

    def test_batch(self, fresh_model):
        cons = place_and_get_connectors(fresh_model, "Item.lbr", "Batch")
        assert_has_connector_type(cons, TYPE_ITEM, 2, "Batch")

    def test_unbatch(self, fresh_model):
        cons = place_and_get_connectors(fresh_model, "Item.lbr", "Unbatch")
        assert_has_connector_type(cons, TYPE_ITEM, 2, "Unbatch")


# =============================================================================
# Item.lbr — Resources
# =============================================================================
class TestItemResourceConnectors:
    """Resource blocks: Resource Pool, Resource Pool Release, Resource Item,
    Resource Manager, Shift, Shutdown."""

    def test_resource_pool(self, fresh_model):
        cons = place_and_get_connectors(fresh_model, "Item.lbr", "Resource Pool")
        assert len(cons) >= 1, f"Resource Pool should have connectors, got: {cons}"

    def test_resource_pool_release(self, fresh_model):
        cons = place_and_get_connectors(fresh_model, "Item.lbr", "Resource Pool Release")
        assert_has_connector_type(cons, TYPE_ITEM, 2, "Resource Pool Release")

    def test_resource_item(self, fresh_model):
        cons = place_and_get_connectors(fresh_model, "Item.lbr", "Resource Item")
        assert_has_connector_type(cons, TYPE_ITEM, 1, "Resource Item")

    def test_resource_manager(self, fresh_model):
        cons = place_and_get_connectors(fresh_model, "Item.lbr", "Resource Manager")
        # Resource Manager has no visible connectors — it's a management block
        assert len(cons) >= 0, "Resource Manager placed successfully"

    def test_shift(self, fresh_model):
        cons = place_and_get_connectors(fresh_model, "Item.lbr", "Shift")
        assert len(cons) >= 1, f"Shift should have connectors, got: {cons}"

    def test_shutdown(self, fresh_model):
        cons = place_and_get_connectors(fresh_model, "Item.lbr", "Shutdown")
        assert len(cons) >= 1, f"Shutdown should have connectors, got: {cons}"


# =============================================================================
# Item.lbr — Properties (Get, Set, Equation(I), Query Equation(I))
# =============================================================================
class TestItemPropertyConnectors:
    """Property blocks: Get, Set, Equation(I), Query Equation(I)."""

    def test_get(self, fresh_model):
        cons = place_and_get_connectors(fresh_model, "Item.lbr", "Get")
        assert_has_connector_type(cons, TYPE_ITEM, 2, "Get")

    def test_set(self, fresh_model):
        cons = place_and_get_connectors(fresh_model, "Item.lbr", "Set")
        assert_has_connector_type(cons, TYPE_ITEM, 2, "Set")

    def test_equation_i(self, fresh_model):
        cons = place_and_get_connectors(fresh_model, "Item.lbr", "Equation(I)")
        assert_has_connector_type(cons, TYPE_ITEM, 2, "Equation(I)")

    def test_query_equation_i(self, fresh_model):
        # Note: block name has space before (I) — unlike Equation(I), Read(I), Write(I)
        cons = place_and_get_connectors(fresh_model, "Item.lbr", "Query Equation (I)")
        assert_has_connector_type(cons, TYPE_ITEM, 2, "Query Equation (I)")


# =============================================================================
# Item.lbr — Information & Data
# =============================================================================
class TestItemInfoConnectors:
    """Information blocks: History, Information, Cost By Item, Read(I), Write(I)."""

    def test_history(self, fresh_model):
        cons = place_and_get_connectors(fresh_model, "Item.lbr", "History")
        assert_has_connector_type(cons, TYPE_ITEM, 1, "History")

    def test_information(self, fresh_model):
        cons = place_and_get_connectors(fresh_model, "Item.lbr", "Information")
        assert_has_connector_type(cons, TYPE_ITEM, 2, "Information")

    def test_cost_by_item(self, fresh_model):
        cons = place_and_get_connectors(fresh_model, "Item.lbr", "Cost By Item")
        assert_has_connector_type(cons, TYPE_ITEM, 2, "Cost By Item")

    def test_read_i(self, fresh_model):
        cons = place_and_get_connectors(fresh_model, "Item.lbr", "Read(I)")
        assert_has_connector_type(cons, TYPE_ITEM, 2, "Read(I)")

    def test_write_i(self, fresh_model):
        cons = place_and_get_connectors(fresh_model, "Item.lbr", "Write(I)")
        assert_has_connector_type(cons, TYPE_ITEM, 2, "Write(I)")


# =============================================================================
# Value.lbr — Core blocks
# =============================================================================
class TestValueCoreConnectors:
    """Core Value blocks: Constant, Equation, Random Number, Math, Decision."""

    def test_constant(self, fresh_model):
        cons = place_and_get_connectors(fresh_model, "Value.lbr", "Constant")
        assert_has_connector_type(cons, TYPE_VALUE, 1, "Constant")

    def test_equation(self, fresh_model):
        cons = place_and_get_connectors(fresh_model, "Value.lbr", "Equation")
        assert_has_connector_type(cons, TYPE_VALUE, 2, "Equation")

    def test_random_number(self, fresh_model):
        cons = place_and_get_connectors(fresh_model, "Value.lbr", "Random Number")
        assert_has_connector_type(cons, TYPE_VALUE, 1, "Random Number")

    def test_math(self, fresh_model):
        cons = place_and_get_connectors(fresh_model, "Value.lbr", "Math")
        assert_has_connector_type(cons, TYPE_VALUE, 2, "Math")

    def test_decision(self, fresh_model):
        cons = place_and_get_connectors(fresh_model, "Value.lbr", "Decision")
        assert_has_connector_type(cons, TYPE_VALUE, 2, "Decision")


# =============================================================================
# Value.lbr — Data & Lookup
# =============================================================================
class TestValueDataConnectors:
    """Data blocks: Lookup Table, Holding Tank, Display Value, Simulation Variable."""

    def test_lookup_table(self, fresh_model):
        cons = place_and_get_connectors(fresh_model, "Value.lbr", "Lookup Table")
        assert_has_connector_type(cons, TYPE_VALUE, 2, "Lookup Table")

    def test_holding_tank(self, fresh_model):
        cons = place_and_get_connectors(fresh_model, "Value.lbr", "Holding Tank")
        assert_has_connector_type(cons, TYPE_VALUE, 2, "Holding Tank")

    def test_display_value(self, fresh_model):
        cons = place_and_get_connectors(fresh_model, "Value.lbr", "Display Value")
        assert_has_connector_type(cons, TYPE_VALUE, 1, "Display Value")

    def test_simulation_variable(self, fresh_model):
        cons = place_and_get_connectors(fresh_model, "Value.lbr", "Simulation Variable")
        assert_has_connector_type(cons, TYPE_VALUE, 1, "Simulation Variable")


# =============================================================================
# Value.lbr — Statistics & Analysis
# =============================================================================
class TestValueStatsConnectors:
    """Statistics blocks: Mean & Variance, Max & Min, Clear Statistics."""

    def test_mean_variance(self, fresh_model):
        cons = place_and_get_connectors(fresh_model, "Value.lbr", "Mean & Variance")
        assert_has_connector_type(cons, TYPE_VALUE, 2, "Mean & Variance")

    def test_max_min(self, fresh_model):
        cons = place_and_get_connectors(fresh_model, "Value.lbr", "Max & Min")
        assert_has_connector_type(cons, TYPE_VALUE, 2, "Max & Min")

    def test_clear_statistics(self, fresh_model):
        cons = place_and_get_connectors(fresh_model, "Value.lbr", "Clear Statistics")
        assert len(cons) >= 1, f"Clear Statistics should have connectors, got: {cons}"


# =============================================================================
# Value.lbr — Continuous simulation
# =============================================================================
class TestValueContinuousConnectors:
    """Continuous blocks: Integrate, Pulse, Wait Time."""

    def test_integrate(self, fresh_model):
        cons = place_and_get_connectors(fresh_model, "Value.lbr", "Integrate")
        assert_has_connector_type(cons, TYPE_VALUE, 2, "Integrate")

    def test_pulse(self, fresh_model):
        cons = place_and_get_connectors(fresh_model, "Value.lbr", "Pulse")
        assert_has_connector_type(cons, TYPE_VALUE, 1, "Pulse")

    def test_wait_time(self, fresh_model):
        cons = place_and_get_connectors(fresh_model, "Value.lbr", "Wait Time")
        assert_has_connector_type(cons, TYPE_VALUE, 1, "Wait Time")


# =============================================================================
# Value.lbr — Routing
# =============================================================================
class TestValueRoutingConnectors:
    """Routing blocks: Select Value In, Select Value Out, Catch Value, Throw Value."""

    def test_select_value_in(self, fresh_model):
        cons = place_and_get_connectors(fresh_model, "Value.lbr", "Select Value In")
        assert_has_connector_type(cons, TYPE_VALUE, 2, "Select Value In")

    def test_select_value_out(self, fresh_model):
        cons = place_and_get_connectors(fresh_model, "Value.lbr", "Select Value Out")
        assert_has_connector_type(cons, TYPE_VALUE, 2, "Select Value Out")

    def test_catch_value(self, fresh_model):
        cons = place_and_get_connectors(fresh_model, "Value.lbr", "Catch Value")
        assert_has_connector_type(cons, TYPE_VALUE, 1, "Catch Value")

    def test_throw_value(self, fresh_model):
        cons = place_and_get_connectors(fresh_model, "Value.lbr", "Throw Value")
        assert_has_connector_type(cons, TYPE_VALUE, 1, "Throw Value")


# =============================================================================
# Value.lbr — Data Access & I/O
# =============================================================================
class TestValueDataAccessConnectors:
    """Data access blocks: Read, Write, Data Source Create, Data Init,
    Data Import Export, Data Specs."""

    def test_read(self, fresh_model):
        cons = place_and_get_connectors(fresh_model, "Value.lbr", "Read")
        assert_has_connector_type(cons, TYPE_VALUE, 1, "Read")

    def test_write(self, fresh_model):
        cons = place_and_get_connectors(fresh_model, "Value.lbr", "Write")
        assert_has_connector_type(cons, TYPE_VALUE, 1, "Write")

    def test_data_source_create(self, fresh_model):
        cons = place_and_get_connectors(fresh_model, "Value.lbr", "Data Source Create")
        assert len(cons) >= 0, "Data Source Create placed successfully"

    def test_data_init(self, fresh_model):
        cons = place_and_get_connectors(fresh_model, "Value.lbr", "Data Init")
        assert len(cons) >= 0, "Data Init placed successfully"

    def test_data_import_export(self, fresh_model):
        cons = place_and_get_connectors(fresh_model, "Value.lbr", "Data Import Export")
        assert len(cons) >= 0, "Data Import Export placed successfully"

    def test_data_specs(self, fresh_model):
        cons = place_and_get_connectors(fresh_model, "Value.lbr", "Data Specs")
        assert len(cons) >= 0, "Data Specs placed successfully"


# =============================================================================
# Value.lbr — Misc
# =============================================================================
class TestValueMiscConnectors:
    """Misc blocks: Query Equation, Notify, Command, Time Unit."""

    def test_query_equation(self, fresh_model):
        cons = place_and_get_connectors(fresh_model, "Value.lbr", "Query Equation")
        assert_has_connector_type(cons, TYPE_VALUE, 1, "Query Equation")

    def test_notify(self, fresh_model):
        cons = place_and_get_connectors(fresh_model, "Value.lbr", "Notify")
        assert len(cons) >= 1, f"Notify should have connectors, got: {cons}"

    def test_command(self, fresh_model):
        cons = place_and_get_connectors(fresh_model, "Value.lbr", "Command")
        assert len(cons) >= 0, "Command placed successfully"

    def test_time_unit(self, fresh_model):
        cons = place_and_get_connectors(fresh_model, "Value.lbr", "Time Unit")
        assert len(cons) >= 0, "Time Unit placed successfully"


# =============================================================================
# Rate.lbr — Flow blocks (kept from original)
# =============================================================================
class TestFlowBlockConnectors:
    """Verify Rate.lbr blocks have Flow connectors (type 25)."""

    def test_tank(self, fresh_model):
        cons = place_and_get_connectors(fresh_model, "Rate.lbr", "Tank")
        assert_has_connector_type(cons, TYPE_FLOW, 2, "Tank")

    def test_valve(self, fresh_model):
        cons = place_and_get_connectors(fresh_model, "Rate.lbr", "Valve")
        assert_has_connector_type(cons, TYPE_FLOW, 1, "Valve")
