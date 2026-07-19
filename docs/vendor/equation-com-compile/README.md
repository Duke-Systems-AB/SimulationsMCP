# Proposal: COM/OLE-drivable equation compilation for the Equation block

**To:** ExtendSim block engineering (ANDRITZ / Imagine That)
**From:** Duke Systems AB — building a COM/OLE automation layer for ExtendSim 2026 Pro
**Date:** 2026-07-18
**Re:** Value library **Equation** block — letting automation clients compile & validate an equation

---

## Summary

We drive ExtendSim 2026 Pro over COM/OLE (`ExtendSim.Application`) to build and validate models
programmatically. One gap blocks us: **an automation client cannot compile or validate an
Equation block's equation.** There is no OLE entry point for it, and the only feedback path
(the interactive compile) surfaces errors through modal dialogs that deadlock an automation host.

We prototyped a small, self-contained fix — a single dialog-item handler, **`CompileAndReport`**
(see `CompileAndReport.modl`) — and proved it works for valid equations. We also found a
**compiler-level issue** that only you can fully resolve: the silent compile primitive **hangs on
invalid equations when invoked outside a running simulation.**

This document describes both, with reproduction steps, so the capability can be adopted into the
stock block.

---

## 1. The gap

`app.Execute()` over COM runs in **system context**. From there a client cannot:
- call the equation compiler, nor
- read the block-private `EQ_CompiledEquation` STAT array to learn whether a compile succeeded.

The block *does* compile equations — at `on OK`, `on CheckData`, `on Simulate`, and via the
compile-button message — but every one of those is reached only through GUI interaction or a full
run, and reports syntax errors via a **modal `UserError` dialog**. A headless automation host has
no user to dismiss the modal, so the process wedges.

Net: today there is no way to ask, from automation, *"is this equation valid?"* before relying on
the block.

## 2. What we found — the in-block bridge

A dialog-item handler runs in **block context**, where the compiler and the compiled-code array
*are* reachable. And crucially: **setting a dialog variable from COM fires that item's
`on <var>` handler.** That is the bridge:

```
COM  SetDialogVariable(blockNum, "CompileAndReport", 1)
        →  block runs  on CompileAndReport { ... compile ... write globals ... }
COM  Request("global0") / Request("globalStr0")   ←  reads the verdict
```

We verified the bridge independently with a throwaway block (a checkbox handler that wrote a
sentinel to `globalStr0`; COM read it back).

## 3. The proposed handler

See **`CompileAndReport.modl`**. It:

1. `EQ_TranslateEquation()` — translate `EQ_EquationText` into the internal form.
2. `EquationCompileDynamicVariablesSilent(...)` — the **silent** compile primitive (the same one
   `EQ_Compile()` uses at `OpenModel`), which returns an error code instead of showing a dialog.
3. Writes the verdict to COM-readable globals: `global0` (1/0) and `globalStr0` (error indication).

Client usage:

```python
SetDialogVariable(blockNum, "EQ_EquationText",  "outCon0 = 123 + 456;", 0, 0)
SetDialogVariable(blockNum, "CompileAndReport", 1,                      0, 0)  # fires the handler
ok  = Request("global0")     # "1" = compiled OK, "0" = error
err = Request("globalStr0")  # "" on success
```

It requires one added dialog item: a checkbox named `CompileAndReport`.

## 4. Result

- **Valid equation** (`outCon0 = 123 + 456;`): the client reads `global0 = "1"`, `globalStr0 = ""`.
  End-to-end, no dialog, no run, no wedge. **Works.**

## 5. KNOWN ISSUE (needs a vendor fix)

- **Invalid equation:** `EquationCompileDynamicVariablesSilent(...)` **hangs** when invoked
  outside a running simulation. No dialog appears (we confirmed with a UI-Automation watcher
  running throughout — zero dialogs), so there is nothing to dismiss; the COM call never returns
  and ExtendSim must be force-restarted.
- The **loud** `EQ_Compile(FALSE)` path, when fired from this same handler, also wedged the COM
  call on an invalid equation (it did not complete and return control to the client).
- Reproduced with a syntax error (`outCon0 = 1 +;`) and with an undefined variable
  (`outCon0 = zzz;`). Both hang at the compile call.

**Our hypothesis:** the compile primitives assume a run/init context (populated `iVars_Values` /
`oVars_Values`, completed `CheckData`/`InitSim`). Called on a freshly placed, un-initialized block,
the error path enters a bad state and hangs instead of returning a code. During a real run the same
compiler reports errors cleanly (as a dismissable dialog).

## 6. Requests

1. **Make `EquationCompileDynamicVariablesSilent(...)` robust out-of-run** — return a nonzero error
   code on invalid input regardless of run/init state, never blocking. This alone makes the handler
   above a complete compile-and-validate API.
2. **Consider a first-class OLE entry point**, e.g.
   `CompileEquationBlock(blockNum) -> {ok, errorText}`, so automation clients get a supported
   compile+validate call (with the human-readable error text the loud path already produces) without
   needing a custom dialog item.

## 7. Reproduction / artifacts

- `CompileAndReport.modl` — the added handler (drop-in; English, commented).
- We built a working copy by: copying `Value.lbr`, adding a `CompileAndReport` checkbox to the
  Equation block, appending the handler to the block source, and recompiling — all 13 equation
  includes resolved and the block compiled cleanly. The copy is available on request.
- The `on OK` and `on CheckData` handlers already invoke `EQ_Compile()`; the loud vs. silent branch
  is in `EQ_Compile()` in `Equation.h` (silent is used only at `PHASE_OPEN_MODEL`).

Happy to demo the working block over a call.
