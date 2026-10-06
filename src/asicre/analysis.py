"""Work out what the extracted circuit does.

Two complementary views:

* **Dynamic.** Run 122 simulations at once (one bit-parallel lane per board
  position, plus an empty baseline). Lane ``p`` places a single star at
  position ``p``. A flip-flop that ends up different from the baseline "heard"
  that star, and the set of positions each flop hears tells you its job:
  a column counter hears one column, a region counter hears one region, and so
  on. This is the impulse-response trick from signals and systems.
* **Physical.** Standard cells of one RTL module are placed together, so the
  layout is a set of islands. Clustering the cells recovers the module
  boundaries, and the dynamic roles name them.
"""

from __future__ import annotations

import collections
from dataclasses import dataclass, field

from .liberty import Library
from .netlist import Netlist
from .sim import Simulator

N = 11
CELLS = N * N
IDLE = 24  # cycles to keep clocking after the board so the message can play out


# ---------------------------------------------------------------------------
# Driving the chip

@dataclass
class RunResult:
    success: bool
    message: str
    success_cycle: int | None
    bytes: list[int] = field(default_factory=list)


def stimulus(board: list[int], idle: int = IDLE) -> list[tuple[int, int, int]]:
    """(rst_n, enable, I) for every clock cycle of one attempt."""
    seq = [(0, 0, 0), (0, 0, 0)]
    seq += [(1, 1, bit) for bit in board]
    seq += [(1, 0, 0)] * idle
    return seq


def run_boards(netlist: Netlist, lib: Library, boards: list[list[int]],
               idle: int = IDLE, floating: int = 0) -> list[RunResult]:
    """Feed each board to the chip (one lane each) and collect what it says."""
    sim = Simulator(netlist, lib, lanes=len(boards), floating=floating)
    M = sim.mask
    sim.set_inputs(rst_n=0, enable=0, I=0)
    sim.settle()
    sim.clock()
    sim.set_inputs(rst_n=M, enable=M)
    succ_cycle: list[int | None] = [None] * len(boards)
    streams: list[list[int]] = [[] for _ in boards]
    for cycle in range(CELLS + idle):
        if cycle == CELLS:
            sim.set_inputs(enable=0, I=0)
        elif cycle < CELLS:
            sim.set_inputs(I=sum(b[cycle] << k for k, b in enumerate(boards)))
        sim.clock()
        s = sim.get("success")
        for k, byte in enumerate(sim.output_byte()):
            streams[k].append(byte)
            if (s >> k) & 1 and succ_cycle[k] is None:
                succ_cycle[k] = cycle + 1  # number of rising edges seen
    out = []
    for k in range(len(boards)):
        msg = "".join(chr(b) for b in streams[k] if b)
        out.append(RunResult(succ_cycle[k] is not None, msg, succ_cycle[k], streams[k]))
    return out


# ---------------------------------------------------------------------------
# Impulse response

@dataclass
class FlopResponse:
    final: frozenset[int]    # positions whose single star changes this flop's final value
    touched: frozenset[int]  # positions that change it at any time during the run


def impulse_response(netlist: Netlist, lib: Library) -> dict[str, FlopResponse]:
    lanes = CELLS + 1  # lane CELLS is the empty board
    sim = Simulator(netlist, lib, lanes=lanes)
    M = sim.mask
    sim.set_inputs(rst_n=0, enable=0, I=0)
    sim.settle()
    sim.clock()
    sim.set_inputs(rst_n=M, enable=M)
    touched = collections.defaultdict(int)
    for cycle in range(CELLS):
        sim.set_inputs(I=1 << cycle)
        sim.clock()
        for f in sim.flops:
            v = sim.values[f.q]
            base = -((v >> CELLS) & 1) & M  # baseline bit replicated across lanes
            touched[f.inst] |= v ^ base
    sim.set_inputs(enable=0, I=0)
    out = {}
    for f in sim.flops:
        v = sim.values[f.q]
        base = -((v >> CELLS) & 1) & M
        final = v ^ base
        out[f.inst] = FlopResponse(_positions(final), _positions(touched[f.inst]))
    return out


def _positions(mask: int) -> frozenset[int]:
    return frozenset(p for p in range(CELLS) if (mask >> p) & 1)


@dataclass
class Roles:
    column_counters: dict[int, str]       # column -> flop (bit 0 of its counter)
    region_counters: list[tuple[frozenset[int], str]]
    delay_line: list[str]                 # ordered: newest input first
    star_counter_lsb: str | None
    board_hash: list[str]                 # flops that see every position but forget none (LFSR)
    other: list[str]


def classify(responses: dict[str, FlopResponse]) -> Roles:
    """Sort flops into jobs using only their impulse responses.

    * every position flips it at the end: the parity bit of the star counter
    * one position at the end but many on the way: a stage of a delay line
      (the star is still in the pipe when the board ends)
    * the same set at the end as on the way: a counter. If the set is one
      column it counts that column, otherwise it is a region counter.
    * a scrambled subset that is not the same as what it saw on the way: a
      bit of a hash of the board (the output generator's LFSR)
    * nothing: control logic that does not depend on any single star
    """
    columns, regions, delay, hashes, other = {}, [], [], [], []
    star_lsb = None
    for flop, r in responses.items():
        f, t = r.final, r.touched
        if len(f) == CELLS:
            star_lsb = flop
        elif len(f) == 1 and len(t) > 1:
            delay.append(flop)
        elif len(f) > 1 and f == t:
            cols = {p % N for p in f}
            if len(cols) == 1 and len(f) == N:
                columns[cols.pop()] = flop
            else:
                regions.append((f, flop))
        elif f:
            hashes.append(flop)
        else:
            other.append(flop)
    # a star at position p sits in the stage (120 - p) once all 121 bits are in
    delay.sort(key=lambda fl: -min(responses[fl].final))
    regions.sort(key=lambda rf: min(rf[0]))
    return Roles(columns, regions, delay, star_lsb, sorted(hashes), sorted(other))


