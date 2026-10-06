"""Read cell behaviour out of a Liberty (.lib) timing library.

Only the parts needed for logic simulation are kept: pin directions, the
boolean ``function`` of each output, and the ``ff`` group of flip-flops
(clock pin, next-state function, asynchronous clear/preset).

Liberty is a nested ``group (args) { attr : value ; ... }`` format. The full
SKY130 typical-corner file is ~10 MB, so the parsed result is cached as JSON.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

_TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|[A-Za-z0-9_.\-\[\]!&|^*+\'~]+|[(){}:;,]')


@dataclass
class FlipFlop:
    state: str           # internal state name, e.g. "IQ"
    clocked_on: str      # e.g. "CLK"
    next_state: str      # e.g. "D"
    clear: str | None = None    # e.g. "!RESET_B"
    preset: str | None = None   # e.g. "!SET_B"


@dataclass
class Cell:
    name: str
    pins: dict[str, str] = field(default_factory=dict)       # pin -> direction
    functions: dict[str, str] = field(default_factory=dict)  # output pin -> expression
    ff: FlipFlop | None = None

    @property
    def inputs(self) -> list[str]:
        return [p for p, d in self.pins.items() if d == "input"]

    @property
    def outputs(self) -> list[str]:
        return [p for p, d in self.pins.items() if d == "output"]


@dataclass
class Library:
    cells: dict[str, Cell]

    def pin_directions(self) -> dict[str, dict[str, str]]:
        return {name: dict(cell.pins) for name, cell in self.cells.items()}


def _strip(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    return text.replace("\\\n", " ")


def _parse_group(tokens: list[str], i: int):
    """Parse the body of a group starting after '{'. Returns (items, next_i).

    items is a list of ("attr", name, value) or ("group", name, args, items).
    """
    items = []
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        if tok == "}":
            return items, i + 1
        name = tok
        i += 1
        if tokens[i] == ":":
            value = tokens[i + 1]
            i += 2
            while tokens[i] != ";" and tokens[i] != "}":
                value += " " + tokens[i]  # unquoted multi-token value
                i += 1
            if tokens[i] == ";":
                i += 1
            items.append(("attr", name, value.strip('"')))
        elif tokens[i] == "(":
            j = i + 1
            args = []
            while tokens[j] != ")":
                if tokens[j] != ",":
                    args.append(tokens[j].strip('"'))
                j += 1
            i = j + 1
            if i < n and tokens[i] == "{":
                body, i = _parse_group(tokens, i + 1)
                items.append(("group", name, args, body))
            else:
                if i < n and tokens[i] == ";":
                    i += 1
                items.append(("attr", name, args))
        else:
            raise ValueError(f"unexpected token {tokens[i]!r} after {name!r}")
    return items, i


def _cells_from_text(text: str) -> dict[str, Cell]:
    cells: dict[str, Cell] = {}
    # Split at cell headers so we never tokenize the large library header twice.
    starts = [m.start() for m in re.finditer(r'\bcell\s*\(', text)]
    for k, start in enumerate(starts):
        end = starts[k + 1] if k + 1 < len(starts) else len(text)
        chunk = text[start:end]
        tokens = _TOKEN.findall(chunk)
        # tokens: cell ( "name" ) { ...
        name = tokens[2].strip('"')
        body, _ = _parse_group(tokens, 5)
        cell = Cell(name)
        for item in body:
            if item[0] != "group":
                continue
            _, gname, args, gbody = item
            attrs = {a[1]: a[2] for a in gbody if a[0] == "attr"}
            if gname == "pin":
                for pin in args:
                    cell.pins[pin] = attrs.get("direction", "input")
                    if "function" in attrs:
                        cell.functions[pin] = attrs["function"]
            elif gname == "ff":
                cell.ff = FlipFlop(
                    state=args[0], clocked_on=attrs["clocked_on"],
                    next_state=attrs["next_state"],
                    clear=attrs.get("clear"), preset=attrs.get("preset"),
                )
        cells[name] = cell
    return cells


def load(lib_path: str | Path, cache: str | Path | None = None) -> Library:
    lib_path = Path(lib_path)
    if cache is not None:
        cache = Path(cache)
        if cache.exists() and cache.stat().st_mtime >= lib_path.stat().st_mtime:
            data = json.loads(cache.read_text())
            cells = {}
            for name, d in data.items():
                ff = FlipFlop(**d["ff"]) if d["ff"] else None
                cells[name] = Cell(name, d["pins"], d["functions"], ff)
            return Library(cells)
    text = _strip(lib_path.read_text())
    # The library body is "library (name) { ... }"; drop everything before the
    # first cell, which only holds operating conditions and lookup templates.
    lib = Library(_cells_from_text(text))
    if cache is not None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({n: asdict(c) for n, c in lib.cells.items()}))
    return lib


# ---------------------------------------------------------------------------
# Boolean expressions

_EXPR_TOKEN = re.compile(r"\s*([A-Za-z_][A-Za-z0-9_]*|[01]|[!&|^()'*+])")


def parse_expr(expr: str):
    """Parse a Liberty boolean expression into a nested tuple AST.

    Grammar (lowest to highest precedence): | or +, ^, & or * or juxtaposition,
    prefix ! and postfix '. Leaves are ("var", name) or ("const", 0/1).
    """
    toks = [t for t in _EXPR_TOKEN.findall(expr)]
    pos = 0

    def peek():
        return toks[pos] if pos < len(toks) else None

    def take():
        nonlocal pos
        pos += 1
        return toks[pos - 1]

    def p_or():
        node = p_xor()
        while peek() in ("|", "+"):
            take()
            node = ("or", node, p_xor())
        return node

    def p_xor():
        node = p_and()
        while peek() == "^":
            take()
            node = ("xor", node, p_and())
        return node

    def p_and():
        node = p_not()
        while True:
            t = peek()
            if t in ("&", "*"):
                take()
                node = ("and", node, p_not())
            elif t is not None and (t == "(" or t == "!" or re.fullmatch(r"[A-Za-z_]\w*|[01]", t)):
                node = ("and", node, p_not())  # juxtaposition means AND
            else:
                return node

    def p_not():
        if peek() == "!":
            take()
            return ("not", p_not())
        node = p_atom()
        while peek() == "'":
            take()
            node = ("not", node)
        return node

    def p_atom():
        t = take()
        if t == "(":
            node = p_or()
            assert take() == ")", expr
            return node
        if t in ("0", "1"):
            return ("const", int(t))
        return ("var", t)

    tree = p_or()
    if pos != len(toks):
        raise ValueError(f"trailing tokens in {expr!r}")
    return tree


def expr_vars(tree) -> set[str]:
    if tree[0] == "var":
        return {tree[1]}
    if tree[0] == "const":
        return set()
    return set().union(*(expr_vars(t) for t in tree[1:]))


def to_python(tree, env: dict[str, str]) -> str:
    """Render an AST as a Python bitwise expression over integer bit-vectors.

    Every net value is a Python int whose bit k is the value in simulation
    lane k, so one evaluation simulates many input vectors at once. ``~`` gives
    a negative number, which is fine: callers mask the result with the lane
    mask at the end.
    """
    kind = tree[0]
    if kind == "var":
        return env[tree[1]]
    if kind == "const":
        return "-1" if tree[1] else "0"
    if kind == "not":
        return f"(~{to_python(tree[1], env)})"
    op = {"and": "&", "or": "|", "xor": "^"}[kind]
    return f"({to_python(tree[1], env)} {op} {to_python(tree[2], env)})"


def evaluate(tree, values: dict[str, int]) -> int:
    kind = tree[0]
    if kind == "var":
        return values[tree[1]]
    if kind == "const":
        return tree[1]
    if kind == "not":
        return 1 - evaluate(tree[1], values)
    a, b = evaluate(tree[1], values), evaluate(tree[2], values)
    return {"and": a & b, "or": a | b, "xor": a ^ b}[kind]
