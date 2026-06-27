# src/compose.py
"""Compose molecule instances into a whole flow (M4).

build_flow orchestrates an injected EsOps interface (reuses the M3 engine);
validate_flow is pure (role + declared attribute-contract checks). See spec
2026-06-27-m4-compose-flow-design.md.
"""
import collections
from typing import Any, Dict


class FlowError(Exception):
    pass


def _port_role(molecule, port, kind):
    for p in molecule.get("interface", {}).get(kind, []):
        if p["port"] == port:
            return p["role"]
    return None


def _attrs(molecule):
    a = molecule.get("attributes", {})
    return set(a.get("reads", [])), set(a.get("writes", []))


def validate_flow(flow_def: Dict[str, Any], molecules: Dict[str, Any]) -> None:
    """Raise FlowError if the flow is not buildable. `molecules` maps pattern id -> molecule dict."""
    instances = flow_def.get("instances", [])
    refs = [i["ref"] for i in instances]
    if len(refs) != len(set(refs)):
        raise FlowError("duplicate instance ref in flow")
    ref_to_pattern = {i["ref"]: i["pattern"] for i in instances}

    edges = []
    for w in flow_def.get("wiring", []):
        a_ref, a_port = w["from"].split(".", 1)
        b_ref, b_port = w["to"].split(".", 1)
        if a_ref not in ref_to_pattern:
            raise FlowError(f"wiring references unknown instance: {a_ref}")
        if b_ref not in ref_to_pattern:
            raise FlowError(f"wiring references unknown instance: {b_ref}")
        a_role = _port_role(molecules[ref_to_pattern[a_ref]], a_port, "outlets")
        b_role = _port_role(molecules[ref_to_pattern[b_ref]], b_port, "inlets")
        if a_role is None:
            raise FlowError(f"unknown outlet port: {w['from']}")
        if b_role is None:
            raise FlowError(f"unknown inlet port: {w['to']}")
        if a_role != b_role:
            raise FlowError(f"role mismatch: {w['from']}({a_role}) -> {w['to']}({b_role})")
        edges.append((a_ref, b_ref))

    _check_attribute_contract(ref_to_pattern, molecules, edges)


def _check_attribute_contract(ref_to_pattern, molecules, edges):
    preds = collections.defaultdict(set)
    for a, b in edges:
        preds[b].add(a)

    def ancestors(node):
        seen, stack = set(), list(preds[node])
        while stack:
            p = stack.pop()
            if p not in seen:
                seen.add(p)
                stack.extend(preds[p])
        return seen

    for ref, pattern in ref_to_pattern.items():
        reads, _ = _attrs(molecules[pattern])
        if not reads:
            continue
        upstream_writes = set()
        for anc in ancestors(ref):
            _, w = _attrs(molecules[ref_to_pattern[anc]])
            upstream_writes |= w
        missing = reads - upstream_writes
        if missing:
            raise FlowError(
                f"instance {ref} reads {sorted(missing)} but no upstream instance writes them")


def build_flow(flow_def, ops):
    """Instantiate every molecule instance and wire them per the flow definition.

    Reuses the M3 engine; `ops` is the injected EsOps interface.
    """
    from instantiate import build_molecule, _load_molecule
    molecules = {i["pattern"]: _load_molecule(i["pattern"]) for i in flow_def["instances"]}
    validate_flow(flow_def, molecules)

    instances = {}
    for i in flow_def["instances"]:
        res = build_molecule(molecules[i["pattern"]], i.get("params") or {}, ops)
        instances[i["ref"]] = {"hblockId": res["hblockId"], "interfaceMap": res["interfaceMap"]}

    for w in flow_def.get("wiring", []):
        a_ref, a_port = w["from"].split(".", 1)
        b_ref, b_port = w["to"].split(".", 1)
        a, b = instances[a_ref], instances[b_ref]
        ops.connect(a["hblockId"], a["interfaceMap"][a_port]["outerCon"],
                    b["hblockId"], b["interfaceMap"][b_port]["outerCon"])

    return {"flowId": flow_def.get("id"), "instances": instances,
            "wiring": flow_def.get("wiring", [])}
