"""Find a winning input without understanding the chip: bounded model checking.

Unroll the netlist for 121 input cycles plus a few idle ones. Each cycle gets fresh boolean
variables for the flip-flop outputs, tied to the previous cycle's D inputs.
Then ask z3 for an input sequence that makes ``success`` true after the last
input. If z3 also proves there is no second sequence, the answer is unique.

Initial state: flops with an async clear/preset start at their reset value.
Flops without a reset (``dfxtp``) start as free variables, so the solver may
not rely on a lucky power-up value.
"""

from __future__ import annotations

import z3

from .liberty import Library
from .netlist import Netlist
from .sim import Simulator


def _expr(tree, env):
    kind = tree[0]
    if kind == "var":
        return env[tree[1]]
    if kind == "const":
        return z3.BoolVal(bool(tree[1]))
    if kind == "not":
        return z3.Not(_expr(tree[1], env))
    a, b = _expr(tree[1], env), _expr(tree[2], env)
    if kind == "and":
        return z3.And(a, b)
    if kind == "or":
        return z3.Or(a, b)
    return z3.Xor(a, b)


class Unrolled:
    def __init__(self, netlist: Netlist, lib: Library, cycles: int = 121, idle: int = 3,
                 floating: bool = False):
        self.sim = Simulator(netlist, lib)  # reuse its levelized gate list
        self.cycles = cycles
        self.steps = cycles + idle
        self.solver = z3.Solver()
        self.inputs = [z3.Bool(f"I_{t}") for t in range(cycles)]
        s = self.solver
        flops = self.sim.flops
        self.state = [{}]
        for f in flops:
            if f.clear is not None:
                self.state[0][f.q] = z3.BoolVal(False)
            elif f.preset is not None:
                self.state[0][f.q] = z3.BoolVal(True)
            else:
                self.state[0][f.q] = z3.Bool(f"init_{f.inst}")
        const = {"VPWR": z3.BoolVal(True), "VGND": z3.BoolVal(False)}
        floating_val = z3.BoolVal(floating)
        self.nets = []
        for t in range(self.steps + 1):
            env = dict(const)
            env.update(self.state[t])
            env["rst_n"] = z3.BoolVal(True)
            env["enable"] = z3.BoolVal(t < cycles)
            env["I"] = self.inputs[t] if t < cycles else z3.BoolVal(False)
            env["clk"] = z3.BoolVal(False)
            for _, _, _, out, ins, tree in self.sim.order:
                local = {v: (env.get(n, floating_val) if n is not None else floating_val)
                         for v, n in ins.items()}
                env[out] = _expr(tree, local)
            self.nets.append(env)
            if t == self.steps:
                break
            nxt = {}
            for f in flops:
                q = z3.Bool(f"q_{f.inst}_{t + 1}")
                s.add(q == env[f.d])
                nxt[f.q] = q
            self.state.append(nxt)

    def net(self, name: str, t: int):
        return self.nets[t][name]

    def solve(self, target: str = "success", limit: int = 2) -> list[list[int]]:
        """Input sequences that drive ``target`` high at some point in the idle
        cycles after the last input bit."""
        s = self.solver
        s.push()
        s.add(z3.Or([self.net(target, t) for t in range(self.cycles, self.steps + 1)]))
        found = []
        while len(found) < limit and s.check() == z3.sat:
            m = s.model()
            bits = [1 if z3.is_true(m.eval(v, model_completion=True)) else 0 for v in self.inputs]
            found.append(bits)
            s.add(z3.Or([v != bool(b) for v, b in zip(self.inputs, bits)]))
        s.pop()
        return found
