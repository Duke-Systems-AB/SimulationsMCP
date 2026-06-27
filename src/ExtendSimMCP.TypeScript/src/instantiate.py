# src/instantiate.py
"""Deterministic molecule -> H-block construction engine (approach 1).

Orchestrates an injected EsOps interface so the COM construction sequence
is testable. Every COM-affecting step is effect-verified, never trusting a
success flag (krav 12). See spec 2026-06-27-m3-instantiate-pattern-design.md.
"""
from typing import Any, Dict
from molecule_schema import validate_molecule, resolve_params


class BuildError(Exception):
    pass


def _node(molecule, ref):
    for n in molecule["nodes"]:
        if n["ref"] == ref:
            return n
    raise BuildError(f"unknown node ref: {ref}")


def _flow_order(seed_ref, flow_edges):
    """Return the flow node refs in chain order starting at seed_ref."""
    nxt = {e["from"].split(".")[0]: e["to"].split(".")[0] for e in flow_edges}
    order, cur = [seed_ref], seed_ref
    while cur in nxt:
        cur = nxt[cur]
        order.append(cur)
    return order


def _assert_clean(ops, a_id, b_id):
    """Effect-verify: a.ItemOut and b.ItemIn share a node, not collapsed to 0."""
    if ops.node_of(a_id, ops.con_index(a_id, "ItemOut")) != ops.node_of(b_id, ops.con_index(b_id, "ItemIn")):
        raise BuildError("flow rewire failed: connectors not on a shared node")
    if ops.node_of(b_id, ops.con_index(b_id, "ItemIn")) == 0:
        raise BuildError("flow rewire failed: node collapsed to 0")


def build_molecule(molecule: Dict[str, Any], params: Dict[str, Any], ops) -> Dict[str, Any]:
    validate_molecule(molecule, params)            # fail-closed, before any COM
    ops.activate()

    seed = next(n for n in molecule["nodes"] if n.get("seed"))

    # Phase 1: wrap seed in context -> interfaced 1-block H-block, then drop stubs.
    up = ops.add_block("Item.lbr", "Create")
    seed_id = ops.add_block(seed["lib"], seed["type"])
    down = ops.add_block("Item.lbr", "Exit")
    ops.connect(up, ops.con_index(up, "ItemOut"), seed_id, ops.con_index(seed_id, "ItemIn"))
    ops.connect(seed_id, ops.con_index(seed_id, "ItemOut"), down, ops.con_index(down, "ItemIn"))
    hblock_id = ops.create_hblock(seed_id, molecule["id"])
    ops.remove_block(up)
    ops.remove_block(down)

    internal = {seed["ref"]: seed_id}

    # Phase 2: append remaining flow nodes at the outlet, disconnect-first.
    flow_edges = [e for e in molecule["edges"] if e["kind"] == "flow"]
    order = _flow_order(seed["ref"], flow_edges)     # seed first, then downstream
    last_ref, last_id = seed["ref"], seed_id
    for ref in order[1:]:
        node = _node(molecule, ref)
        new_id = ops.place_in_hblock(node["lib"], node["type"], hblock_id)
        internal[ref] = new_id
        outlet = ops.outlet_connector(hblock_id)
        # disconnect last.out <-> outlet, then last.out -> new.in, new.out -> outlet
        ops.disconnect(last_id, ops.con_index(last_id, "ItemOut"), outlet, 0)
        ops.connect(last_id, ops.con_index(last_id, "ItemOut"), new_id, ops.con_index(new_id, "ItemIn"))
        ops.connect(new_id, ops.con_index(new_id, "ItemOut"), outlet, 0)
        _assert_clean(ops, last_id, new_id)
        last_ref, last_id = ref, new_id

    return {"hblockId": hblock_id, "internalBlockIds": internal}
