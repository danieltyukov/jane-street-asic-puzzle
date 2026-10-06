"""End-to-end checks on the real puzzle files.

These are the claims the README makes. If one of them breaks, the README is
wrong.
"""

import pytest

from asicre import eggs, iverilog, paths, pipeline, starbattle, vcd

from .conftest import needs_inputs

pytestmark = needs_inputs

ANSWER = "(* TWO STARS *)"


def test_extraction_is_clean(ctx):
    out = pipeline.step_extract(ctx)
    assert out["cells"] == 1618
    assert out["flops"] == 92
    assert sum(out["report"].dangling_cuts.values()) == 0
    assert not out["report"].pin_conflicts
    assert set(ctx.netlist.ports) >= {"clk", "rst_n", "enable", "I", "success", "O[0]", "O[7]"}


def test_warmup_matches_reference(ctx):
    out = pipeline.step_warmup(ctx)
    assert out["ok"], out


def test_vcd_replay_has_no_mismatches(ctx):
    out = pipeline.step_replay(ctx)
    assert out["ok"]
    assert out["checked"] > 1000
    assert out["messages"] == ["TRY AGAIN", "TRY AGAIN"]


def test_region_map_partitions_board(ctx):
    sizes = sorted(len(s) for s, _ in ctx.roles.region_counters)
    assert len(sizes) == 11 and sum(sizes) == 121
    assert len(ctx.roles.column_counters) == 11
    assert len(ctx.roles.delay_line) == 12


def test_solution_and_answer(ctx):
    out = pipeline.step_solve(ctx)
    assert out["unique"]
    assert out["success"]
    assert out["answer"] == ANSWER


def test_sat_agrees_with_star_battle(ctx):
    out = pipeline.step_sat(ctx)
    assert out["solutions"] == 1
    assert out["matches"]


def test_lfsr_crack_recovers_answer(ctx):
    out = pipeline.step_crack(ctx)
    assert out["hits"] == [(out["solution_signature"], ANSWER)]
    assert out["polynomial"] == "x^8 + x^6 + x^5 + x^4 + 1"


def test_easter_eggs(ctx):
    wave = vcd.read(paths.PUZZLE_VCD)
    assert eggs.vcd_hidden_text(eggs.vcd_attempts(wave)).strip() == "The night sky awaits"
    assert eggs.vcd_header(wave).leap_second
    assert eggs.morse(str(paths.PUZZLE_GDS)).text == "PER ARENAM AD ASTRA"
    logo = eggs.met2_logo(str(paths.PUZZLE_GDS))
    assert logo.count == 1366 and len(logo.pixels) == 57
    msgs = eggs.failure_messages(ctx.netlist, ctx.lib, ctx.regions, ctx.solution)
    assert msgs["empty board"].message == "EMPTY SKY"
    assert msgs["full board"].message == "BIG BANG"
    assert msgs["counts right, stars touching"].message == 'TWO"NOT TOUCH'
    assert msgs["one star"].message == "TRY AGAIN"


def test_floating_wire(ctx):
    assert sorted(p for _, p in ctx.netlist.floating) == ["A1", "A1"]
    touching = starbattle.solve(ctx.regions, adjacency=False, require_touching=True, limit=1)[0]
    wire = eggs.floating_wire(ctx.netlist, ctx.lib, touching, search=False)
    assert wire.message_if_low == 'TWO"NOT TOUCH'


@pytest.mark.skipif(not iverilog.available(), reason="iverilog not installed")
def test_icarus_cross_check(ctx):
    out = pipeline.step_crosscheck(ctx)
    assert out["ok"]
    assert not out["mismatches"]


def test_poly_joins_the_floating_wire_to_the_and2_output(ctx):
    out = pipeline.step_poly(ctx)
    assert len(out["merged"]) == 1
    floating_net = ctx.netlist.instance(ctx.netlist.floating[0][0]).pins[ctx.netlist.floating[0][1]]
    assert floating_net in out["merged"][0]
    assert not out["floating"]
    assert out["touching_message"] == "TWO NOT TOUCH"


@pytest.mark.skipif(not (paths.BUILD / "magic" / "puzzle_magic.spice").exists(),
                    reason="run `asicre magic` first (needs Magic VLSI)")
def test_magic_agrees_with_poly_aware_extraction(ctx):
    out = pipeline.step_magic(ctx)
    assert out["vs_poly"]["same_graph"]
    assert not out["vs_metal"]["same_graph"]
    assert out["functional"]
