# src/block_help.py
"""Help text for ExtendSim blocks from the installed .chm help files - ExtendSim's own
documentation, never the library file.
"""
from __future__ import annotations

import html
import os
import re
import shutil
import subprocess
from pathlib import Path


def html_to_text(html_str: str) -> str:
    t = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", html_str)
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    t = html.unescape(t).replace("\xa0", " ")
    return re.sub(r"\s+", " ", t).strip()


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def split_sentences(text: str) -> list[str]:
    text = text.strip()
    return [s for s in _SENTENCE_END.split(text) if s] if text else []


def first_sentences(text: str, n: int) -> str:
    """The first n sentences of text, naively split on . ! ? followed by whitespace."""
    return " ".join(split_sentences(text)[:n])


_PARAGRAPH = re.compile(r"(?is)<p\b[^>]*>(.*?)</p>")
_HEADING_TAG = re.compile(r"(?is)<h[1-3][^>]*>(.*?)</h[1-3]>")
_NUMBERED_HEADING = re.compile(r"^(\d+)\.\s*(\S.*)$")


def extract_sections(html_str: str) -> tuple[list[str], list[str]]:
    """(body paragraphs, section headings) from a decompiled help page.

    Prefers real <h1>-<h3> tags. ExtendSim's own block help (a Word/RoboHelp export)
    doesn't use those - measured against real .chm output, a heading there is a short
    paragraph whose whole text is "<number>. <title>"; a numbered body item (e.g. a
    variable-reference list) uses the same "N. " prefix, so a heading is additionally
    required to be short and colon-free to tell the two apart.
    """
    headings = [t for m in _HEADING_TAG.finditer(html_str) if (t := html_to_text(m.group(1)))]
    use_numbered = not headings
    body = []
    for m in _PARAGRAPH.finditer(html_str):
        t = html_to_text(m.group(1))
        if not t:
            continue
        nm = _NUMBERED_HEADING.match(t) if use_numbered else None
        if nm and len(nm.group(2)) <= 60 and ":" not in nm.group(2):
            headings.append(nm.group(2))
            continue
        body.append(t)
    return body, headings


def cut_at_boundary(paragraphs: list[str], limit: int = 4000) -> str:
    """Join paragraphs up to about `limit` characters, cut at a paragraph or sentence
    boundary - never mid-sentence (a single over-long sentence is the only exception).
    """
    out, total = [], 0
    for para in paragraphs:
        if out and total + len(para) + 1 > limit:
            break
        out.append(para)
        total += len(para) + 1
        if total >= limit:
            break
    text = " ".join(out)
    if len(text) > limit:
        kept, total = [], 0
        for s in split_sentences(text):
            if kept and total + len(s) + 1 > limit:
                break
            kept.append(s)
            total += len(s) + 1
        text = " ".join(kept) or text[:limit]
    return text.strip()


HH_TIMEOUT_S = 60


def find_chm(help_dir: str, block_name: str) -> str | None:
    if not os.path.isdir(help_dir):
        return None
    want = block_name.replace(" ", "_").lower() + "_b_help.chm"
    for f in os.listdir(help_dir):
        if f.lower() == want:
            return os.path.join(help_dir, f)
    return None


def help_cache_dir(year: str, env=os.environ) -> Path:
    base = env.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "SimulationsMCP" / "help-cache" / year


def _hh_runner(chm_path: str, out_dir) -> bool:
    hh = shutil.which("hh.exe") or shutil.which("hh")
    if not hh:
        return False
    try:
        subprocess.run([hh, "-decompile", str(out_dir), chm_path], timeout=HH_TIMEOUT_S, check=False,
                       capture_output=True)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return True


def decompile(chm_path: str, out_dir: Path, runner=None) -> bool:
    out_dir = Path(out_dir)
    if any(out_dir.rglob("*.htm*")):
        return True                                   # cached
    out_dir.mkdir(parents=True, exist_ok=True)
    ok = (runner or _hh_runner)(chm_path, out_dir)
    return bool(ok) and any(out_dir.rglob("*.htm*"))


def _largest_page(out_dir: Path) -> str:
    pages = sorted(out_dir.rglob("*.htm*"), key=lambda p: p.stat().st_size, reverse=True)
    if not pages:
        return ""
    raw = pages[0].read_bytes()
    for enc in ("utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return ""


def chm_text(chm_path: str, cache_dir: Path, runner=None) -> str:
    """Decompile (cached under cache_dir) and return the raw HTML of the .chm's largest
    page - "" on failure. Despite the name, this is the page's HTML, not plain text;
    callers that want plain text run it through html_to_text (see help_for below)."""
    out = Path(cache_dir) / Path(chm_path).stem
    if not decompile(chm_path, out, runner):
        return ""
    return _largest_page(out)


def help_for(block_name: str, docs_year_dir, detail: bool, runner=None, env=os.environ) -> dict | None:
    """docs_year_dir: Documents\\ExtendSim_<year>_Pro (None when unknown)."""
    if docs_year_dir is None:
        return None
    docs_year_dir = Path(docs_year_dir)
    chm = find_chm(str(docs_year_dir / "Help"), block_name)
    if not chm:
        return None
    year = "".join(c for c in docs_year_dir.name if c.isdigit())[:4] or "unknown"
    html_page = chm_text(chm, help_cache_dir(year, env), runner)
    if not html_page:
        return None
    text = html_to_text(html_page)
    paragraphs, headings = extract_sections(html_page)
    lead = " ".join(paragraphs) if paragraphs else text      # headings are not the summary
    out = {"source": "chm", "summary": first_sentences(lead, 3), "headings": headings}
    if detail:
        out["text"] = text
    return out
