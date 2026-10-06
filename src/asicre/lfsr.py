"""Reverse-engineer the output generator's LFSR and use it to read the ROM.

The output generator keeps an 8-bit linear feedback shift register. While the
board streams in, every input bit is XORed into the feedback, so after 121
cycles the register holds a signature of the board. When the board is right,
the generator plays a stored message: output byte k is ROM[k] XOR the register,
and the register jumps 8 LFSR steps per byte.

Everything here is recovered from simulation traces, not from reading gates:

* ``find_chain``: flop B is the next stage after flop A if B(t+1) == A(t) on
  every cycle of every lane.
* ``find_taps``: the head of the chain is the XOR of the input bit and some
  subset of stages; try all 2^width subsets.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

from .liberty import Library
from .netlist import Netlist
from .sim import Simulator

PRINTABLE = set(range(32, 127))


@dataclass
class LFSR:
    width: int
    taps: list[int]  # stage indices XORed into the feedback (stage 0 is the head)

    def step(self, state: int, bit: int = 0) -> int:
        fb = bit
        for t in self.taps:
            fb ^= (state >> t) & 1
        return ((state << 1) | fb) & ((1 << self.width) - 1)

    def load(self, seed: int, bits: list[int]) -> int:
        state = seed
        for b in bits:
            state = self.step(state, b)
        return state

    def keystream(self, state: int, n: int, stride: int = 8) -> list[int]:
        out = []
        for _ in range(n):
            out.append(state)
            for _ in range(stride):
                state = self.step(state)
        return out

    def period(self, start: int = 1) -> int:
        s, k = self.step(start), 1
        while s != start:
            s, k = self.step(s), k + 1
        return k

    def polynomial(self) -> str:
        powers = sorted({t + 1 for t in self.taps}, reverse=True)
        return " + ".join(f"x^{p}" for p in powers) + " + 1"


def record(netlist: Netlist, lib: Library, boards: list[list[int]], flops: list[str],
           force: dict[str, int] | None = None, idle: int = 20):
    """Per-cycle state of ``flops`` and the output byte, one lane per board.

    ``force`` pins flop outputs (by instance name) to a value from the cycle
    after the board is loaded, which is how the success flag can be faked.
    """
    sim = Simulator(netlist, lib, lanes=len(boards))
    M = sim.mask
    q_of = {f.inst: f.q for f in sim.flops}
    sim.set_inputs(rst_n=0, enable=0, I=0)
    sim.settle()
    sim.clock()
    sim.set_inputs(rst_n=M, enable=M)
    states, outs, inputs = [], [], []
    n = len(boards[0])
    for cycle in range(n + idle):
        if cycle < n:
            word = sum(b[cycle] << k for k, b in enumerate(boards))
            sim.set_inputs(I=word)
        else:
            word = 0
            sim.set_inputs(enable=0, I=0)
        states.append({f: sim.values[q_of[f]] for f in flops})
        outs.append(sim.output_byte())
        inputs.append(word)
        sim.clock()
        if force and cycle >= n:
            for inst, value in force.items():
                sim.values[q_of[inst]] = M if value else 0
            sim.settle()
    states.append({f: sim.values[q_of[f]] for f in flops})
    outs.append(sim.output_byte())
    return states, outs, inputs


def find_chain(states: list[dict[str, int]], flops: list[str], cycles: int) -> list[str]:
    """Order the register's flops from head (stage 0) to tail."""
    nxt = {}
    for a, b in itertools.permutations(flops, 2):
        if all(states[t + 1][b] == states[t][a] for t in range(cycles)):
            nxt[a] = b
    heads = [f for f in flops if f not in nxt.values()]
    if len(heads) != 1:
        raise ValueError(f"not a single shift chain: heads={heads}")
    chain = [heads[0]]
    while chain[-1] in nxt:
        chain.append(nxt[chain[-1]])
    if len(chain) != len(flops):
        raise ValueError("chain does not cover every flop")
    return chain


def find_taps(states, inputs, chain: list[str], cycles: int, mask: int) -> list[int]:
    head = chain[0]
    width = len(chain)
    for k in range(1, width + 1):
        for taps in itertools.combinations(range(width), k):
            ok = True
            for t in range(cycles):
                fb = inputs[t]
                for s in taps:
                    fb ^= states[t][chain[s]]
                if (fb & mask) != states[t + 1][head]:
                    ok = False
                    break
            if ok:
                return list(taps)
    raise ValueError("feedback is not linear in the register and the input")


def pack(state: dict[str, int], chain: list[str], lane: int = 0) -> int:
    return sum(((state[f] >> lane) & 1) << i for i, f in enumerate(chain))


def crack(rom: list[int], lfsr: LFSR, stride: int = 8) -> list[tuple[int, str]]:
    """Try every seed; keep the ones that decode the ROM to printable text."""
    hits = []
    for seed in range(1 << lfsr.width):
        stream = lfsr.keystream(seed, len(rom), stride)
        text = bytes(r ^ k for r, k in zip(rom, stream))
        if all(ch in PRINTABLE for ch in text):
            hits.append((seed, text.decode()))
    return hits
