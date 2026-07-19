# Proposal: COM/OLE-drivable Python-code injection for the Python Bridge block

**To:** ExtendSim block engineering — ANDRITZ / Imagine That
**From:** Duke Systems AB — COM automation layer for ExtendSim 2026 Pro
**Date:** 2026-07-18
**Re:** Value library **Python Bridge** block — set the Python script from automation

> Companion to the Equation block proposal (`../equation-com-compile/`). Same underlying
> mechanism: a dialog-item handler, fired from COM, doing in-block work a COM client cannot.

---

## Summary

An automation client cannot set a Python Bridge block's script over COM/OLE. The script lives in
`dsPythonCode[]`, a block-private STAT array that the dialog API cannot address, and the only way
to enter code is the interactive editor. We prototyped an **in-block handler pattern** that lets a
COM client populate `dsPythonCode[]` line by line, and **proved it round-trips arbitrary
multi-line Python** on a purpose-built block. We ask you to adopt an equivalent injection (and
registration) entry point in the stock Python Bridge.

## 1. The gap

The Python Bridge stores its script in `dsPythonCode[]` (a `String` array, one line per element).
That array is block-private STAT storage, and a COM client cannot reach it:

```
SetDialogVariable(blockNum, "dsPythonCode", row, 0)
    →  "Row or column index out of range for static variable"
```

The widget `PythonScript_frm` is only the editor UI, not the storage — reading it returns the
editor title, not code. So today the script can only be entered by hand in the editor; there is no
automation path to set it.

## 2. What we found — the same in-block bridge

A dialog-item handler runs in **block context**, where `dsPythonCode[]` *can* be indexed directly.
And setting a dialog variable from COM fires that item's `on <var>` handler. So a client can drive
the block to populate its own array:

```
COM  SetDialogVariable(blockNum, "PY_InjectTrig", 1)
        →  block runs  on PY_InjectTrig { dsPythonCode[PY_LineIndex] = PY_InjectBuf; }
```

One line per element sidesteps the fact that a ModL string literal cannot carry newlines — the
client iterates lines (natural on its side) and sets a one-line buffer per row. See
`PY_InjectHandlers.modl` for the three small handlers (size / inject / read-back).

## 3. Result — proven

On a purpose-built block declaring `String dsPythonCode[];` and the three handlers, a COM client:

```
PY_SizeTrig = 3                                  → dsPythonCode[] sized to 3   (global0 = "3")
inject 3 lines (one per index) via PY_InjectTrig
read back via PY_ReadTrig →
  ["import numpy as np", "x = np.arange(10)", "result = x.sum()"]
match = TRUE
```

Arbitrary multi-line Python injected from COM into `dsPythonCode[]` and read back **verbatim**, with
no dialog and no wedge.

## 4. Scope of the proof (honest)

- **Proven:** COM → in-block handler → populate `dsPythonCode[]` (arbitrary multi-line) → read back
  exact. Demonstrated on a block using the *same* String-array + handler pattern the Python Bridge
  uses.
- **Not yet demonstrated on the stock block:** we did not hand-patch the real Python Bridge — its
  large blob contains non-ASCII bytes that make in-place source surgery risky. We *did* successfully
  modify and recompile your real **Equation** block with an added handler (see the companion
  proposal), so the modify-and-recompile technique itself is established; we simply didn't want to
  risk corrupting the Python Bridge blob to demo it.
- **Execution is a separate step:** populating `dsPythonCode[]` is injection, not execution. To run,
  the block must register the code with its Python editor/runtime (the stock block already uses
  `PythonEditorAddRelatedCode(scriptID, dsPythonCode, append)` and `PythonEditorFetch(...)`), and
  Python must be initialized with a trigger configured.

## 5. Requests

1. **A COM/OLE way to set the script** — either make `dsPythonCode[]` settable through a supported
   entry point, or add inject handlers like `PY_InjectHandlers.modl` to the stock Python Bridge so
   automation can populate the script array.
2. **Register + (optionally) execute in the same call** — an entry point such as
   `SetPythonScript(blockNum, lines[]) -> {ok, errorText}` that stores the lines into
   `dsPythonCode[]` **and** calls `PythonEditorAddRelatedCode(...)` so the injected code is
   immediately part of the runnable script, ideally reporting any Python syntax error back to the
   client (mirroring request #1 in the Equation proposal).

## 6. Artifacts

- `PY_InjectHandlers.modl` — the three handlers (size / inject / read-back), English, commented.
- The purpose-built demo block and the COM round-trip test are available on request.
- Related storage/registration APIs already in the block:
  `EQPY_TAB1_PythonCode.modl` — `PythonEditorFetch`, `PythonEditorAddRelatedCode`, `PythonEditorOpen`.

Happy to demo over a call, alongside the Equation block proposal.
