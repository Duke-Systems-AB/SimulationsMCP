# src/resource_pool_config.py
"""Resource Pool configuration for a functional resource-constrained machine.

Pure cores take an injected `backend` and are effect-verified + fail-closed.
Live-verified recipe (2026-07-12): 49 items through a full acquire/use/release
cycle, configured entirely in code. See spec 2026-07-12-resource-machine-design.md.

COM facts: ResourcePoolName / Queue ResourceTable are string cells written via
SetDialogVariable (ResourceTable has no _ttbl suffix, so _set_var would silently
no-op) and read back via GetDialogVariable-as-string. The Release block links to
its pool via its own ResourcePoolName + ServerBlockNum (resolved live at CheckData
by the block's FindRPBlock, matching a Resource Pool with that name in the same
H-block) — NOT via the Serverblocks_pop popup, whose RPNames list stays empty in a
freshly-built H-block (confirmed from the block ModL source).
"""


# Local copy of simulation_backend._error (also duplicated in attribute_config.py
# and dialog_table.py) — deliberate: keeps this zero-dep module importable
# without a module-level import of simulation_backend (win32com) or a shared
# helper module.
def _err(code, message, **extra):
    result = {"success": False, "errorCode": code, "error": message}
    result.update(extra)
    return result


def _preflight(backend, block_id, block_type):
    app = backend.get_extendsim_app()
    mc = backend._validate_model_open(app)
    if not mc.get("success"):
        return app, mc
    tc = backend._validate_block_type(app, block_id, block_type)
    if not tc.get("success"):
        return app, tc
    return app, None


def configure_pool(backend, block_id, name, capacity):
    """Set a Resource Pool's name (string) + capacity (numeric), verified."""
    app, err = _preflight(backend, block_id, "Resource Pool")
    if err:
        return err
    try:
        backend._set_dialog_var(app, block_id, "ResourcePoolName", str(name))
        backend._set_var(app, block_id, "NumServ", capacity, 0, 0, 1)
    except Exception as e:
        return _err("POOL_CONFIG_FAILED", str(e), blockId=block_id)
    try:
        name_rb = backend._get_dialog_string(app, block_id, "ResourcePoolName")
    except Exception as e:
        return _err("POOL_CONFIG_READ_FAILED", str(e), blockId=block_id)
    if str(name_rb) != str(name):
        return _err("POOL_CONFIG_REJECTED", f"pool name on block {block_id} did not persist",
                    blockId=block_id, requested=str(name), actual=str(name_rb))
    return {"success": True, "blockId": block_id, "name": name, "capacity": capacity}


def configure_queue_pool(backend, block_id, pool_name, qty=1):
    """Put a Queue in Resource Pool mode and point it at pool_name (by name)."""
    app, err = _preflight(backend, block_id, "Queue")
    if err:
        return err
    try:
        backend._set_var(app, block_id, "QueueType_pop", 2, 0, 0, 1)
        backend._set_dialog_var(app, block_id, "ResourceTable", str(pool_name), 0, 0)
        backend._set_dialog_var(app, block_id, "ResourceTable", qty, 0, 1)
    except Exception as e:
        return _err("QUEUE_POOL_FAILED", str(e), blockId=block_id)
    try:
        name_rb = backend._get_dialog_string(app, block_id, "ResourceTable", 0, 0)
    except Exception as e:
        return _err("QUEUE_POOL_READ_FAILED", str(e), blockId=block_id)
    if str(name_rb) != str(pool_name):
        return _err("QUEUE_POOL_REJECTED", f"ResourceTable on block {block_id} did not persist",
                    blockId=block_id, requested=str(pool_name), actual=str(name_rb))
    return {"success": True, "blockId": block_id, "poolName": pool_name, "qty": qty}


def configure_release(backend, block_id, pool_name, pool_block_id=None, qty=1):
    """Point a Resource Pool Release at pool_name.

    The block's real link is its own ResourcePoolName + ServerBlockNum, which its
    FindRPBlock() resolves live at CheckData by matching a "Resource Pool" block
    with that ResourcePoolName within the same H-block (verified from the block's
    ModL source). The Serverblocks_pop popup only SETS those two vars from an
    RPNames list that stays empty until a UI redraw — so it is unusable in a
    freshly-built H-block. Set the two link vars directly instead, effect-verified.

    pool_block_id is the Resource Pool block's id (ServerBlockNum). When omitted it
    is resolved by name via backend.find_resource_pool (matches FindRPBlock)."""
    app, err = _preflight(backend, block_id, "Resource Pool Release")
    if err:
        return err
    if pool_block_id is None:
        pool_block_id = backend.find_resource_pool(app, pool_name)
    if pool_block_id is None or int(pool_block_id) < 0:
        return _err("RELEASE_POOL_NOT_FOUND",
                    f"no Resource Pool named '{pool_name}' found for release block {block_id}",
                    blockId=block_id, poolName=pool_name)
    try:
        backend._set_var(app, block_id, "NumReleased_PRM", qty, 0, 0, 1)
        backend._set_dialog_var(app, block_id, "ResourcePoolName", str(pool_name))
        backend._set_dialog_var(app, block_id, "ServerBlockNum", int(pool_block_id))
    except Exception as e:
        return _err("RELEASE_CONFIG_FAILED", str(e), blockId=block_id)
    try:
        name_rb = backend._get_dialog_string(app, block_id, "ResourcePoolName")
    except Exception as e:
        return _err("RELEASE_CONFIG_READ_FAILED", str(e), blockId=block_id)
    if str(name_rb) != str(pool_name):
        return _err("RELEASE_CONFIG_REJECTED",
                    f"ResourcePoolName on release block {block_id} did not persist",
                    blockId=block_id, requested=str(pool_name), actual=str(name_rb))
    return {"success": True, "blockId": block_id, "poolName": pool_name,
            "poolBlock": int(pool_block_id), "qty": qty}
