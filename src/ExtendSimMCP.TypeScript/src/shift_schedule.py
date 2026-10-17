"""Shift block schedules (2026-10-04).

ExtendSim's Shift block holds its schedule in ShiftTable with the columns Time; Number or on/off;
Description - one row per CHANGE ([0, "On"], [8, "Off"]), measured on ExtendSim 2026 against the
vendor's own example models. The server's input stays a list of periods {startTime, endTime,
capacity}; this module turns it into change rows and writes them through an injected cell object
(row_count / set / get), reading every cell back. No COM import here.
"""

# ShiftStatusTypePop values. Measured live 2026-10-05 (ExtendSim 2024 and 2026): the popup's
# options are "On/Off;Number", so 1 = on/off and 2 = numbers; a numbers row such as [0, "3"]
# is kept and reads back as written.
ON_OFF, NUMBERS = 1, 2


def _num(v):
    f = float(v)
    return str(int(f)) if f.is_integer() else repr(f)


def change_rows(schedule, status_type):
    numbers = status_type == NUMBERS
    periods = sorted(schedule or [], key=lambda p: float(p.get("startTime", 0)))
    rows = []
    for i, p in enumerate(periods):
        if p.get("endTime") is None:
            raise ValueError(f"period {i + 1} has no endTime")
        start, end = float(p.get("startTime", 0)), float(p["endTime"])
        cap = float(p.get("capacity", 1))
        if not numbers and cap not in (0, 1):
            # An on/off shift can only say On or Off: a capacity of 3 would silently become "On".
            raise ValueError(f"period {i + 1}: capacity {_num(cap)} needs a numbers shift (statusType 2); "
                             "an on/off shift takes capacity 0 (off) or 1 (on)")
        if end <= start:
            raise ValueError(f"period {i + 1}: endTime {_num(end)} must be after startTime {_num(start)}")
        if i + 1 < len(periods) and float(periods[i + 1].get("startTime", 0)) < end:
            raise ValueError(f"periods {i + 1} and {i + 2} overlap")
        rows.append((_num(start), _num(cap) if numbers else ("On" if cap > 0 else "Off")))
        nxt = float(periods[i + 1].get("startTime", 0)) if i + 1 < len(periods) else None
        if nxt != end:
            rows.append((_num(end), "0" if numbers else "Off"))
    return rows


def write(cells, rows):
    n = cells.row_count()
    if len(rows) > n:
        raise ValueError(f"the schedule needs {len(rows)} change rows but this Shift block's table has "
                         f"{n} rows; use fewer periods")
    wanted = [(t, s) for t, s in rows] + [("", "")] * (n - len(rows))
    for r, (t, s) in enumerate(wanted):
        cells.set(r, 0, t)
        cells.set(r, 1, s)
    for r, (t, s) in enumerate(wanted):
        got = (cells.get(r, 0), cells.get(r, 1))
        if got != (t, s):
            raise RuntimeError(f"Shift table row {r} reads back {got}, expected {(t, s)}")


def status_type_mismatch(got, wanted):
    """None when the ShiftStatusTypePop value read back (`got`, as COM returns it) equals the
    `wanted` status type; otherwise why the write did not take. ExtendSim can keep the old popup
    value without an error (e.g. a value outside the popup's options), so the write is only
    reported once it reads back."""
    try:
        if int(float(got)) == int(wanted):
            return None
    except (TypeError, ValueError):
        pass
    return (f"write of ShiftStatusTypePop did not take: the block kept {got!r}, not {wanted} "
            f"({ON_OFF} = on/off, {NUMBERS} = numbers)")
