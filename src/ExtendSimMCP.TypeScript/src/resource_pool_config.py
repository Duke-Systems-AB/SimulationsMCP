# src/resource_pool_config.py
"""Resource Pool configuration for a functional resource-constrained machine.

Pure cores take an injected `backend` and are effect-verified + fail-closed.
Live-verified recipe (2026-07-12): 49 items through a full acquire/use/release
cycle, configured entirely in code. See spec 2026-07-12-resource-machine-design.md.

COM facts: ResourcePoolName / Queue ResourceTable are string cells written via
SetDialogVariable (ResourceTable has no _ttbl suffix, so _set_var would silently
no-op) and read back via GetDialogVariable-as-string. The Release block must
select its pool via Serverblocks_pop (int index); the right index is found by
setting it and reading back ResourcePoolName until it matches, else the sim
aborts at t=0 (CHECKDATA).
"""

MAX_POOL_INDEX = 32     # upper bound for the Serverblocks_pop index search


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


def configure_release(backend, block_id, pool_name, qty=1):
    """Point a Resource Pool Release at pool_name. Serverblocks_pop is an int
    index into the model's pools; find the index whose ResourcePoolName readback
    matches pool_name (robust to other pools). Fail-closed if none matches."""
    app, err = _preflight(backend, block_id, "Resource Pool Release")
    if err:
        return err
    found = None
    try:
        for idx in range(1, MAX_POOL_INDEX + 1):
            backend._set_var(app, block_id, "Serverblocks_pop", idx, 0, 0, 1)
            if str(backend._get_dialog_string(app, block_id, "ResourcePoolName")) == str(pool_name):
                found = idx
                break
    except Exception as e:
        return _err("RELEASE_CONFIG_FAILED", str(e), blockId=block_id)
    if found is None:
        return _err("RELEASE_POOL_NOT_FOUND",
                    f"no Serverblocks_pop index selects pool '{pool_name}' on block {block_id}",
                    blockId=block_id, poolName=pool_name)
    try:
        backend._set_var(app, block_id, "NumReleased_PRM", qty, 0, 0, 1)
    except Exception as e:
        return _err("RELEASE_CONFIG_FAILED", str(e), blockId=block_id)
    return {"success": True, "blockId": block_id, "poolName": pool_name,
            "poolIndex": found, "qty": qty}


def configure_pool_entry(block_id, name, capacity):
    import simulation_backend as backend
    return configure_pool(backend, block_id, name, capacity)


def configure_queue_pool_entry(block_id, pool_name, qty=1):
    import simulation_backend as backend
    return configure_queue_pool(backend, block_id, pool_name, qty)


def configure_release_entry(block_id, pool_name, qty=1):
    import simulation_backend as backend
    return configure_release(backend, block_id, pool_name, qty)
