# src/pattern_approve.py
"""Pattern approval + library persistence (M10).

Assembles a validated, M3-instantiable library entry (§7.1) from an M9 mined pattern
candidate plus a caller-supplied naming, and writes it into patterns/molecules/. Pure
+ file I/O, no COM. Nothing is written unless it passes molecule_schema.validate_molecule
and the caller deliberately approves. See spec 2026-07-16-m10-approve-pattern-design.md.
"""
import os
import re
import json

from molecule_schema import validate_molecule, MoleculeError
from patterns import infer_port_role

# Mining (model_extract's EXTRACT_PARAMS) names block settings with friendly names; a molecule
# must hold ExtendSim's own variable names, because instantiate writes them with
# block_set_value, which fails closed on a name the block does not have. Found 2026-09-29: an
# approved Bank.mox pattern carried Queue "rankType" and its settings had silently never been
# applied. Activity/Workstation settings EXTRACT_PARAMS reads by dialog ID are named here
# (IDs resolved from Item.lbr 2024). tests/unit_py/test_pattern_approve.py keeps this table in
# step with EXTRACT_PARAMS.
WRITE_NAMES = {
    "Create": {"arrivalType": "CreateOptions_pop", "distribution": "Rnd_Distributions_pop",
               "arg1": "Rnd_Arg1_prm", "arg2": "Rnd_Arg2_prm", "arg3": "Rnd_Arg3_prm",
               "maxArrivalsEnabled": "RndI_MaxItems_chk", "maxArrivals": "RndI_MaxItems_prm"},
    "Queue": {"rankType": "QueueRank_Pop", "sortAttribute": "SortAttrib_Pop",
              "maxLength": "MaxLength_prm"},
    "Activity": {"delayType": "Delay_Options_pop", "distribution": "Delay_Distributions_pop",
                 "arg1": "Delay_Arg1_prm", "arg2": "Delay_Arg2_prm", "arg3": "Delay_Arg3_prm",
                 "fixedDelay": "WaitDelta_prm"},
    "Workstation": {"maxServers": "MaxServers_prm", "maxQueueLength": "MaxQueueLength_prm",
                    "delayType": "Delay_Options_pop", "distribution": "Delay_Distributions_pop",
                    "arg1": "Delay_Arg1_prm", "arg2": "Delay_Arg2_prm", "arg3": "Delay_Arg3_prm"},
    "Gate": {"demandType": "DemandType_Pop", "initialState": "InitialCondition_Pop"},
    "Select Item Out": {"mode": "SelectType_Pop"},
    "Select Item In": {"mode": "SelectType_Pop"},
    "Batch": {"batchSize": "Quantity_prm"},
    "Resource Pool": {"poolName": "PoolName_prm", "initialResources": "NumberOfItems_prm"},
    "Tank": {"capacity": "MaxLevel_prm", "initialLevel": "InitialLevel_prm"},
    "Valve": {"maxRate": "MaxRate_prm"},
    "Shutdown": {"tbfDistribution": "SF_TBF_Distribs_pop", "tbfArg1": "SF_TBF_Arg1_prm",
                 "tbfArg2": "SF_TBF_Arg2_prm", "ttrDistribution": "SF_TTR_Distribs_pop",
                 "ttrArg1": "SF_TTR_Arg1_prm", "ttrArg2": "SF_TTR_Arg2_prm"},
    "Resource Pool Release": {"releaseQuantity": "NumReleased_PRM"},
}

# Settings that choose an ATTRIBUTE from a popup are never written: SetDialogVariable runs the
# popup's handler, which indexes the model's attribute list - in a model without that attribute
# it raised a modal "Array exceeded dimensional bounds" (Queue SortAttrib_Pop, 2026-09-29) and
# blocked COM. They are listed in the entry's notApplied instead. Setting an attribute choice
# safely (write the block's statics, like attribute_set) is part of the queues sub-project.
NEVER_WRITE = {"SortAttrib_Pop"}

_PLACEHOLDER = re.compile(r"^\{\{(.+?)\}\}$")
_WORD = re.compile(r"^\w+$")


class ApproveError(Exception):
    pass


def _sanitize(key):
    """Turn an M9 param key (e.g. 'b3.D') into a placeholder-safe \\w+ name ('b3_D')."""
    return re.sub(r"\W+", "_", key)


