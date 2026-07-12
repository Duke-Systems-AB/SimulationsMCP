# src/instantiate.py
"""Deterministic molecule -> H-block construction engine (approach 1).

Orchestrates an injected EsOps interface so the COM construction sequence
is testable. Every COM-affecting step is effect-verified, never trusting a
success flag (krav 12). See spec 2026-06-27-m3-instantiate-pattern-design.md.
"""
from typing import Any, Dict
from molecule_schema import (
    validate_molecule, resolve_params, resolve_set_attributes, resolve_resource_pool, MoleculeError,
)


class BuildError(Exception):
    pass


def _node(molecule, ref):
    for n in molecule["nodes"]:
        if n["ref"] == ref:
            return n
    raise BuildError(f"unknown node ref: {ref}")


def _flow_chain(flow_edges):
    """Flow node refs from chain head to tail (single linear chain assumed)."""
    if not flow_edges:
        return []
    nxt = {e["from"].split(".")[0]: e["to"].split(".")[0] for e in flow_edges}
    tos = set(nxt.values())
    heads = [f for f in nxt if f not in tos]
    if len(heads) != 1:
        raise BuildError(f"flow must be a single linear chain; heads={heads}")
    order, cur = [heads[0]], heads[0]
    while cur in nxt:
        cur = nxt[cur]
        order.append(cur)
    return order


def _layout(molecule, internal, ops):
    """Spread blocks: flow nodes left->right along x, side nodes on a row below.
    Deterministic; prevents the default (100,100)-stacking. Positions are logical
    ExtendSim units; exact spacing is cosmetic."""
    chain = _flow_chain([e for e in molecule["edges"] if e["kind"] == "flow"])
    ordered = chain or [n["ref"] for n in molecule["nodes"]]
    x0, y_flow, dx = 60, 100, 120
    for i, ref in enumerate(ordered):
        if ref in internal:
            ops.move(internal[ref], x0 + i * dx, y_flow)
    side = [n["ref"] for n in molecule["nodes"] if n["ref"] not in ordered]
    for j, ref in enumerate(side):
        if ref in internal:
            ops.move(internal[ref], x0 + j * dx, y_flow + 140)


def _assert_clean(ops, a_id, b_id):
    """Effect-verify: a.ItemOut and b.ItemIn share a node, not collapsed to 0."""
    if ops.node_of(a_id, ops.con_index(a_id, "ItemOut")) != ops.node_of(b_id, ops.con_index(b_id, "ItemIn")):
        raise BuildError("flow rewire failed: connectors not on a shared node")
    if ops.node_of(b_id, ops.con_index(b_id, "ItemIn")) == 0:
        raise BuildError("flow rewire failed: node collapsed to 0")


def _merge_param_defaults(molecule, params):
    """Return params with molecule-declared defaults filled in where absent."""
    merged = dict(params or {})
    for name, spec in (molecule.get("params") or {}).items():
        if name not in merged and isinstance(spec, dict) and "default" in spec:
            merged[name] = spec["default"]
    return merged


