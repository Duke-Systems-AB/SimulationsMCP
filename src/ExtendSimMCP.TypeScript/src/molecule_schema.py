# src/molecule_schema.py
"""Pure validation + param resolution for molecule definitions. No COM."""
import re
from typing import Any, Dict

_PLACEHOLDER = re.compile(r"^\{\{(\w+)\}\}$")


class MoleculeError(Exception):
    pass


def validate_molecule(molecule: Dict[str, Any], params: Dict[str, Any]) -> None:
    """Raise MoleculeError if the molecule + bound params are not buildable."""
    nodes = molecule.get("nodes", [])
    refs = {n["ref"] for n in nodes}

    # exactly one seed
    seeds = [n for n in nodes if n.get("seed")]
    if len(seeds) != 1:
        raise MoleculeError(f"molecule must have exactly one seed node, found {len(seeds)}")

    # required params present
    for name, spec in (molecule.get("params") or {}).items():
        if spec.get("required") and name not in params:
            raise MoleculeError(f"missing required param: {name}")

    # edges: known kind + reference known nodes
    for e in molecule.get("edges", []):
        if e.get("kind") not in ("flow", "side"):
            raise MoleculeError(f"edge kind must be 'flow' or 'side': {e}")
        for side in ("from", "to"):
            ref = e[side].split(".", 1)[0]
            if ref not in refs:
                raise MoleculeError(f"edge {side} references unknown node: {ref}")

    # interface: at most one inlet/outlet (M3 builds a single linear flow), binds known
    iface = molecule.get("interface", {})
    if len(iface.get("inlets", [])) > 1 or len(iface.get("outlets", [])) > 1:
        raise MoleculeError("interface must have at most one inlet and one outlet")
    for port in (iface.get("inlets", []) + iface.get("outlets", [])):
        ref = port["binds"].split(".", 1)[0]
        if ref not in refs:
            raise MoleculeError(f"interface binds unknown node: {ref}")


def resolve_params(node: Dict[str, Any], params: Dict[str, Any]) -> Dict[str, Any]:
    """Substitute {{name}} placeholders in a node's params with bound values."""
    out = {}
    for k, v in (node.get("params") or {}).items():
        if isinstance(v, str):
            m = _PLACEHOLDER.match(v)
            if m:
                out[k] = params[m.group(1)]
                continue
        out[k] = v
    return out