def _friendly_map(candidate, naming):
    """Map each M9 param key -> friendly name (caller-provided, validated \\w+, or sanitized)."""
    provided = naming.get("params") or {}
    out = {}
    for key in candidate.get("params", {}):
        name = provided.get(key)
        if name is not None:
            if not _WORD.match(name):
                raise ApproveError(f"friendly param name must be \\w+ (letters/digits/_): {name!r}")
            out[key] = name
        else:
            out[key] = _sanitize(key)
    seen = {}
    for k, name in out.items():
        if name in seen:
            raise ApproveError(
                f"duplicate friendly param name {name!r} (from {seen[name]!r} and {k!r})")
        seen[name] = k
    return out


def _normalize_lib(lib):
    if lib and not lib.lower().endswith(".lbr"):
        return lib + ".lbr"
    return lib


def _infer_edge_kind(frm, to, override):
    if to in override:
        return override[to]
    text = frm.rpartition(".")[2].lower() + " " + to.rpartition(".")[2].lower()
    return "flow" if "item" in text else "side"


def _infer_attribute_contract(nodes, not_applied=()):
    """Infer {reads, writes} from a molecule's (already-rewritten) nodes.

    writes = attribute names appearing in any node's setAttributes.
    reads = attribute names referenced by other nodes' params: a literal
    (non-placeholder) string value on a param whose key names an attribute
    reference - a key containing "attrib" (sortAttribute, SortAttrib_Pop,
    matchAttribute, ...). Mirrors the flat reads/writes list shape compose.py's
    _check_attribute_contract consumes."""
    writes = []
    for n in nodes:
        for entry in (n.get("setAttributes") or []):
            name = entry.get("name")
            if name and name not in writes:
                writes.append(name)
    reads = []
    for n in nodes:
        for k, v in (n.get("params") or {}).items():
            # "attrib" matches both friendly keys (sortAttribute) and ExtendSim's own names
            # (SortAttrib_Pop, attribNamesChosen) now that WRITE_NAMES translates the keys.
            if isinstance(v, str) and "attrib" in k.lower() and not _PLACEHOLDER.match(v):
                if v not in reads:
                    reads.append(v)
    # An attribute choice that could not be applied (NEVER_WRITE) is still what the pattern
    # reads - the user sets it by hand - so compose keeps warning when nothing writes it.
    for na in not_applied:
        v = na.get("value")
        if isinstance(v, str) and "attrib" in na["param"].lower() and not _PLACEHOLDER.match(v):
            if v not in reads:
                reads.append(v)
    return {"reads": reads, "writes": writes}


def _role_for(binds, candidate):
    """Prefer the candidate's own inferred role for this bind; fall back to the
    shared name-based heuristic (patterns.infer_port_role) if not found there."""
    for grp in ("inlets", "outlets"):
        for p in candidate.get("interface", {}).get(grp, []):
            if p.get("binds") == binds:
                return p.get("role")
    return infer_port_role(binds)


