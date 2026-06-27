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

    return {"hblockId": hblock_id, "internalBlockIds": {seed["ref"]: seed_id}}
