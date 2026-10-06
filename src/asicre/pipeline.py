"""The full solve, step by step. Each step returns plain data for the report."""

from __future__ import annotations

import functools
import random
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import (analysis, eggs, extract, iverilog, lfsr, liberty, magic, paths, replay, sat, starbattle,
               validate, vcd)
from .netlist import Netlist, short_type


@dataclass
class Context:
    build: Path = paths.BUILD
    log: list[str] = field(default_factory=list)

    def say(self, msg: str = "") -> None:
        print(msg, flush=True)
        self.log.append(msg)

    @functools.cached_property
    def lib(self) -> liberty.Library:
        return liberty.load(paths.liberty_path(), cache=self.build / "liberty_tt.json")

    @functools.cached_property
    def netlist(self) -> Netlist:
        cached = self.build / "puzzle.json"
        if cached.exists() and cached.stat().st_mtime >= paths.PUZZLE_GDS.stat().st_mtime:
            return Netlist.from_json(cached)
        return step_extract(self)["netlist"]

    @functools.cached_property
    def responses(self) -> dict[str, analysis.FlopResponse]:
        return analysis.impulse_response(self.netlist, self.lib)

    @functools.cached_property
    def roles(self) -> analysis.Roles:
        return analysis.classify(self.responses)

    @functools.cached_property
    def islands(self) -> list[analysis.Island]:
        isl = analysis.islands(self.netlist)
        return analysis.name_islands(self.netlist, self.lib, isl, self.responses, self.roles)

    @functools.cached_property
    def regions(self) -> list[str]:
        return analysis.region_map(self.roles)

    @functools.cached_property
    def solution(self) -> list[int]:
        return starbattle.solve(self.regions, limit=1)[0]


def _timed(fn):
    @functools.wraps(fn)
    def wrapper(ctx: Context, *a, **kw):
        t = time.time()
        out = fn(ctx, *a, **kw)
        out["seconds"] = round(time.time() - t, 2)
        return out
    return wrapper


@_timed
def step_extract(ctx: Context) -> dict:
    ctx.build.mkdir(parents=True, exist_ok=True)
    gds = paths.puzzle_file("puzzle.gds")
    netlist, rep = extract.extract(str(gds), ctx.lib.pin_directions())
    netlist.to_json(ctx.build / "puzzle.json")
    netlist.to_verilog(ctx.build / "puzzle_extracted.v", ctx.lib)
    ctx.__dict__["netlist"] = netlist
    logic = netlist.logic_instances()
    flops = [i for i in logic if short_type(i.cell).startswith("df")]
    ctx.say(f"extracted {len(netlist.instances)} cells ({len(logic)} logic, {len(flops)} flip-flops), "
            f"{len(netlist.nets())} nets")
    ctx.say(f"ports: {', '.join(netlist.ports)}")
    ctx.say(f"dangling vias: {sum(rep.dangling_cuts.values())}, pin label conflicts: {len(rep.pin_conflicts)}")
    if netlist.floating:
        ctx.say(f"input pins with no driver: {netlist.floating}")
    return {"netlist": netlist, "report": rep, "cells": len(netlist.instances), "logic": len(logic),
            "flops": len(flops), "nets": len(netlist.nets()), "floating": netlist.floating}


@_timed
def step_warmup(ctx: Context) -> dict:
    gds = paths.require(paths.WARMUP / "04_final.gds", "warm-up files missing")
    netlist, rep = extract.extract(str(gds), ctx.lib.pin_directions())
    netlist.to_json(ctx.build / "warmup.json")
    design = validate.read_def(paths.WARMUP / "03_post_place_and_route.def")
    cmp = validate.compare(netlist, design)
    from .sim import Simulator
    sim = Simulator(netlist, ctx.lib, lanes=256 * 4)
    rng = random.Random(0)
    pairs = [(a, 496 - a) for a in range(241, 256)] + [(rng.randrange(256), rng.randrange(256)) for _ in range(1009)]
    M = sim.mask
    sim.set_inputs(rst_n=0)
    sim.settle()
    sim.set_inputs(rst_n=M, en=M)
    for bit in range(7, -1, -1):
        sim.set_inputs(A=sum(((a >> bit) & 1) << k for k, (a, _) in enumerate(pairs)),
                       B=sum(((b >> bit) & 1) << k for k, (_, b) in enumerate(pairs)))
        sim.clock()
    got = sim.get("S")
    wrong = [(a, b) for k, (a, b) in enumerate(pairs) if ((got >> k) & 1) != int(a + b == 496)]
    ctx.say(f"warm-up: {cmp.matched_instances}/{len(design.components)} instances matched the DEF, "
            f"{cmp.nets_compared - len(cmp.mismatched_nets)}/{cmp.nets_compared} nets identical")
    ctx.say(f"warm-up: S == (A + B == 496) for {len(pairs) - len(wrong)}/{len(pairs)} random and edge-case inputs")
    return {"ok": cmp.ok and not wrong, "instances": cmp.matched_instances, "nets": cmp.nets_compared,
            "net_mismatches": cmp.mismatched_nets, "adder_failures": wrong}


