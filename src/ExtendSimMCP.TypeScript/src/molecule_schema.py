# src/molecule_schema.py
"""Pure validation + param resolution for molecule definitions. No COM."""
import re
from typing import Any, Dict

_PLACEHOLDER = re.compile(r"^\{\{(\w+)\}\}$")


class MoleculeError(Exception):
    pass


def _check_placeholder_declared(value: Any, declared_params: set, where: str) -> None:
    """Raise MoleculeError if `value` is a {{placeholder}} not in declared_params.
    Reuses the same placeholder-detection regex as resolve_params/_resolve_value."""
    if isinstance(value, str):
        m = _PLACEHOLDER.match(value)
        if m and m.group(1) not in declared_params:
            raise MoleculeError(
                f"undeclared param placeholder '{{{{{m.group(1)}}}}}' in {where} "
                f"(not in molecule params)")


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

    # setAttributes entries must name an attribute
    for n in nodes:
        for entry in (n.get("setAttributes") or []):
            if not entry.get("name"):
                raise MoleculeError(f"setAttributes entry on node {n.get('ref')} missing 'name'")

    # {{placeholder}} references (params / setAttributes / resourcePool) must be
    # declared in molecule["params"] - otherwise they surface as a raw KeyError
    # mid-build (W2-9).
    declared_params = set((molecule.get("params") or {}).keys())
    for n in nodes:
        ref = n.get("ref")
        for key, value in (n.get("params") or {}).items():
            _check_placeholder_declared(value, declared_params, f"node {ref} param '{key}'")
        for entry in (n.get("setAttributes") or []):
            _check_placeholder_declared(
                entry.get("value"), declared_params,
                f"node {ref} setAttributes['{entry.get('name')}'].value")
    rp = molecule.get("resourcePool")
    if rp:
        for key in ("name", "capacity", "qty"):
            if key in rp:
                _check_placeholder_declared(rp[key], declared_params, f"resourcePool.{key}")


def _lookup_param(name: str, params: Dict[str, Any]) -> Any:
    """Look up a resolved param value, raising an honest MoleculeError (not a
    raw KeyError) when the placeholder has no default and no caller-supplied
    value (W2-9 follow-up)."""
    try:
        return params[name]
    except KeyError:
        raise MoleculeError(
            f"no value supplied for param '{name}' (no default declared)") from None


def resolve_params(node: Dict[str, Any], params: Dict[str, Any]) -> Dict[str, Any]:
    """Substitute {{name}} placeholders in a node's params with bound values."""
    out = {}
    for k, v in (node.get("params") or {}).items():
        if isinstance(v, str):
            m = _PLACEHOLDER.match(v)
            if m:
                out[k] = _lookup_param(m.group(1), params)
                continue
        out[k] = v
    return out


def _resolve_value(v, params):
    """Resolve a single {{name}} placeholder against params; pass through otherwise."""
    if isinstance(v, str):
        m = _PLACEHOLDER.match(v)
        if m:
            return _lookup_param(m.group(1), params)
    return v


def resolve_resource_pool(molecule, params):
    """Resolve a molecule's optional resourcePool block ({{...}} -> values)."""
    rp = molecule.get("resourcePool")
    if not rp:
        return None
    out = dict(rp)
    for k in ("name", "capacity", "qty"):
        if k in out:
            out[k] = _resolve_value(out[k], params)
    return out


def resolve_set_attributes(node: Dict[str, Any], params: Dict[str, Any]):
    """Return the node's setAttributes with placeholders resolved.

    Each entry -> {"name": str, "value": <resolved>, "valueType": str}.
    """
    out = []
    for entry in (node.get("setAttributes") or []):
        out.append({
            "name": entry["name"],
            "value": _resolve_value(entry.get("value"), params),
            "valueType": entry.get("valueType", "constant"),
        })
    return out
