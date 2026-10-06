"""Minimal Value Change Dump reader/writer (enough for the puzzle waveform)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Signal:
    name: str
    width: int
    code: str


@dataclass
class VCD:
    header: dict[str, str] = field(default_factory=dict)  # $date, $version, ...
    signals: dict[str, Signal] = field(default_factory=dict)  # by name
    changes: list[tuple[int, str, str]] = field(default_factory=list)  # (time, name, value)

    def timeline(self):
        """Yield (time, {name: value}) with all changes at each timestamp."""
        current_t, batch = None, {}
        for t, name, value in self.changes:
            if t != current_t and batch:
                yield current_t, batch
                batch = {}
            current_t = t
            batch[name] = value
        if batch:
            yield current_t, batch


def read(path: str | Path) -> VCD:
    vcd = VCD()
    by_code: dict[str, list[str]] = {}
    tokens = Path(path).read_text().split()
    i, t = 0, 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in ("$date", "$version", "$timescale", "$comment"):
            j = tokens.index("$end", i)
            vcd.header[tok[1:]] = " ".join(tokens[i + 1:j])
            i = j + 1
        elif tok == "$var":
            _, kind, width, code, name = tokens[i:i + 5]
            j = tokens.index("$end", i)
            ref = " ".join(tokens[i + 4:j])
            base = ref.split(" ")[0]
            vcd.signals[base] = Signal(base, int(width), code)
            by_code.setdefault(code, []).append(base)
            i = j + 1
        elif tok.startswith("$"):
            i += 1
        elif tok.startswith("#"):
            t = int(tok[1:])
            i += 1
        elif tok[0] in "bBrR":
            value, code = tok[1:], tokens[i + 1]
            for name in by_code.get(code, []):
                vcd.changes.append((t, name, value))
            i += 2
        else:
            value, code = tok[0], tok[1:]
            for name in by_code.get(code, []):
                vcd.changes.append((t, name, value))
            i += 1
    return vcd


def write(path: str | Path, signals: list[tuple[str, int]], cycles: list[tuple[dict, dict]],
          period: int = 10000, module: str = "puzzle") -> None:
    """Write a VCD with a clock. ``cycles`` holds (inputs, outputs) per clock
    cycle: inputs change on the falling edge, outputs on the rising edge, the
    same convention as example_inputs.vcd."""
    all_sigs = [("clk", 1)] + signals
    codes = {name: chr(33 + k) for k, (name, _) in enumerate(all_sigs)}
    widths = dict(all_sigs)
    out = ["$timescale 1ps $end", f"$scope module {module} $end"]
    for name, code in codes.items():
        kind = "wire" if widths[name] > 1 else "reg"
        rng = f" [{widths[name] - 1}:0]" if widths[name] > 1 else ""
        out.append(f"$var {kind} {widths[name]} {code} {name}{rng} $end")
    out += ["$upscope $end", "$enddefinitions $end"]

    def fmt(name, value):
        if widths[name] == 1:
            return f"{value & 1}{codes[name]}"
        return f"b{value:b} {codes[name]}"

    prev: dict[str, int] = {}

    def changes(values):
        lines = []
        for name, value in values.items():
            if prev.get(name) != value:
                lines.append(fmt(name, value))
                prev[name] = value
        return lines

    for k, (ins, outs) in enumerate(cycles):
        out.append(f"#{k * period}")
        out.append(fmt("clk", 0))
        out += changes(ins)
        out.append(f"#{k * period + period // 2}")
        out.append(fmt("clk", 1))
        out += changes(outs)
    Path(path).write_text("\n".join(out) + "\n")