@_timed
def step_replay(ctx: Context) -> dict:
    from .sim import Simulator
    wave = vcd.read(paths.puzzle_file("example_inputs.vcd"))
    res = replay.replay(Simulator(ctx.netlist, ctx.lib), wave)
    messages, current = [], ""
    for row in res.trace:
        if row["O"]:
            current += chr(row["O"])
        elif current:
            messages.append(current)
            current = ""
    if current:
        messages.append(current)
    ctx.say(f"replayed {res.cycles} clock cycles of example_inputs.vcd: {res.checked} output samples, "
            f"{len(res.mismatches)} mismatches")
    ctx.say(f"the chip answered: {messages}")
    return {"ok": res.ok, "cycles": res.cycles, "checked": res.checked, "mismatches": res.mismatches,
            "messages": messages}


@_timed
def step_analyze(ctx: Context) -> dict:
    r = ctx.roles
    isl = ctx.islands
    ctx.say(f"impulse response: {len(r.column_counters)} column counters, {len(r.region_counters)} region "
            f"counters, {len(r.delay_line)}-stage delay line, star-count LSB {r.star_counter_lsb}, "
            f"{len(r.board_hash)}-bit board hash, {len(r.other)} control flops")
    ctx.say(f"layout: {len(isl)} islands of logic cells:")
    for i in isl:
        x0, y0, x1, y1 = i.bbox
        ctx.say(f"  {i.name or '?':18s} {len(i.cells):4d} cells {len(i.flops):3d} flops  "
                f"x {x0:5.0f}-{x1:5.0f} um  y {y0:5.0f}-{y1:5.0f} um")
    ctx.say("region map (rows top to bottom, one letter per region):")
    for row in ctx.regions:
        ctx.say("  " + " ".join(row))
    return {"regions": ctx.regions, "region_sizes": sorted(len(s) for s, _ in r.region_counters),
            "islands": [{"name": i.name, "cells": len(i.cells), "flops": len(i.flops),
                         "bbox": [round(v, 1) for v in i.bbox]} for i in isl]}


@_timed
def step_solve(ctx: Context) -> dict:
    sols = starbattle.solve(ctx.regions, limit=2)
    board = sols[0]
    run = analysis.run_boards(ctx.netlist, ctx.lib, [board])[0]
    ctx.say(f"Star Battle solutions: {len(sols)} (unique)" if len(sols) == 1 else f"solutions: {len(sols)}")
    ctx.say(starbattle.render(ctx.regions, board))
    ctx.say(f"fed to the extracted chip: success={int(run.success)} after {run.success_cycle} clocks, "
            f"output {run.message!r}")
    _write_solution_vcd(ctx, board)
    return {"unique": len(sols) == 1, "board": board, "success": run.success, "answer": run.message,
            "rows": [[c for c in range(11) if board[r * 11 + c]] for r in range(11)]}


def _write_solution_vcd(ctx: Context, board: list[int]) -> None:
    """Record the winning run so it can be opened in Surfer or GTKWave."""
    from .sim import Simulator
    sim = Simulator(ctx.netlist, ctx.lib)
    cycles = []
    for rst_n, enable, bit in analysis.stimulus(board, idle=24):
        ins = {"rst_n": rst_n, "enable": enable, "I": bit}
        sim.set_inputs(**ins)
        sim.settle()
        sim.clock()
        cycles.append((ins, {"O": sim.output_byte()[0], "success": sim.get("success") & 1}))
    path = ctx.build / "solution.vcd"
    vcd.write(path, [("rst_n", 1), ("enable", 1), ("I", 1), ("O", 8), ("success", 1)], cycles)
    ctx.say(f"wrote {path.relative_to(paths.ROOT) if path.is_relative_to(paths.ROOT) else path}")


@_timed
def step_sat(ctx: Context) -> dict:
    model = sat.Unrolled(ctx.netlist, ctx.lib)
    found = model.solve(limit=2)
    same = bool(found) and found[0] == ctx.solution
    ctx.say(f"SAT (no knowledge of the rules): {len(found)} input sequence(s) raise success; "
            f"{'matches' if same else 'differs from'} the Star Battle solution")
    return {"solutions": len(found), "matches": same}


