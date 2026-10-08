"""Only one ExtendSim may run while the server works.

With two ExtendSim instances running (for example 2024 and 2026 side by side), COM attaches to
one of them while the window helpers - the "User Parameter" name prompt answerer and the dialog
dismisser - find their windows by title and may watch the other. A prompt in the instance COM
talks to is then never answered and the call hangs. The server therefore refuses every command
that needs ExtendSim while more than one instance runs, and says which ones to close.

Pure: the process list is injected, so the rule is tested without Windows or ExtendSim.
"""
import os
import time

MULTIPLE_EXTENDSIM_INSTANCES = "MULTIPLE_EXTENDSIM_INSTANCES"

# Commands that never talk to ExtendSim, so they keep working while the user closes an instance.
# extendsim_status is here too: it reports the instances instead of being refused.
OFFLINE_COMMANDS = frozenset({
    "extendsim_status", "list_patterns", "get_pattern", "template_list", "approve_pattern",
})


def _offline(command, params):
    """True when this call needs no ExtendSim. Mining is offline only on saved JSON: without a
    psgPath, mine_candidates extracts from a model through COM, and so does cluster_patterns
    for filePaths."""
    if command in OFFLINE_COMMANDS:
        return True
    params = params or {}
    if command == "mine_candidates":
        return bool(params.get("psgPath"))
    if command == "cluster_patterns":
        return not params.get("filePaths")
    return False


def same_session(processes, session):
    """(pid, path) of the (pid, path, session id) entries in the given Windows session."""
    return [(pid, path) for pid, path, sid in processes if sid == session]


def windows_processes():
    """(pid, executable path) of the processes in this Windows session. Another user's ExtendSim
    (fast user switching, Remote Desktop) is one the server cannot reach, so it does not count."""
    import win32api
    import win32process
    import win32ts
    query_limited = 0x1000          # PROCESS_QUERY_LIMITED_INFORMATION
    found = []
    for pid in win32process.EnumProcesses():
        try:
            handle = win32api.OpenProcess(query_limited, False, pid)
        except Exception:
            continue                # protected processes
        try:
            found.append((pid, win32process.GetModuleFileNameEx(handle, 0), win32ts.ProcessIdToSessionId(pid)))
        except Exception:
            pass
        finally:
            win32api.CloseHandle(handle)
    return same_session(found, win32ts.ProcessIdToSessionId(os.getpid()))


def extendsim_processes(lister):
    """The (pid, path) entries of lister() whose executable is ExtendSim.exe."""
    return [(pid, path) for pid, path in lister() if os.path.basename(path).lower() == "extendsim.exe"]


def check_single_instance(command, lister, params=None):
    """None when the command may run; the error to return when more than one ExtendSim runs.

    A process list that cannot be read never blocks the server: the guard is a safety net, and
    the command's own COM handling still reports a missing ExtendSim."""
    if _offline(command, params):
        return None
    try:
        found = extendsim_processes(lister)
    except Exception:
        return None
    if len(found) <= 1:
        return None
    return {
        "success": False,
        "errorCode": MULTIPLE_EXTENDSIM_INSTANCES,
        "error": (f"{len(found)} ExtendSim instances are running. The server works with one ExtendSim "
                  "at a time - with two, ExtendSim's prompts and messages can appear in the instance "
                  "the server is not connected to. Close all but one and try again."),
        "instances": [{"pid": pid, "path": path} for pid, path in found],
    }


class InstanceGuard:
    """check_single_instance over a process list cached for ttl_s (one scan takes ~13 ms;
    batch tools send many commands in a row)."""

    def __init__(self, lister=windows_processes, ttl_s=2.0, clock=time.monotonic):
        self._lister = lister
        self._ttl = ttl_s
        self._clock = clock
        self._cached = None
        self._at = None

    def processes(self):
        now = self._clock()
        if self._cached is None or now - self._at >= self._ttl:
            self._cached = self._lister()
            self._at = now
        return self._cached

    def check(self, command, params=None):
        return check_single_instance(command, self.processes, params)
