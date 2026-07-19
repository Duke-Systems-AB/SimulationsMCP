"""Offline reader for a block's STAT storage-variable table from its .lbr blob.

The STAT section of a compiled block blob lists the block's internal ModL
"static" storage variables (e.g. dsPythonCode, EQ_EquationText) whose names are
invisible to the live COM dialog API. Ported from the blob_research STATEntry
decoder (build MCP). Pure file/bytes operation - no COM, no ExtendSim.
"""
from __future__ import annotations
import sqlite3
import struct
from dataclasses import dataclass

# data_type codes (blob_research Phase 6 Track C, verified across 9538 entries).
# data_type is the element/storage CLASS, not the shape: codes 3 and 6 appear on
# both scalar and dimensioned numeric vars, so the labels avoid shape words. The
# authoritative shape is is_scalar / dim_count, never the label.
_DATA_TYPE_LABELS = {
    3: "numeric", 6: "numeric", 7: "string",
    8: "specialized8", 9: "specialized9", 10: "specialized10", 12: "specialized12",
}


@dataclass
class StatVar:
    name: str
    data_type: int
    data_type_label: str
    is_scalar: bool
    dim_count: int
    dim_sizes: tuple[int, int]  # (dim_size_1, dim_size_2)


def _parse_stat_entry(data: bytes, pos: int) -> "tuple[StatVar, int]":
    # Per-entry layout: 52-byte header (13 BE u32) + id_value(u32) + name_len(u32)
    # + name(UTF-16BE). u32s: [0]=data_type [5]=dim_size_1 [6]=is_scalar
    # [7]=dim_count [8]=dim_size_2.
    header = data[pos:pos + 52]
    u32s = struct.unpack(">13I", header)
    nl_pos = pos + 52 + 4  # skip 52-byte header + 4-byte id_value
    name_len = struct.unpack(">I", data[nl_pos:nl_pos + 4])[0]
    name = data[nl_pos + 4:nl_pos + 4 + name_len].decode("utf-16-be")
    next_pos = nl_pos + 4 + name_len
    dt = u32s[0]
    return StatVar(
        name=name,
        data_type=dt,
        data_type_label=_DATA_TYPE_LABELS.get(dt, f"unknown({dt})"),
        is_scalar=bool(u32s[6]),
        dim_count=u32s[7],
        dim_sizes=(u32s[5], u32s[8]),
    ), next_pos


def _locate_stat_section(blob: bytes) -> bytes:
    dlog = blob.find(b"DLOG")
    tabn = blob.find(b"TABN", dlog) if dlog >= 0 else -1
    stat = blob.find(b"STAT", tabn) if tabn >= 0 else -1
    view = blob.find(b"VIEW", stat) if stat >= 0 else -1
    if stat < 0 or view < 0 or view <= stat:
        raise ValueError("STAT/VIEW section markers not found in blob")
    return blob[stat:view]


def parse_stat_variables(blob: bytes) -> "list[StatVar]":
    section = _locate_stat_section(blob)  # slice starts at the b"STAT" marker
    if len(section) < 8:
        raise ValueError("bad STAT section: shorter than the 8-byte header")
    entry_count = struct.unpack(">I", section[4:8])[0]
    out: list[StatVar] = []
    pos = 8
    for i in range(entry_count):
        if pos >= len(section):
            raise ValueError(
                f"truncated STAT section: only {i}/{entry_count} entries fit in "
                f"{len(section)} bytes"
            )
        try:
            entry, pos = _parse_stat_entry(section, pos)
        except (struct.error, UnicodeDecodeError, IndexError) as e:
            raise ValueError(f"corrupt STAT entry {i} at offset {pos}: {e}") from e
        out.append(entry)
    return out


def read_stat_variables(lbr_path: str, block_name: str) -> "list[StatVar]":
    con = sqlite3.connect(lbr_path)
    try:
        row = con.execute(
            "SELECT blockBlob FROM VocabsTable WHERE blockName=?", (block_name,)
        ).fetchone()
        if row is None:
            row = con.execute(
                "SELECT blockBlob FROM VocabsTable "
                "WHERE lower(trim(blockName))=lower(trim(?))", (block_name,)
            ).fetchone()
    finally:
        con.close()
    if row is None:
        raise ValueError(f"block {block_name!r} not found in {lbr_path}")
    return parse_stat_variables(row[0])