@_timed
def step_crack(ctx: Context) -> dict:
    H = ctx.roles.board_hash
    rng = random.Random(1)
    boards = [[rng.randint(0, 1) for _ in range(121)] for _ in range(16)]
    states, _, inputs = lfsr.record(ctx.netlist, ctx.lib, boards, H)
    chain = lfsr.find_chain(states, H, 120)
    taps = lfsr.find_taps(states, inputs, chain, 120, (1 << len(boards)) - 1)
    reg = lfsr.LFSR(len(chain), taps)
    seed = lfsr.pack(states[0], chain)
    success_flop = next(i.name for i in ctx.netlist.instances if i.pins.get("Q") == "success")
    st, out, _ = lfsr.record(ctx.netlist, ctx.lib, [[1] + [0] * 120], H, force={success_flop: 1})
    rom = [o[0] ^ lfsr.pack(s, chain) for s, o in zip(st[121:], out[121:]) if o[0]]
    hits = lfsr.crack(rom, reg)
    signature = reg.load(seed, ctx.solution)
    ctx.say(f"output LFSR: {reg.polynomial()} (period {reg.period()}), reset value 0x{seed:02x}")
    ctx.say(f"message ROM (with success faked on a wrong board): {' '.join(f'{b:02x}' for b in rom)}")
    ctx.say(f"seeds that decode it to printable text: {[(f'0x{s:02x}', t) for s, t in hits]}")
    ctx.say(f"signature of the solved board: 0x{signature:02x}")
    return {"polynomial": reg.polynomial(), "taps": taps, "chain": chain, "reset": seed, "rom": rom,
            "hits": hits, "solution_signature": signature}


@_timed
def step_eggs(ctx: Context) -> dict:
    wave = vcd.read(paths.puzzle_file("example_inputs.vcd"))
    attempts = eggs.vcd_attempts(wave)
    hidden = eggs.vcd_hidden_text(attempts)
    header = eggs.vcd_header(wave)
    morse = eggs.morse(str(paths.PUZZLE_GDS))
    logo = eggs.met2_logo(str(paths.PUZZLE_GDS))
    msgs = eggs.failure_messages(ctx.netlist, ctx.lib, ctx.regions, ctx.solution)
    touching_boards = starbattle.solve(ctx.regions, adjacency=False, require_touching=True, limit=30)
    rng = random.Random(7)
    battery = [(ctx.solution, "(* TWO STARS *)"), ([0] * 121, "EMPTY SKY"), ([1] * 121, "BIG BANG")]
    battery += [(b, "TWO NOT TOUCH") for b in touching_boards]
    battery += [([rng.randint(0, 1) for _ in range(121)], "TRY AGAIN") for _ in range(30)]
    wire = eggs.floating_wire(ctx.netlist, ctx.lib, touching_boards[0], battery=battery)
    ctx.say(f"1. the two attempts in example_inputs.vcd, one 7-bit character per board row: {hidden.strip()!r}")
    ctx.say(f"2. VCD $date {header.date!r} (a real leap second: {header.leap_second}); "
            f"$version {header.version!r}")
    ctx.say(f"3. {len(morse.marks)} marks on layer 200 below the die: {morse.code} -> {morse.text!r}")
    ctx.say(f"4. failure messages:")
    for name, r in msgs.items():
        ctx.say(f"     {name:30s} -> {r.message!r}")
    ctx.say(f"5. {logo.count} loose {logo.pitch_um:.1f} um met2 squares form a "
            f"{len(logo.pixels[0])}x{len(logo.pixels)} picture (the Jane Street logo)")
    ctx.say("6. the region map draws the letters J, S and C (see docs/img/regions.png)")
    if wire:
        ctx.say(f"7. floating wire: {wire.pins} share net {wire.net} with no driver in a metal-only "
                f"extraction. Tied low: {wire.message_if_low!r}; tied high: {wire.message_if_high!r}. "
                f"Nearby nets that restore every message on {len(battery)} boards: {wire.repaired_by}. "
                f"The `poly` step shows which one it really reaches.")
    return {"vcd_text": hidden, "vcd_date": header.date, "vcd_version": header.version,
            "morse": morse.text, "morse_code": morse.code, "logo_squares": logo.count,
            "logo_size": [len(logo.pixels[0]), len(logo.pixels)],
            "messages": {k: v.message for k, v in msgs.items()},
            "floating": wire.__dict__ if wire else None}


