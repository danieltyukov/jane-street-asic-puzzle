"""Drive a simulator from a VCD and compare its outputs with the recording."""

from __future__ import annotations

from dataclasses import dataclass, field

from .sim import Simulator
from .vcd import VCD


@dataclass
class ReplayResult:
    cycles: int = 0
    checked: int = 0
    mismatches: list[tuple[int, str, str, str]] = field(default_factory=list)  # (time, signal, expected, got)
    trace: list[dict] = field(default_factory=list)  # one row per rising edge

    @property
    def ok(self) -> bool:
        return self.checked > 0 and not self.mismatches


def _bits(value: str, width: int) -> int | None:
    if any(ch in "xXzZ" for ch in value):
        return None
    return int(value, 2)


def replay(sim: Simulator, vcd: VCD, clock: str = "clk") -> ReplayResult:
    inputs = [p for p, d in sim.netlist.ports.items() if d == "input" and p != clock]
    out_buses = {}
    for port, d in sim.netlist.ports.items():
        if d == "output":
            base = port.split("[")[0]
            out_buses.setdefault(base, []).append(port)
    widths = {name: s.width for name, s in vcd.signals.items()}

    res = ReplayResult()
    expected: dict[str, str] = {}
    current_inputs: dict[str, int] = {p: 0 for p in inputs}
    clk = 0
    for t, batch in vcd.timeline():
        rising = clock in batch and batch[clock] == "1" and clk == 0
        if clock in batch:
            clk = int(batch[clock]) if batch[clock] in "01" else clk
        if rising:
            sim.clock()
            res.cycles += 1
        for name, value in batch.items():
            if name in current_inputs:
                v = _bits(value, widths[name])
                current_inputs[name] = v or 0
            elif name != clock:
                expected[name] = value
        sim.set_inputs(**current_inputs)
        sim.settle()
        row = {"t": t, **current_inputs}
        for base, ports in out_buses.items():
            got = sum(((sim.get(p) & 1) << int(p.split("[")[1][:-1])) if "[" in p else (sim.get(p) & 1)
                      for p in ports)
            row[base] = got
            if base in expected:
                exp = _bits(expected[base], widths.get(base, 1))
                if exp is not None:
                    res.checked += 1
                    if exp != got:
                        res.mismatches.append((t, base, expected[base], format(got, "b")))
        if rising:
            res.trace.append(row)
    return res
