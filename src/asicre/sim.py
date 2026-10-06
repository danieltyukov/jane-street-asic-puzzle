"""A cycle-based, bit-parallel gate-level simulator.

Each net holds a Python integer. Bit ``k`` of that integer is the net's value
in simulation lane ``k``, so a single pass through the gates simulates as many
independent input sequences as there are lanes. This makes "poke every input
position and see what changes" style experiments cheap.

The model is synchronous: all flip-flops share one clock (checked at build
time), and asynchronous clear/preset pins are honoured whenever the
combinational logic is settled.
"""

from __future__ import annotations

import collections
from dataclasses import dataclass

from .liberty import Library, parse_expr, to_python, expr_vars
from .netlist import Netlist, is_physical_only



@dataclass
class Flop:
    inst: str
    q: str            # net driven by Q
    d: str
    clear: str | None = None   # net that, when 0, forces Q to 0
    preset: str | None = None  # net that, when 0, forces Q to 1


class Simulator:
    def __init__(self, netlist: Netlist, lib: Library, lanes: int = 1, floating: int = 0):
        self.netlist = netlist
        self.lib = lib
        self.lanes = lanes
        self.mask = (1 << lanes) - 1
        self.floating_value = floating
        self.flops: list[Flop] = []
        self._build()
        self.reset_state()

    # -- construction ----------------------------------------------------
    def _build(self) -> None:
        nl, lib = self.netlist, self.lib
        driver: dict[str, tuple[str, str]] = {}
        comb = []  # (inst, cell, out_pin, out_net, in_nets_by_pin, expr)
        for inst in nl.instances:
            if is_physical_only(inst.cell):
                continue
            cell = lib.cells[inst.cell]
            for pin in cell.outputs:
                net = inst.pins.get(pin)
                if net is None:
                    continue
                if net in driver:
                    raise ValueError(f"net {net} has two drivers: {driver[net]} and {inst.name}.{pin}")
                driver[net] = (inst.name, pin)
            if cell.ff:
                ff = cell.ff
                q_pin = next(p for p, f in cell.functions.items() if f == ff.state)
                self.flops.append(Flop(
                    inst=inst.name, q=inst.pins[q_pin], d=inst.pins[ff.next_state],
                    clear=inst.pins[ff.clear.lstrip("!")] if ff.clear else None,
                    preset=inst.pins[ff.preset.lstrip("!")] if ff.preset else None,
                ))
                for flag in (ff.clear, ff.preset):
                    if flag and not flag.startswith("!"):
                        raise NotImplementedError(f"active-high async pin on {inst.cell}")
                self._check_clock(inst, ff.clocked_on)
                continue
            for pin, func in cell.functions.items():
                if pin not in inst.pins:
                    continue
                tree = parse_expr(func)
                ins = {v: inst.pins.get(v) for v in expr_vars(tree)}
                comb.append((inst.name, inst.cell, pin, inst.pins[pin], ins, tree))
        self.driver = driver
        self._comb = comb
        self._levelize()

    def _check_clock(self, inst, clk_pin: str) -> None:
        """Every flop must be clocked by the ``clk`` port through buffers."""
        net = inst.pins[clk_pin]
        seen = set()
        while net not in self.netlist.ports:
            if net in seen:
                raise ValueError(f"clock loop at {net}")
            seen.add(net)
            src = self._find_driver(net)
            if src is None:
                raise ValueError(f"undriven clock net {net} at {inst.name}")
            cell = self.lib.cells[src.cell]
            trees = [parse_expr(f) for f in cell.functions.values()]
            if trees != [("var", "A")]:
                raise NotImplementedError(f"clock of {inst.name} passes through {src.cell}")
            net = src.pins["A"]
        self.clock_port = net

    def _find_driver(self, net: str):
        for inst in self.netlist.instances:
            if is_physical_only(inst.cell):
                continue
            cell = self.lib.cells[inst.cell]
            if any(inst.pins.get(p) == net for p in cell.outputs):
                return inst
        return None

    def _levelize(self) -> None:
        """Topologically sort the combinational gates (Kahn's algorithm)."""
        produced = {g[3]: k for k, g in enumerate(self._comb)}
        deps = collections.defaultdict(set)
        users = collections.defaultdict(set)
        for k, (_, _, _, _, ins, _) in enumerate(self._comb):
            for net in ins.values():
                if net in produced:
                    deps[k].add(produced[net])
                    users[produced[net]].add(k)
        ready = collections.deque(k for k in range(len(self._comb)) if not deps[k])
        order = []
        while ready:
            k = ready.popleft()
            order.append(k)
            for u in users[k]:
                deps[u].discard(k)
                if not deps[u]:
                    ready.append(u)
        if len(order) != len(self._comb):
            raise ValueError("combinational loop in netlist")
        self.order = [self._comb[k] for k in order]
        self.level = {}
        for k in order:
            g = self._comb[k]
            lvl = 0
            for net in g[4].values():
                if net in produced:
                    lvl = max(lvl, self.level[self._comb[produced[net]][3]] + 1)
            self.level[g[3]] = lvl
        self._compile()

    def _compile(self) -> None:
        """Turn the sorted gate list into one Python function.

        The generated source only contains net names and bitwise operators
        built from the Liberty functions, so exec'ing it is safe. Keeping the
        text in ``self.source`` makes it easy to read what is being run."""
        lines = ["def settle(v, M):"]
        for inst, cell, pin, out, ins, tree in self.order:
            env = {}
            for var, net in ins.items():
                env[var] = f"v[{net!r}]" if net is not None else ("-1" if self.floating_value else "0")
            lines.append(f"    v[{out!r}] = {to_python(tree, env)} & M")
        lines.append("    return v")
        src = "\n".join(lines)
        scope: dict = {}
        exec(compile(src, "<netlist>", "exec"), scope)
        self._settle_fn = scope["settle"]
        self.source = src

    # -- state -----------------------------------------------------------
    def reset_state(self) -> None:
        self.values: dict[str, int] = collections.defaultdict(int)
        self.values["VPWR"] = self.mask
        self.values["VGND"] = 0
        for f in self.flops:
            self.values[f.q] = 0
        for port, direction in self.netlist.ports.items():
            if direction == "input":
                self.values[port] = 0
        for net in self._undriven_nets():
            self.values[net] = self.mask if self.floating_value else 0

    def _undriven_nets(self) -> set[str]:
        used = set()
        for _, _, _, _, ins, _ in self._comb:
            used.update(n for n in ins.values() if n)
        for f in self.flops:
            used.update(n for n in (f.d, f.clear, f.preset) if n)
        driven = set(self.driver) | set(self.netlist.ports) | {"VPWR", "VGND"}
        return used - driven

    def set_inputs(self, **values: int) -> None:
        for name, value in values.items():
            if name not in self.netlist.ports:
                raise KeyError(name)
            self.values[name] = value & self.mask

    def settle(self) -> None:
        """Evaluate combinational logic and apply async clear/preset."""
        v, M = self.values, self.mask
        for _ in range(8):
            self._settle_fn(v, M)
            changed = False
            for f in self.flops:
                q = v[f.q]
                if f.clear is not None:
                    q &= v[f.clear]
                if f.preset is not None:
                    q |= ~v[f.preset] & M
                if q != v[f.q]:
                    v[f.q] = q
                    changed = True
            if not changed:
                return
        raise RuntimeError("async set/reset did not settle")

    def clock(self) -> None:
        """One rising clock edge: every flop samples D, then logic settles."""
        self.settle()
        v = self.values
        nxt = [v[f.d] for f in self.flops]
        for f, d in zip(self.flops, nxt):
            v[f.q] = d
        self.settle()

    def get(self, net: str) -> int:
        return self.values[net]

    def output_byte(self, bus: str = "O", width: int = 8) -> list[int]:
        """Per-lane value of a bus, e.g. O[7:0]."""
        bits = [self.values[f"{bus}[{i}]"] for i in range(width)]
        return [sum(((b >> lane) & 1) << i for i, b in enumerate(bits)) for lane in range(self.lanes)]

    def state(self) -> dict[str, int]:
        return {f.inst: self.values[f.q] for f in self.flops}
