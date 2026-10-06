"""Cross-check the Python simulator against Icarus Verilog.

The extracted netlist is written as structural Verilog and simulated with the
official SKY130 functional cell models. Both simulators get the same stimulus
and must produce the same output bytes and success bit on every cycle.

This is the check that catches bugs a waveform replay can miss, such as a tie
cell evaluated as 0 instead of 1. Icarus also models undriven wires as Z, so
the floating wire shows up there as X bits rather than as a guessed value.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .liberty import Library
from .netlist import Netlist
from .sim import Simulator

TESTBENCH = """`timescale 1ns/1ps
module tb;
  reg clk = 0, rst_n = 0, enable = 0, I = 0;
  wire [7:0] O;
  wire success;
  reg [2:0] stim [0:{depth}];
  integer k, fd;
  puzzle dut(.clk(clk), .rst_n(rst_n), .enable(enable), .I(I), .O(O), .success(success));
  initial begin
    $readmemb("{stim}", stim);
    fd = $fopen("{out}", "w");
    for (k = 0; k < {n}; k = k + 1) begin
      {{rst_n, enable, I}} = stim[k];
      #5 clk = 1;
      #4 $fdisplay(fd, "%b %b", O, success);
      #1 clk = 0;
    end
    $fclose(fd);
    $finish;
  end
endmodule
"""


@dataclass
class CrossCheck:
    cycles: int
    compared: int
    mismatches: list[tuple[int, str, str]]  # (cycle, python, icarus)
    unknown_cycles: list[int]               # cycles where Icarus reported X/Z

    @property
    def ok(self) -> bool:
        return self.compared > 0 and not self.mismatches


def available() -> bool:
    return shutil.which("iverilog") is not None and shutil.which("vvp") is not None


def run_icarus(netlist_v: Path, models: list[Path], stimulus: list[tuple[int, int, int]],
               workdir: Path) -> list[tuple[str, str]]:
    workdir.mkdir(parents=True, exist_ok=True)
    stim = workdir / "stim.mem"
    out = workdir / "icarus_out.txt"
    stim.write_text("\n".join(f"{r}{e}{i}" for r, e, i in stimulus) + "\n")
    tb = workdir / "tb.v"
    tb.write_text(TESTBENCH.format(depth=len(stimulus) - 1, n=len(stimulus),
                                   stim=stim.as_posix(), out=out.as_posix()))
    exe = workdir / "tb.vvp"
    cmd = ["iverilog", "-g2012", "-DFUNCTIONAL", "-DUNIT_DELAY=#1", "-o", str(exe),
           str(tb), str(netlist_v), *map(str, models)]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    subprocess.run(["vvp", "-n", str(exe)], check=True, capture_output=True, text=True)
    return [tuple(line.split()) for line in out.read_text().splitlines() if line.strip()]


def run_python(netlist: Netlist, lib: Library, stimulus: list[tuple[int, int, int]],
               floating: int = 0) -> list[tuple[str, str]]:
    sim = Simulator(netlist, lib, floating=floating)
    rows = []
    for rst_n, enable, bit in stimulus:
        sim.set_inputs(rst_n=rst_n, enable=enable, I=bit)
        sim.settle()
        sim.clock()
        rows.append((format(sim.output_byte()[0], "08b"), str(sim.get("success") & 1)))
    return rows


def cross_check(netlist: Netlist, lib: Library, netlist_v: Path, models: list[Path],
                stimulus: list[tuple[int, int, int]], workdir: Path) -> CrossCheck:
    ic = run_icarus(netlist_v, models, stimulus, workdir)
    py = run_python(netlist, lib, stimulus)
    mismatches, unknown = [], []
    compared = 0
    for k, (p, i) in enumerate(zip(py, ic)):
        if any(ch in "xXzZ" for ch in "".join(i)):
            unknown.append(k)
            # still compare every bit Icarus does know
            for pb, ib in zip("".join(p), "".join(i)):
                if ib in "01" and pb != ib:
                    mismatches.append((k, " ".join(p), " ".join(i)))
                    break
            continue
        compared += 1
        if p != i:
            mismatches.append((k, " ".join(p), " ".join(i)))
    return CrossCheck(len(stimulus), compared, mismatches, unknown)
