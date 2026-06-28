# src/patterns.py
"""Discovery over the molecule + flow library (M5). Pure file I/O, no COM."""
import os
import json

_BASE = os.path.join(os.path.dirname(__file__), "..", "patterns")
_DIRS = {"molecule": "molecules", "flow": "flows"}


def _iter_defs():
    for kind, sub in _DIRS.items():
        d = os.path.join(_BASE, sub)
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if fn.endswith(".json"):
                with open(os.path.join(d, fn), encoding="utf-8") as f:
                    yield kind, json.load(f)   # raises on broken JSON (fail-closed)


def list_patterns(intent=None):
    out = []
    for kind, d in _iter_defs():
        if intent and intent.lower() not in d.get("intent", "").lower():
            continue
        out.append({
            "id": d.get("id"),
            "kind": kind,
            "intent": d.get("intent", ""),
            "params": d.get("params", {}),
            "interface": d.get("interface", {}),
        })
    return {"success": True, "patterns": out, "count": len(out)}


def get_pattern(pattern_id):
    for kind, d in _iter_defs():
        if d.get("id") == pattern_id:
            return {"success": True, "kind": kind, "pattern": d}
    return {"success": False, "errorCode": "UNKNOWN_PATTERN",
            "error": f"unknown pattern: {pattern_id}"}
