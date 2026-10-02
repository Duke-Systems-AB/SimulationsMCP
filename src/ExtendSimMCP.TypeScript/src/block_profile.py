# src/block_profile.py
"""block_profile: what a block is and what it offers, read through COM from ExtendSim and from
ExtendSim's installed help - never from the library file (vendor rule 2026-09-30)."""
import json
import os
import re
import tempfile
import uuid
from pathlib import Path

from block_com_profile import Com, profile_via_com
from block_help import help_for

UNKNOWN_WITHOUT_CODE = ["what a setting resets", "the order settings must be written in",
                        "whether a setting's handler opens a dialog box"]
_SRC = Path(__file__).resolve().parent
_YEAR = re.compile(r"ExtendSim_(\d{4})_Pro", re.I)
_GUIDE_STRING_MAX = 1990
_GUIDE_LIST_MAX = 50


def _err(code, message, **extra):
    r = {"success": False, "errorCode": code, "error": message}
    r.update(extra)
    return r


def _reference():
    data = json.loads((_SRC / "block_reference.json").read_text(encoding="utf-8"))
    return {lib: [b for cat in v.get("categories", {}).values() for b in cat.get("blocks", {})]
            for lib, v in data.get("libraries", {}).items()}


def _official_guides():
    data = json.loads((_SRC / "modeling_guides.json").read_text(encoding="utf-8"))
    return set((data.get("blocks") or {}).keys())


def _docs_year_dir(library_dir):
    m = _YEAR.search(library_dir or "")
    return Path(library_dir).parent if m else None


def _default_app():
    import simulation_backend as sb
    return sb.get_extendsim_app()


def block_profile_command(library, block=None, detail=False, com=None, help_lookup=None, reference=None,
                          guides=None, temp_dir=None, app_lookup=None):
    if not library.lower().endswith(".lbr"):
        return _err("INVALID_PARAMETER", f"{library!r} is not an ExtendSim library file name (.lbr)")
    library_path = None
    if "/" in library or "\\" in library:
        # A full path: ExtendSim opens the library if needed (OpenExtendFile); we only check it
        # exists - its content is never read here (vendor rule 2026-09-30).
        if not os.path.isfile(library):
            return _err("INVALID_PARAMETER", f"library file not found: {library!r}")
        library_path, library = library, os.path.basename(library.replace("\\", "/"))
    reference = _reference() if reference is None else reference
    library = next((lib for lib in reference if lib.lower() == library.lower()), library)  # canonical spelling
    if block is None:
        if library in reference:
            return {"success": True, "library": library, "blocks": reference[library]}
        return _err("INVALID_PARAMETER", f"the blocks of {library!r} cannot be listed through ExtendSim; "
                                         "give the block name (as shown in ExtendSim's library window)")
    if com is None:
        app = (app_lookup or _default_app)()
        if app is None:
            return _err("EXTENDSIM_NOT_RUNNING", "block_profile asks ExtendSim about the block: start ExtendSim first")
        import simulation_backend as sb
        com = Com(sb, app)
    temp = os.path.join(temp_dir or tempfile.gettempdir(), f"block_profile_{uuid.uuid4().hex}.mox")
    r = profile_via_com(com, library, block, temp, library_path=library_path)
    if not r.get("success"):
        return r
    lookup = help_lookup or (lambda b, d, x: help_for(b, d, x))
    help_ = lookup(block, _docs_year_dir(r["libraryDir"]), detail)
    settings = [i for i in r["items"] if i["setting"]]
    profile = {"block": block, "library": library, "source": "com", "settings": settings,
               "tabsCount": (max((i["tab"] for i in r["items"]), default=-1) + 1),
               "help": help_, "unknownWithoutCode": UNKNOWN_WITHOUT_CODE}
    if help_ is None:
        profile["helpNote"] = "no help file (.chm) found for this block - common for your own libraries"
    guides = _official_guides() if guides is None else guides
    if any(g.partition("/")[0].lower() == library.lower() and g.partition("/")[2] == block for g in guides):
        profile["officialGuide"] = "block_search detail: true - measured recipes and pitfalls"
    if detail:
        profile["items"] = r["items"]
    out = {"success": True, "profile": profile}
    if r.get("libraryOpened"):
        out["libraryOpened"] = True
    if r.get("closeProblem"):
        out["closeProblem"] = r["closeProblem"]
    return out


def _cap(text):
    return text if len(text) <= _GUIDE_STRING_MAX else text[:_GUIDE_STRING_MAX - 3] + "..."


def _meaning(s):
    parts = [s["kind"]]
    if s.get("label"):
        parts.append(f"label: {s['label']}")
    if s.get("options"):
        parts.append("options: " + "; ".join(s["options"]))
    if s.get("title"):
        parts.append(f"text: {s['title']}")
    return _cap(" - ".join(parts))


def draft_block_guide(profile):
    help_ = profile.get("help") or {}
    display_only = [s["name"] for s in profile["settings"] if s.get("displayOnly")]
    pitfalls = [
        "Write settings one at a time and check each took (block_set_value reads it back); a dialog box may appear.",
        _cap("Not known without the block's code: " + "; ".join(profile.get("unknownWithoutCode", [])) + "."),
    ]
    if display_only:
        pitfalls.append(_cap("Display only, cannot be written: " + ", ".join(display_only)))
    summary = help_.get("summary") or f"{profile['block']} block (no help file found)."
    return {"library": profile["library"], "block": profile["block"], "provedOn": [],
            "summary": _cap(summary), "useWhen": [], "notFor": [], "howItWorks": _cap(summary),
            "settings": [{"name": s["name"], "tool": "block_set_value", "meaning": _meaning(s), "proved": False}
                         for s in profile["settings"]][:_GUIDE_LIST_MAX],
            "recipes": [], "notYetProved": ["Everything: read from ExtendSim, not run"],
            "pitfalls": pitfalls[:_GUIDE_LIST_MAX]}


def block_guide_draft_command(library, block, com=None, help_lookup=None, temp_dir=None, app_lookup=None):
    r = block_profile_command(library, block, detail=True, com=com, help_lookup=help_lookup, temp_dir=temp_dir,
                              app_lookup=app_lookup)
    if not r.get("success"):
        return r
    return {"success": True, "draft": draft_block_guide(r["profile"]),
            "needsInput": ["useWhen", "notFor", "settings[].meaning in the user's words"]}