def build_library_entry(candidate, naming):
    """Assemble an M3-instantiable §7.1 molecule entry from a candidate + naming."""
    if candidate.get("kind") == "composite":
        raise ApproveError("composite candidates are flows (§7.3), unsupported in v1")

    template = candidate.get("template", {})
    nodes = template.get("nodes", [])
    refs = {n["ref"] for n in nodes}
    seed = naming.get("seed")
    if not seed or seed not in refs:
        raise ApproveError(f"naming.seed must be a template node ref; got {seed!r}")
    if not naming.get("id"):
        raise ApproveError("naming.id is required")

    fmap = _friendly_map(candidate, naming)

    # params: only the tunable (required) ones, friendly-named
    params = {}
    for key, info in candidate.get("params", {}).items():
        if not info.get("required"):
            continue
        p = {"type": info.get("type", "number"), "required": True}
        if "default" in info:
            p["default"] = info["default"]
        if "range" in info:
            p["range"] = info["range"]
        params[fmap[key]] = p

    # nodes: rewrite placeholders, seed flag, lib normalize
    out_nodes = []
    not_applied = []
    for n in nodes:
        on = {"ref": n["ref"], "lib": _normalize_lib(n.get("lib", "")), "type": n.get("type", "")}
        p = {}
        write_names = WRITE_NAMES.get(on["type"], {})
        for k, v in (n.get("params") or {}).items():
            name = write_names.get(k, k)
            if name in NEVER_WRITE:
                not_applied.append({"node": n["ref"], "param": k, "value": v,
                                    "reason": "chooses an attribute from a popup; writing it over "
                                              "COM can raise a modal box - set it by hand"})
                continue
            if isinstance(v, str):
                m = _PLACEHOLDER.match(v)
                if m:
                    inner = m.group(1)
                    p[name] = "{{" + fmap.get(inner, _sanitize(inner)) + "}}"
                    continue
            p[name] = v
        if p:
            on["params"] = p
        sa = []
        for entry in (n.get("setAttributes") or []):
            value = entry.get("value")
            if isinstance(value, str):
                m = _PLACEHOLDER.match(value)
                if m:
                    inner = m.group(1)
                    value = "{{" + fmap.get(inner, _sanitize(inner)) + "}}"
            new_entry = {"name": entry.get("name"), "value": value}
            if "valueType" in entry:
                new_entry["valueType"] = entry["valueType"]
            sa.append(new_entry)
        if sa:
            on["setAttributes"] = sa
        if n["ref"] == seed:
            on["seed"] = True
        if n.get("isHBlock"):
            on["isHBlock"] = True
        out_nodes.append(on)

    # edges: add kind
    override = naming.get("edgeKinds") or {}
    out_edges = [{"kind": _infer_edge_kind(e["from"], e["to"], override),
                  "from": e["from"], "to": e["to"]}
                 for e in template.get("edges", [])]

    # interface from naming.inlet / naming.outlet
    def _iface(spec):
        if not spec:
            return None
        binds = spec["binds"]
        return {"port": spec["port"], "binds": binds, "role": _role_for(binds, candidate)}

    inlets = [x for x in [_iface(naming.get("inlet"))] if x]
    outlets = [x for x in [_iface(naming.get("outlet"))] if x]

    example = {fmap.get(k, _sanitize(k)): v for k, v in (candidate.get("example") or {}).items()}

    attributes = naming.get("attributes") or _infer_attribute_contract(out_nodes, not_applied)

    entry = {
        "id": naming["id"],
        "version": "1.0",
        "kind": "molecule",
        "intent": naming.get("intent", ""),
        "params": params,
        "attributes": attributes,
        "nodes": out_nodes,
        "edges": out_edges,
        "interface": {"inlets": inlets, "outlets": outlets},
        "provenance": {
            "mined_from": candidate.get("support"),
            "wl_fingerprint": candidate.get("wl_fingerprint"),
            "sources": [i.get("source") for i in candidate.get("instances", [])],
            "nearMiss": candidate.get("nearMiss", False),
        },
        "example": example,
    }
    if not_applied:
        entry["notApplied"] = not_applied
    return entry


def _default_molecules_dir():
    return os.path.join(os.path.dirname(__file__), "..", "patterns", "molecules")


def approve_pattern_entry(candidate=None, patterns_path=None, pattern_fingerprint=None,
                          naming=None, dry_run=False, overwrite=False, molecules_dir=None):
    """Resolve a candidate, assemble + validate its library entry, then preview or write it."""
    try:
        if candidate is None:
            if not patterns_path or not pattern_fingerprint:
                return {"success": False, "errorCode": "NO_CANDIDATE",
                        "error": "provide candidate, or patternsPath + patternFingerprint"}
            try:
                with open(patterns_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as e:
                return {"success": False, "errorCode": "PATTERNS_PATH_UNREADABLE",
                        "error": f"cannot read patternsPath: {e}", "patternsPath": patterns_path}
            candidate = next((p for p in data.get("patterns", [])
                              if p.get("wl_fingerprint") == pattern_fingerprint), None)
            if candidate is None:
                return {"success": False, "errorCode": "UNKNOWN_FINGERPRINT",
                        "error": f"no pattern with fingerprint {pattern_fingerprint}"}

        if not naming or not naming.get("id"):
            return {"success": False, "errorCode": "NAMING_REQUIRED",
                    "error": "naming with an id is required"}

        try:
            entry = build_library_entry(candidate, naming)
        except ApproveError as e:
            return {"success": False, "errorCode": "BUILD_FAILED", "error": str(e)}

        try:
            validate_molecule(entry, entry.get("example", {}))
        except MoleculeError as e:
            return {"success": False, "errorCode": "VALIDATION_FAILED", "error": str(e)}

        if dry_run:
            return {"success": True, "preview": entry}

        mdir = molecules_dir or _default_molecules_dir()
        path = os.path.join(mdir, f"{entry['id']}.json")
        if os.path.exists(path) and not overwrite:
            return {"success": False, "errorCode": "ALREADY_EXISTS",
                    "error": f"pattern id already exists: {entry['id']}", "path": path}
        os.makedirs(mdir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(entry, f, indent=2, allow_nan=False)
        return {"success": True, "written": path, "id": entry["id"]}
    except Exception as e:
        return {"success": False, "errorCode": "APPROVE_FAILED", "error": str(e)}