@_timed
def step_crosscheck(ctx: Context) -> dict:
    if not iverilog.available():
        ctx.say("iverilog not found; skipping the Icarus cross-check")
        return {"skipped": True}
    vfile = ctx.build / "puzzle_extracted.v"
    ctx.netlist.to_verilog(vfile, ctx.lib)
    rng = random.Random(3)
    touching = starbattle.solve(ctx.regions, adjacency=False, require_touching=True, limit=2)
    boards = [ctx.solution, [0] * 121, [1] * 121, *touching]
    boards += [[rng.randint(0, 1) for _ in range(121)] for _ in range(8)]
    stim = [row for b in boards for row in analysis.stimulus(b)]
    cc = iverilog.cross_check(ctx.netlist, ctx.lib, vfile, paths.VERILOG_MODELS, stim, ctx.build / "icarus")
    ctx.say(f"Icarus Verilog with the official SKY130 cell models: {cc.compared} cycles compared, "
            f"{len(cc.mismatches)} mismatches, {len(cc.unknown_cycles)} cycles with X "
            f"(all from the floating wire)")
    return {"ok": cc.ok, "compared": cc.compared, "mismatches": cc.mismatches, "x_cycles": cc.unknown_cycles}


@_timed
def step_poly(ctx: Context) -> dict:
    """Extract again, this time following gate poly, and see what changes."""
    nlp, _ = extract.extract(str(paths.PUZZLE_GDS), ctx.lib.pin_directions(), through_poly=True)
    nlp.to_json(ctx.build / "puzzle_poly.json")
    ctx.__dict__["poly_netlist"] = nlp
    metal = {(i.name, p): n for i in ctx.netlist.instances for p, n in i.pins.items()}
    groups: dict[str, set] = {}
    for i in nlp.instances:
        for p, n in i.pins.items():
            groups.setdefault(n, set()).add(metal[(i.name, p)])
    merged = [sorted(g) for g in groups.values() if len(g) > 1]
    touching = starbattle.solve(ctx.regions, adjacency=False, require_touching=True, limit=1)[0]
    msg = analysis.run_boards(nlp, ctx.lib, [touching])[0].message
    drivers = {}
    for g in merged:
        for inst in ctx.netlist.logic_instances():
            for p, n in inst.pins.items():
                if n in g and p in ctx.lib.cells[inst.cell].outputs:
                    drivers[n] = f"{inst.name} ({short_type(inst.cell)}.{p})"
    ctx.say(f"following gate poly merges {len(merged)} pair(s) of metal nets: {merged}; drivers: {drivers}")
    ctx.say(f"floating inputs left: {nlp.floating or 'none'}; the touching-stars board now prints {msg!r}")
    return {"merged": merged, "drivers": drivers, "floating": nlp.floating, "touching_message": msg}


@_timed
def step_magic(ctx: Context) -> dict:
    """Compare with an independent extraction by Magic VLSI."""
    spice = ctx.build / "magic" / "puzzle_magic.spice"
    if not spice.exists():
        if not magic.available():
            ctx.say("magic not found; skipping the Magic cross-check")
            return {"skipped": True}
        ctx.say("running Magic (about 90 s)...")
        magic.run(paths.PUZZLE_GDS, paths.PDK_ROOT, ctx.build / "magic")
    theirs = magic.read_spice(spice)
    magic.set_directions(theirs, ctx.lib)
    poly = ctx.__dict__.get("poly_netlist") or extract.extract(
        str(paths.PUZZLE_GDS), ctx.lib.pin_directions(), through_poly=True)[0]
    vs_metal = magic.compare(ctx.netlist, theirs)
    vs_poly = magic.compare(poly, theirs)
    rng = random.Random(4)
    touching = starbattle.solve(ctx.regions, adjacency=False, require_touching=True, limit=3)
    boards = [ctx.solution, [0] * 121, [1] * 121, *touching] + [[rng.randint(0, 1) for _ in range(121)] for _ in range(20)]
    a = analysis.run_boards(poly, ctx.lib, boards)
    b = analysis.run_boards(theirs, ctx.lib, boards)
    same = all(x.bytes == y.bytes and x.success == y.success for x, y in zip(a, b))
    ctx.say(f"Magic: {vs_poly['cells_magic']} cells (taps dropped), same cell counts: {vs_poly['same_cell_counts']}")
    ctx.say(f"same graph as our metal-only netlist: {vs_metal['same_graph']}; "
            f"as our poly-aware netlist: {vs_poly['same_graph']}; same outputs on {len(boards)} boards: {same}")
    return {"ok": vs_poly["same_graph"] and same, "vs_metal": vs_metal, "vs_poly": vs_poly,
            "functional": same}


STEPS = {
    "extract": step_extract,
    "warmup": step_warmup,
    "replay": step_replay,
    "analyze": step_analyze,
    "solve": step_solve,
    "sat": step_sat,
    "crack": step_crack,
    "eggs": step_eggs,
    "crosscheck": step_crosscheck,
    "poly": step_poly,
    "magic": step_magic,
}
