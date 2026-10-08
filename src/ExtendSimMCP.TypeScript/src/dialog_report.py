"""What one dialog_watcher.py pass tells the caller about the boxes ExtendSim showed.

Pure (no win32, no COM), so both dialog_watcher.py (which writes the progress lines) and the
background dismisser in simulation_backend.py (which reads them) can import it.

A pass prints its JSON report on stdout only when it ends. A pass that is killed first - the
dismisser bounds each one with a subprocess timeout - prints nothing there, so it also writes one
progress line per box to stderr as it goes:

    DIALOG_PROGRESS {"seen": true}          a box is open; its text has not been read yet
    DIALOG_PROGRESS {"dialog": {...}}       a box's text was read (written BEFORE pressing it)

Bug found live on ExtendSim 2024, 2026-10-08: an aborted Optimizer run shows two plain OK boxes;
one pass read and pressed both, but each UIA press takes ~2 s to return, so the pass outlived its
8 s timeout and was killed - both texts were lost and optimizer_run said `dialogs: []` although
ExtendSim had shown two boxes. Fail closed: the AI must always learn that ExtendSim showed a box,
so a box whose text cannot be read is reported as UNREADABLE_BOX, never dropped.
"""
import json

PROGRESS_PREFIX = "DIALOG_PROGRESS "

# What the AI is told about a box ExtendSim showed but whose text could not be read.
UNREADABLE_BOX = "ExtendSim showed a message box whose text could not be read"

# dialog_watcher's Strategy 2 (SetForegroundWindow + Enter) placeholder for a box it pressed
# without being able to read it.
GHOST_PLACEHOLDER = "(text not readable in Ghost state)"


def progress_line(payload: dict) -> str:
    """One progress line (no newline): json.dumps escapes any newline inside a text."""
    return PROGRESS_PREFIX + json.dumps(payload)


def _parse_report(stdout):
    try:
        data = json.loads((stdout or "").strip())
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) and "found" in data else None


def _entry_texts(entry) -> list:
    """The texts to report for one box entry; a box with no readable text is UNREADABLE_BOX."""
    texts = [t for t in (entry.get("texts") or []) if isinstance(t, str) and t.strip()]
    readable = [t for t in texts if t != GHOST_PLACEHOLDER]
    return readable or [UNREADABLE_BOX]


def pass_found(stdout) -> bool:
    """True when a finished pass reported that it found (and dealt with) a box."""
    report = _parse_report(stdout)
    return bool(report and report.get("found"))


def texts_from_pass(stdout, stderr, finished: bool) -> list:
    """The box texts one pass reports, in order.

    finished: the pass ended by itself (not killed by the caller's timeout). Its JSON report on
    stdout is then the whole truth. Otherwise - or if stdout is not the expected JSON - the
    progress lines on stderr are used: every box read, plus UNREADABLE_BOX when a box was seen
    but none was read before the pass was stopped.
    """
    report = _parse_report(stdout) if finished else None
    if report is not None:
        out = []
        for entry in report.get("dialogs") or []:
            if isinstance(entry, dict):
                out.extend(_entry_texts(entry))
        return out

    out, seen = [], False
    for line in (stderr or "").splitlines():
        if not line.startswith(PROGRESS_PREFIX):
            continue
        try:
            payload = json.loads(line[len(PROGRESS_PREFIX):])
        except ValueError:
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("seen"):
            seen = True
        entry = payload.get("dialog")
        if isinstance(entry, dict):
            out.extend(_entry_texts(entry))
    if seen and not out:
        out.append(UNREADABLE_BOX)
    return out
