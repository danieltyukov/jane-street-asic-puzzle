"""Find the Easter eggs hidden in the puzzle files.

Each finder returns plain data so the CLI can print it and the figure script
can draw it.
"""

from __future__ import annotations

import collections
import copy
import datetime as dt
from dataclasses import dataclass

import klayout.db as db

from . import analysis
from .layers import CUSTOM_MARKS
from .liberty import Library
from .netlist import Netlist
from .vcd import VCD

MORSE = {
    ".-": "A", "-...": "B", "-.-.": "C", "-..": "D", ".": "E", "..-.": "F", "--.": "G",
    "....": "H", "..": "I", ".---": "J", "-.-": "K", ".-..": "L", "--": "M", "-.": "N",
    "---": "O", ".--.": "P", "--.-": "Q", ".-.": "R", "...": "S", "-": "T", "..-": "U",
    "...-": "V", ".--": "W", "-..-": "X", "-.--": "Y", "--..": "Z",
}


# ---------------------------------------------------------------------------
# 1 and 2: the waveform file

def vcd_attempts(vcd: VCD) -> list[list[int]]:
    """The I bit sampled on every rising clock edge while enable is high,
    split into one list per attempt."""
    state = {"clk": "0", "enable": "0", "I": "0"}
    attempts: list[list[int]] = []
    current = None
    for _, batch in vcd.timeline():
        if batch.get("clk") == "1" and state["clk"] == "0":
            if state["enable"] == "1":
                if current is None:
                    current = []
                    attempts.append(current)
                current.append(int(state["I"]))
            else:
                current = None
        for name in state:
            if name in batch:
                state[name] = batch[name]
    return attempts


def vcd_hidden_text(attempts: list[list[int]]) -> str:
    """Each 11-cell board row holds one 7-bit ASCII character, least
    significant bit in the leftmost column. The last four columns are zero."""
    chars = []
    for board in attempts:
        for r in range(11):
            row = board[r * 11:(r + 1) * 11]
            chars.append(chr(sum(bit << i for i, bit in enumerate(row[:7]))))
    return "".join(chars)


@dataclass
class VcdHeader:
    date: str
    version: str
    leap_second: bool


def vcd_header(vcd: VCD) -> VcdHeader:
    date = vcd.header.get("date", "")
    # 23:59:60 does not exist in Python's datetime, which is the joke: it was a
    # real leap second, inserted at the end of 31 December 2016.
    try:
        dt.datetime.strptime(date, "%a %b %d %H:%M:%S %Y")
        leap = False
    except ValueError:
        leap = date.endswith("23:59:60 2016")
    return VcdHeader(date, vcd.header.get("version", ""), leap)


# ---------------------------------------------------------------------------
# 3: Morse code on an unused layer

@dataclass
class MorseMarks:
    marks: list[tuple[int, int, int, int]]  # x0, x1, y0, y1 in dbu
    code: str
    text: str


def morse(gds_path: str) -> MorseMarks:
    layout = db.Layout()
    layout.read(gds_path)
    top = layout.top_cell()
    idx = layout.find_layer(*CUSTOM_MARKS)
    marks = []
    it = top.begin_shapes_rec(idx)
    while not it.at_end():
        b = it.shape().bbox().transformed(it.trans())
        marks.append((b.left, b.right, b.bottom, b.top))
        it.next()
    marks.sort()
    unit = min(x1 - x0 for x0, x1, _, _ in marks)
    code = ""
    for k, (x0, x1, _, _) in enumerate(marks):
        code += "." if round((x1 - x0) / unit) == 1 else "-"
        if k + 1 < len(marks):
            gap = round((marks[k + 1][0] - x1) / unit)
            code += "" if gap == 1 else (" " if gap == 3 else " / ")
    words = [" ".join(word.split()) for word in code.split(" / ")]
    text = " ".join("".join(MORSE.get(sym, "?") for sym in w.split()) for w in words)
    return MorseMarks(marks, code, text)


# ---------------------------------------------------------------------------
# 4: a picture drawn in loose metal-2 squares

@dataclass
class Bitmap:
    pixels: list[list[int]]  # rows, top first
    count: int
    pitch_um: float
    origin_um: tuple[float, float]

    def ascii(self, on: str = "##", off: str = "  ") -> str:
        return "\n".join("".join(on if p else off for p in row) for row in self.pixels)