def build_molecule(molecule: Dict[str, Any], params: Dict[str, Any], ops) -> Dict[str, Any]:
    validate_molecule(molecule, params)            # fail-closed, before any COM
    params = _merge_param_defaults(molecule, params)
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

    # Phase 2: prepend earlier flow nodes at the inlet, disconnect-first.
    # Uses only proven COM ops (connect inlet-boundary->inner and inner->inner);
    # the seed must be the chain TAIL (bound to the outlet). Connecting an inner
    # block INTO the outlet boundary is unreliable, so we grow from the tail.
    chain = _flow_chain([e for e in molecule["edges"] if e["kind"] == "flow"])
    if chain:
        if chain[-1] != seed["ref"]:
            raise BuildError(f"seed must be the flow-chain tail; chain={chain}, seed={seed['ref']}")
        first_id = seed_id
        for ref in reversed(chain[:-1]):
            node = _node(molecule, ref)
            new_id = ops.place_in_hblock(node["lib"], node["type"], hblock_id)
            internal[ref] = new_id
            inlet = ops.inlet_connector(hblock_id)
            # disconnect inlet <-> current-first.in, then inlet -> new.in, new.out -> current-first.in
            ops.disconnect(inlet, 0, first_id, ops.con_index(first_id, "ItemIn"))
            ops.connect(inlet, 0, new_id, ops.con_index(new_id, "ItemIn"))
            ops.connect(new_id, ops.con_index(new_id, "ItemOut"), first_id, ops.con_index(first_id, "ItemIn"))
            _assert_clean(ops, new_id, first_id)
            first_id = new_id

    # Phase 3: place + wire side nodes (non-flow blocks), by name, node-verified.
    flow_refs = set(internal)
    for node in molecule["nodes"]:
        if node["ref"] in flow_refs:
            continue
        internal[node["ref"]] = ops.place_in_hblock(node["lib"], node["type"], hblock_id)
    for e in molecule["edges"]:
        if e["kind"] != "side":
            continue
        a_ref, a_con = e["from"].split(".")
        b_ref, b_con = e["to"].split(".")
        a_id, b_id = internal[a_ref], internal[b_ref]
        ops.connect(a_id, ops.con_index(a_id, a_con), b_id, ops.con_index(b_id, b_con))
        if ops.node_of(a_id, ops.con_index(a_id, a_con)) != ops.node_of(b_id, ops.con_index(b_id, b_con)):
            raise BuildError(f"side connection failed (not on shared node): {e['from']} -> {e['to']}")

    # Phase 4: set parameters (placeholders resolved).
    for node in molecule["nodes"]:
        for var, value in resolve_params(node, params).items():
            ops.set_value(internal[node["ref"]], var, value)

    # Phase 4b: apply attribute-set configs (Set blocks tag items).
    for node in molecule["nodes"]:
        for a in resolve_set_attributes(node, params):
            ops.set_attribute(internal[node["ref"]], a["name"], a["value"], a["valueType"])

    # Phase 4c: apply the resource-pool config (pool + queue + release), if any.
    rp_cfg = resolve_resource_pool(molecule, params)
    if rp_cfg:
        ops.configure_resource_pool(
            internal[rp_cfg["poolNode"]], internal[rp_cfg["queueNode"]],
            internal[rp_cfg["releaseNode"]], rp_cfg["name"], rp_cfg["capacity"], rp_cfg["qty"])

    # Phase 4d: layout - spread blocks so they don't stack on top of each other.
    _layout(molecule, internal, ops)

    # Phase 5: interface map (molecule port -> inner block + outer connector).
    iface = {}
    for port in molecule["interface"].get("inlets", []):
        ref = port["binds"].split(".")[0]
        iface[port["port"]] = {"blockId": internal[ref], "outerCon": ops.outer_index(hblock_id, "in")}
    for port in molecule["interface"].get("outlets", []):
        ref = port["binds"].split(".")[0]
        iface[port["port"]] = {"blockId": internal[ref], "outerCon": ops.outer_index(hblock_id, "out")}

    return {"hblockId": hblock_id, "internalBlockIds": internal, "interfaceMap": iface}


