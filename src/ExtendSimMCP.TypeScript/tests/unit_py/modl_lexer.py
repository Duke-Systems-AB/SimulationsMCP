"""A minimal ModL string-literal lexer for tests - the rules measured live, 2026-09-23.

Inside a ModL string literal:

* a backslash is always an ordinary character - StrLen("a\\nb") == 4, a doubled
  backslash stays two characters, and ExtendSim's own code writes "\\" + name for a
  one-backslash path separator;
* ``"`` always ends the literal - there is no escape for it; a quote is written
  outside the literal as StrPutAscii(34);
* a literal may hold at most 255 characters ("String literals cannot be larger than
  255 characters" - a compile error modal).

So the only way a string can inject code is by ending its literal early. These helpers
split a command into literal contents and the code between them, which is what an
injection test has to look at.
"""
import re

MAX_LITERAL = 255


def split_modl(cmd: str):
    """Return (code_outside_literals, [literal contents]) or raise on an open literal."""
    code, literals = [], []
    i, n = 0, len(cmd)
    while i < n:
        if cmd[i] != '"':
            code.append(cmd[i])
            i += 1
            continue
        end = cmd.find('"', i + 1)
        if end < 0:
            raise ValueError(f"unterminated ModL string literal in: {cmd!r}")
        literals.append(cmd[i + 1:end])
        code.append('""')          # keep a placeholder so code stays readable
        i = end + 1
    return "".join(code), literals


_TOKEN = re.compile(r'\s*(?:"([^"]*)"|StrPutAscii\((\d+)\))\s*(\+)?')


def modl_string_value(expr: str) -> str:
    """Evaluate a ModL string expression built only from literals, `+` and
    StrPutAscii(n) - what _escape_modl_string produces inside its quotes."""
    out, rest = [], expr.strip()
    while rest:
        m = _TOKEN.match(rest)
        if not m:
            raise ValueError(f"not a plain ModL string expression: {rest!r}")
        if m.group(1) is not None:
            if len(m.group(1)) > MAX_LITERAL:
                raise ValueError(f"literal of {len(m.group(1))} characters exceeds {MAX_LITERAL}")
            out.append(m.group(1))
        else:
            out.append(chr(int(m.group(2))))
        rest = rest[m.end():]
        if not m.group(3) and rest.strip():
            raise ValueError(f"missing + before: {rest!r}")
    return "".join(out)