def met2_logo(gds_path: str, size: int = 300) -> Bitmap:
    """Collect the 0.3 x 0.3 um met2 boxes in the top cell. They are not
    connected to anything; they only form an image."""
    layout = db.Layout()
    layout.read(gds_path)
    top = layout.top_cell()
    idx = layout.find_layer(69, 20)
    boxes = [s.bbox() for s in top.shapes(idx).each()
             if s.is_box() and s.bbox().width() == size and s.bbox().height() == size]
    xs = sorted({b.left for b in boxes})
    ys = sorted({b.bottom for b in boxes})
    pitch = collections.Counter(b - a for a, b in zip(xs, xs[1:])).most_common(1)[0][0]
    w = (xs[-1] - xs[0]) // pitch + 1
    h = (ys[-1] - ys[0]) // pitch + 1
    pixels = [[0] * w for _ in range(h)]
    for b in boxes:
        pixels[h - 1 - (b.bottom - ys[0]) // pitch][(b.left - xs[0]) // pitch] = 1
    dbu = layout.dbu
    return Bitmap(pixels, len(boxes), pitch * dbu, (xs[0] * dbu, ys[0] * dbu))


# ---------------------------------------------------------------------------
# 5: the failure messages

def failure_messages(netlist: Netlist, lib: Library, regions: list[str],
                     solution: list[int]) -> dict[str, analysis.RunResult]:
    from . import starbattle
    touching = starbattle.solve(regions, adjacency=False, require_touching=True, limit=1)[0]
    one_star = [1] + [0] * 120
    boards = {
        "solution": solution,
        "empty board": [0] * 121,
        "full board": [1] * 121,
        "counts right, stars touching": touching,
        "one star": one_star,
    }
    results = analysis.run_boards(netlist, lib, list(boards.values()))
    return dict(zip(boards, results))


# ---------------------------------------------------------------------------
# 7: the floating wire

@dataclass
class FloatingWire:
    pins: list[tuple[str, str, str]]   # (instance, cell, pin)
    net: str
    message_if_low: str
    message_if_high: str
    repaired_by: list[str]             # nets that, if connected, give the intended text


def floating_wire(netlist: Netlist, lib: Library, touching: list[int],
                  intended: str = "TWO NOT TOUCH", search: bool = True,
                  radius: float = 30.0,
                  battery: list[tuple[list[int], str]] | None = None) -> FloatingWire | None:
    """Describe the undriven net and search for the driver it was meant to have.

    A candidate counts as a repair if the touching-stars board prints the
    intended text, and, when ``battery`` is given, every (board, message) pair
    in it also comes out right."""
    if not netlist.floating:
        return None
    inst_of = {i.name: i for i in netlist.instances}
    pins = [(i, inst_of[i].cell, p) for i, p in netlist.floating]
    net = inst_of[pins[0][0]].pins[pins[0][2]]
    low = analysis.run_boards(netlist, lib, [touching], floating=0)[0].message
    high = analysis.run_boards(netlist, lib, [touching], floating=1)[0].message
    fixes = []
    if search:
        fixes = _search_repair(netlist, lib, touching, net, intended, radius, battery)
    return FloatingWire(pins, net, low, high, fixes)


def _search_repair(netlist, lib, board, net, intended, radius, battery=None) -> list[str]:
    """Try every net driven from within ``radius`` microns of the floating
    pins as the missing driver, and keep the ones that fix the message."""
    from .sim import Simulator
    loads = {(i, p) for i, p in netlist.floating}
    inst_of = {i.name: i for i in netlist.instances}
    cx = sum(inst_of[i].x for i, _ in loads) / len(loads)
    cy = sum(inst_of[i].y for i, _ in loads) / len(loads)
    hits = []
    candidates = set()
    for inst in netlist.logic_instances():
        if abs(inst.x - cx) <= radius and abs(inst.y - cy) <= radius:
            outs = lib.cells[inst.cell].outputs
            candidates.update(n for p, n in inst.pins.items() if p in outs)
    candidates = sorted(candidates - {net, "VPWR", "VGND"})
    for cand in candidates:
        trial = copy.deepcopy(netlist)
        for inst in trial.instances:
            for pin, n in inst.pins.items():
                if (inst.name, pin) in loads:
                    inst.pins[pin] = cand
        trial.floating = []
        try:
            Simulator(trial, lib)
        except ValueError:
            continue  # would create a combinational loop
        if analysis.run_boards(trial, lib, [board])[0].message != intended:
            continue
        if battery:
            results = analysis.run_boards(trial, lib, [b for b, _ in battery])
            if any(r.message != want for r, (_, want) in zip(results, battery)):
                continue
        hits.append(cand)
    return hits