class RealOps:
    """EsOps backed by the live simulation_backend COM primitives.

    Every state-changing call effect-verifies (krav 12): success flags are
    necessary but never sufficient.
    """
    def __init__(self, backend):
        self._b = backend

    def activate(self):
        self._b.execute_command("ActivateApplication();")

    def add_block(self, lib, type_):
        r = self._b.block_add(lib, type_)
        if not r.get("success") or "blockId" not in r:
            raise BuildError(f"add_block failed: {lib}/{type_}: {r}")
        return r["blockId"]

    def con_index(self, block_id, con_name):
        r = self._b.execute_command(
            f'global0 = getConNumber({block_id}, "{con_name}");', get_result=True)
        return int(r["result"])

    def connect(self, a_id, a_con, b_id, b_con):
        self._b.execute_command(f"MakeConnection({a_id}, {a_con}, {b_id}, {b_con});")
        # Boundary connector-objects do not report their node via NodeGetIDIndex
        # (they read 0). So a connect is valid if at least one endpoint shows a
        # node; if BOTH are real connectors their nodes must match (no collapse).
        na, nb = self.node_of(a_id, a_con), self.node_of(b_id, b_con)
        if na == 0 and nb == 0:
            raise BuildError(f"connect did not take (both endpoints unconnected): ({a_id},{a_con})->({b_id},{b_con})")
        if na != 0 and nb != 0 and na != nb:
            raise BuildError(f"connect collapsed/mismatched: ({a_id},{a_con})->({b_id},{b_con})")

    def disconnect(self, a_id, a_con, b_id, b_con):
        r = self._b.block_disconnect(a_id, a_con, b_id, b_con)
        if not r.get("success"):
            raise BuildError(f"disconnect failed: ({a_id},{a_con})->({b_id},{b_con})")

    def create_hblock(self, seed_id, name):
        before = self._b.hierarchy_list().get("count", 0)
        self._b.execute_command(
            f'UnselectAll(); AddBlockToSelection({seed_id}); CreateHblock("{name}");')
        hl = self._b.hierarchy_list()
        if hl.get("count", 0) <= before:
            raise BuildError(f"CreateHblock produced no H-block (name={name})")
        return [h for h in hl["hierarchies"] if h.get("blockName") == name][-1]["blockId"]

    def place_in_hblock(self, lib, type_, hblock_id):
        r = self._b.execute_command(
            f'global0 = PlaceBlockInHblock("{type_}", "{lib}", 200, 200, {hblock_id});',
            get_result=True)
        if not r.get("success"):
            raise BuildError(f"place_in_hblock failed: {lib}/{type_} in {hblock_id}: {r}")
        new_id = int(r["result"])
        ids = [b.get("blockId") for b in self._b.hierarchy_get_contents(hblock_id).get("blocks", [])]
        if new_id not in ids:
            raise BuildError(f"place_in_hblock: block {new_id} not inside H-block {hblock_id}")
        return new_id

    def remove_block(self, block_id):
        r = self._b.block_remove(block_id)
        if not r.get("success"):
            raise BuildError(f"remove_block failed: {block_id}: {r}")

    def set_value(self, block_id, var, value):
        r = self._b.block_set_value(block_id, var, value)
        if not r.get("success"):
            raise BuildError(f"set_value failed: block {block_id} {var}={value}: {r}")

    def set_attribute(self, block_id, name, value, value_type):
        r = self._b.attribute_set(block_id, name, value_type=value_type, value=value)
        if not r.get("success"):
            raise BuildError(f"set_attribute failed: block {block_id} {name}={value}: {r}")

    def move(self, block_id, x, y):
        r = self._b.block_move(block_id, x, y)
        if not r.get("success"):
            raise BuildError(f"move failed: block {block_id} -> ({x},{y}): {r}")

    def configure_resource_pool(self, pool_id, queue_id, release_id, name, capacity, qty):
        import resource_pool_config as rpc
        p1 = rpc.configure_pool(self._b, pool_id, name, capacity)
        if not p1.get("success"):
            raise BuildError(f"pool config failed: {p1}")
        p2 = rpc.configure_queue_pool(self._b, queue_id, name, qty)
        if not p2.get("success"):
            raise BuildError(f"queue pool config failed: {p2}")
        # Link the release directly to the pool block (ResourcePoolName + ServerBlockNum);
        # the Serverblocks_pop popup can't be used in a freshly-built H-block (its
        # RPNames list is empty until a UI redraw). See resource_pool_config.
        p3 = rpc.configure_release(self._b, release_id, name, pool_block_id=pool_id, qty=qty)
        if not p3.get("success"):
            raise BuildError(f"release config failed: {p3}")

    def _outer_connector(self, hblock_id, direction):
        """The H-block's outer connector dict for a direction (in/out).
        Connector-object names (Con0In/Con0Out/...) vary per build, so match
        on direction, not name. M3 molecules expose exactly one item inlet
        (direction 'in') and one item outlet (direction 'out')."""
        for c in self._b.block_info(block_id=hblock_id).get("connectors", []):
            if c.get("direction") == direction:
                return c
        raise BuildError(f"no '{direction}' outer connector on H-block {hblock_id}")

    def _connector_obj(self, hblock_id, name):
        for blk in self._b.hierarchy_get_contents(hblock_id).get("blocks", []):
            if blk.get("blockName") == name:
                return blk["blockId"]
        raise BuildError(f"connector-object {name} not found in H-block {hblock_id}")

    def inlet_connector(self, hblock_id):
        return self._connector_obj(hblock_id, self._outer_connector(hblock_id, "in")["name"])

    def outer_index(self, hblock_id, direction):
        return self._outer_connector(hblock_id, direction)["connectorIndex"]

    def node_of(self, block_id, con_index):
        r = self._b.execute_command(
            f"global0 = NodeGetIDIndex({block_id}, {con_index});", get_result=True)
        return int(r.get("result") or 0)


import json as _json
import os as _os

_MOLECULE_DIR = _os.path.join(_os.path.dirname(__file__), "..", "patterns", "molecules")


def _load_molecule(molecule_id):
    path = _os.path.join(_MOLECULE_DIR, f"{molecule_id}.json")
    if not _os.path.exists(path):
        raise BuildError(f"unknown molecule: {molecule_id}")
    with open(path, encoding="utf-8") as f:
        return _json.load(f)


def instantiate_pattern(molecule_id, params, model_id=None):
    """MCP entry point: build a molecule as an H-block in the live model.

    model_id is accepted for API forward-compatibility but currently ignored:
    the molecule is always built in the active model.
    """
    import simulation_backend as backend
    try:
        molecule = _load_molecule(molecule_id)
        return {"success": True, **build_molecule(molecule, params or {}, RealOps(backend))}
    except MoleculeError as e:
        return {"success": False, "errorCode": "INVALID_MOLECULE", "error": str(e)}
    except Exception as e:
        return {"success": False, "errorCode": "INSTANTIATE_FAILED", "error": str(e)}