def region_map(roles: Roles) -> list[str]:
    """Turn the region counters' position sets into an 11x11 letter grid."""
    sets = [s for s, _ in roles.region_counters]
    covered = [p for s in sets for p in s]
    if sorted(covered) != list(range(CELLS)):
        raise ValueError("region counters do not partition the board")
    grid = [["?"] * N for _ in range(N)]
    for k, s in enumerate(sets):
        for p in s:
            grid[p // N][p % N] = chr(ord("a") + k)
    return ["".join(row) for row in grid]


# ---------------------------------------------------------------------------
# Islands

@dataclass
class Island:
    cells: list[str]
    flops: list[str]
    bbox: tuple[float, float, float, float]  # x0, y0, x1, y1 in microns
    name: str = ""


def islands(netlist: Netlist, gap: float = 3.0, skip=("conb",)) -> list[Island]:
    """Single-linkage clustering of logic cells: two cells are in the same
    island if their placement boxes are within ``gap`` microns."""
    cells = [i for i in netlist.logic_instances() if not any(s in i.cell for s in skip)]
    parent = list(range(len(cells)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    order = sorted(range(len(cells)), key=lambda k: cells[k].x)
    for oi, i in enumerate(order):
        a = cells[i]
        for j in order[oi + 1:]:
            b = cells[j]
            if b.x - (a.x + a.width) > gap:
                break
            dx = max(0.0, max(a.x, b.x) - min(a.x + a.width, b.x + b.width))
            dy = max(0.0, max(a.y, b.y) - min(a.y + a.height, b.y + b.height))
            if dx <= gap and dy <= gap:
                parent[find(i)] = find(j)
    groups = collections.defaultdict(list)
    for k, c in enumerate(cells):
        groups[find(k)].append(c)
    out = []
    for members in groups.values():
        x0 = min(c.x for c in members)
        y0 = min(c.y for c in members)
        x1 = max(c.x + c.width for c in members)
        y1 = max(c.y + c.height for c in members)
        out.append(Island([c.name for c in members],
                          [c.name for c in members if c.cell.removeprefix("sky130_fd_sc_hd__").startswith("df")],
                          (x0, y0, x1, y1)))
    out.sort(key=lambda isl: -len(isl.cells))
    return out


# ---------------------------------------------------------------------------
# Naming the islands

BLOCKS = [
    "region counters",
    "column counters",
    "region ROM",
    "adjacency check",
    "row check",
    "star counter",
    "position counters",
    "output generator",
]


def name_islands(netlist: Netlist, lib: Library, isl: list[Island],
                 responses: dict[str, FlopResponse], roles: Roles) -> list[Island]:
    """Give every island a block name from what its flops do and how it is wired.

    Flop islands are named by the roles found in the impulse response. Islands
    without flops are named after the block they mostly feed (a combinational
    island that drives the region counters is the region ROM), and the clock
    buffers are recognised by cell type.
    """
    where = {c: k for k, i in enumerate(isl) for c in i.cells}
    inst = {i.name: i for i in netlist.instances}
    hashes = set(roles.board_hash)
    delay = set(roles.delay_line)
    cols = set(roles.column_counters.values())
    regs = {f for _, f in roles.region_counters}
    success_flop = next((i.name for i in netlist.instances if i.pins.get("Q") == "success"), None)

    for k, island in enumerate(isl):
        fl = set(island.flops)
        types = {inst[c].cell.removeprefix("sky130_fd_sc_hd__").split("_")[0] for c in island.cells}
        if types <= {"clkbuf", "buf"}:
            island.name = "clock tree"
        elif fl & regs:
            island.name = "region counters"
        elif fl & cols:
            island.name = "column counters"
        elif fl & delay:
            island.name = "adjacency check"
        elif roles.star_counter_lsb in fl:
            island.name = "star counter"
        elif fl & hashes or success_flop in fl:
            island.name = "output generator"
        elif fl and any(responses[f].touched for f in fl):
            island.name = "row check"
        elif fl:
            island.name = "position counters"

    # combinational islands: follow their outputs
    driver = {}
    for i in netlist.logic_instances():
        for p, n in i.pins.items():
            if p in lib.cells[i.cell].outputs:
                driver[n] = i.name
    feeds = collections.defaultdict(collections.Counter)
    for i in netlist.logic_instances():
        if i.name not in where:
            continue
        for p, n in i.pins.items():
            if p in lib.cells[i.cell].inputs and driver.get(n) in where:
                a, b = where[driver[n]], where[i.name]
                if a != b:
                    feeds[a][b] += 1
    fed_by = collections.defaultdict(collections.Counter)
    for a, outs in feeds.items():
        for b, c in outs.items():
            fed_by[b][a] += c
    for _ in range(3):  # a few passes, so chains of unnamed islands resolve
        for k, island in enumerate(isl):
            if island.name:
                continue
            # a big block of logic is named after what it drives; a handful of
            # gates is usually the tail end of whatever drives it
            links = feeds[k] if len(island.cells) > 10 else fed_by[k]
            named = [isl[b].name for b, _ in links.most_common() if isl[b].name not in ("", "clock tree")]
            if not named:
                continue
            island.name = "region ROM" if named[0] == "region counters" and len(island.cells) > 10 else named[0]
    return isl
