"""Figures for the README and the write-up (written to docs/img)."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import Rectangle

from . import analysis, eggs, paths, starbattle
from .netlist import is_physical_only, short_type
import matplotlib.patches

OUT = paths.ROOT / "docs" / "img"

# Reference categorical palette (light mode), in its fixed order.
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#8a8984"
GRID = "#e4e3df"
NEUTRAL = ["#ecebe7", "#dddcd7"]

BLOCK_COLOR = dict(zip(analysis.BLOCKS, SERIES))


def _style() -> None:
    family = "Inter" if any("Inter" in f.name for f in font_manager.fontManager.ttflist) else "DejaVu Sans"
    plt.rcParams.update({
        "font.family": family,
        "font.size": 10,
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "savefig.facecolor": SURFACE,
        "axes.edgecolor": GRID,
        "axes.labelcolor": INK_2,
        "xtick.color": INK_2,
        "ytick.color": INK_2,
        "text.color": INK,
    })


def _save(fig, name: str) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    fig.savefig(path, dpi=160, bbox_inches="tight", pad_inches=0.25)
    plt.close(fig)
    return path


# ---------------------------------------------------------------------------

def layout(ctx) -> Path:
    nl = ctx.netlist
    block_of = {}
    for isl in ctx.islands:
        for c in isl.cells:
            block_of[c] = isl.name
    fig, ax = plt.subplots(figsize=(7.2, 10.4))
    ax.add_patch(Rectangle((0, 0), 200, 300, fill=False, ec=MUTED, lw=0.8))
    for inst in nl.instances:
        name = block_of.get(inst.name)
        if is_physical_only(inst.cell) or name is None:
            color, z = (GRID if is_physical_only(inst.cell) else NEUTRAL[1]), 1
        elif name == "clock tree":
            color, z = MUTED, 2
        else:
            color, z = BLOCK_COLOR[name], 3
        ax.add_patch(Rectangle((inst.x, inst.y), inst.width, inst.height, fc=color, ec=SURFACE, lw=0.25, zorder=z))
    # one direct label per block, on its largest island
    seen = set()
    for isl in ctx.islands:
        if isl.name in seen or isl.name in ("", "clock tree"):
            continue
        seen.add(isl.name)
        x0, y0, x1, y1 = isl.bbox
        ax.text((x0 + x1) / 2, y1 + 2.5, isl.name, ha="center", va="bottom", fontsize=8.5, color=INK,
                zorder=5, bbox=dict(boxstyle="round,pad=0.25", fc=SURFACE, ec="none", alpha=0.9))
    for port, direction in nl.ports.items():
        y = _port_y(ctx, port)
        if y is None:
            continue
        x = 0 if direction == "input" else 200
        ax.annotate(port, (x, y), xytext=(-6 if x == 0 else 6, 0), textcoords="offset points",
                    ha="right" if x == 0 else "left", va="center", fontsize=8, color=INK_2)
    handles = [Rectangle((0, 0), 1, 1, fc=BLOCK_COLOR[b]) for b in analysis.BLOCKS]
    handles += [Rectangle((0, 0), 1, 1, fc=MUTED), Rectangle((0, 0), 1, 1, fc=GRID)]
    labels = analysis.BLOCKS + ["clock tree", "tap / decap / diode"]
    ax.legend(handles, labels, loc="upper center", bbox_to_anchor=(0.5, -0.02), ncol=3, frameon=False,
              fontsize=8.5, handlelength=1.0, handleheight=1.0)
    ax.set_xlim(-28, 228)
    ax.set_ylim(-6, 306)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_title("Placed cells of puzzle.gds, coloured by recovered block", fontsize=11, loc="left", color=INK)
    return _save(fig, "layout.png")


def _port_y(ctx, port):
    import klayout.db as db
    if not hasattr(ctx, "_port_pos"):
        layout = db.Layout()
        layout.read(str(paths.PUZZLE_GDS))
        top = layout.top_cell()
        pos = {}
        for li in layout.layer_indexes():
            for s in top.shapes(li).each(db.Shapes.STexts):
                pos[s.text_string] = s.text.trans.disp.y * layout.dbu
        ctx._port_pos = pos
    return ctx._port_pos.get(port)


# ---------------------------------------------------------------------------

def _letter_regions(regions: list[str]) -> dict[str, str]:
    """The three regions drawn as letters, found by shape: S and C are the
    regions whose cells match those glyphs; J is the remaining small region
    next to them. Returns {region label: letter}."""
    glyphs = {
        "S": {(0, 0), (0, 1), (0, 2), (1, 0), (2, 0), (2, 1), (2, 2), (3, 2), (4, 0), (4, 1), (4, 2)},
        "C": {(0, 0), (0, 1), (0, 2), (1, 0), (2, 0), (3, 0), (4, 0), (4, 1), (4, 2)},
        "J": {(0, 0), (0, 1), (0, 2), (1, 0), (1, 1), (2, 1), (3, 0), (3, 1)},
    }
    found = {}
    for lab in sorted({ch for row in regions for ch in row}):
        cells = {(r, c) for r in range(11) for c in range(11) if regions[r][c] == lab}
        r0 = min(r for r, _ in cells)
        c0 = min(c for _, c in cells)
        norm = {(r - r0, c - c0) for r, c in cells}
        for letter, shape in glyphs.items():
            if norm == shape:
                found[lab] = letter
    return found


def regions(ctx) -> Path:
    grid = ctx.regions
    board = ctx.solution
    letters = _letter_regions(grid)
    letter_color = {"J": SERIES[0], "S": SERIES[1], "C": SERIES[2]}
    labels = sorted({ch for row in grid for ch in row})
    fig, axes = plt.subplots(1, 2, figsize=(10, 5.2))
    for k, ax in enumerate(axes):
        for r in range(11):
            for c in range(11):
                lab = grid[r][c]
                if lab in letters:
                    fc = letter_color[letters[lab]]
                    alpha = 0.85
                else:
                    fc = NEUTRAL[labels.index(lab) % 2]
                    alpha = 1.0
                ax.add_patch(Rectangle((c, 10 - r), 1, 1, fc=fc, alpha=alpha, ec="none"))
                if k == 1 and board[r * 11 + c]:
                    ax.plot(c + 0.5, 10 - r + 0.5, marker="*", ms=17, color=INK, mec=SURFACE, mew=1.2)
        # thin grid, thick region borders
        for i in range(12):
            ax.plot([0, 11], [i, i], color=SURFACE, lw=0.8)
            ax.plot([i, i], [0, 11], color=SURFACE, lw=0.8)
        for r in range(11):
            for c in range(11):
                y = 10 - r
                if c + 1 < 11 and grid[r][c] != grid[r][c + 1]:
                    ax.plot([c + 1, c + 1], [y, y + 1], color=INK, lw=2.2, solid_capstyle="round")
                if r + 1 < 11 and grid[r][c] != grid[r + 1][c]:
                    ax.plot([c, c + 1], [y, y], color=INK, lw=2.2, solid_capstyle="round")
        ax.add_patch(Rectangle((0, 0), 11, 11, fill=False, ec=INK, lw=2.6))
        ax.set_xlim(-0.2, 11.2)
        ax.set_ylim(-0.2, 11.2)
        ax.set_aspect("equal")
        ax.axis("off")
    for lab, letter in letters.items():
        r, c = min((r, c) for r in range(11) for c in range(11) if grid[r][c] == lab)
        axes[0].text(c + 0.5, 10 - r + 0.5, letter, ha="center", va="center", fontsize=13,
                     fontweight="bold", color=SURFACE)
    axes[0].set_title("Region map read out of the chip (11 regions)", loc="left", fontsize=11)
    axes[1].set_title("The only valid board: 2 stars per row, column, region", loc="left", fontsize=11)
    return _save(fig, "regions.png")


def impulse(ctx) -> Path:
    roles = ctx.roles
    cols = [roles.column_counters[c] for c in sorted(roles.column_counters)]
    regs = roles.region_counters
    fig, axes = plt.subplots(2, 11, figsize=(11, 2.6))
    sets = [ctx.responses[f].final for f in cols] + [s for s, _ in regs]
    names = cols + [f for _, f in regs]
    for ax, s, name in zip(axes.flat, sets, names):
        for p in range(121):
            r, c = divmod(p, 11)
            ax.add_patch(Rectangle((c, 10 - r), 1, 1, fc=SERIES[0] if p in s else NEUTRAL[0], ec=SURFACE, lw=0.3))
        ax.set_xlim(0, 11)
        ax.set_ylim(0, 11)
        ax.set_aspect("equal")
        ax.axis("off")
        ax.set_title(name, fontsize=7, color=INK_2)
    fig.text(0.01, 0.95, "Column counters: each flop flips for the stars in one column", fontsize=10, color=INK)
    fig.text(0.01, 0.47, "Region counters: each flop flips for the stars in one region", fontsize=10, color=INK)
    fig.subplots_adjust(hspace=0.55, top=0.86)
    return _save(fig, "impulse.png")


def logo(ctx) -> Path:
    bmp = eggs.met2_logo(str(paths.PUZZLE_GDS))
    fig, ax = plt.subplots(figsize=(4.6, 4.6))
    h = len(bmp.pixels)
    for r, row in enumerate(bmp.pixels):
        for c, on in enumerate(row):
            if on:
                ax.add_patch(Rectangle((c, h - 1 - r), 0.86, 0.86, fc=SERIES[6], ec="none"))
    ax.set_xlim(-1, len(bmp.pixels[0]) + 1)
    ax.set_ylim(-1, h + 1)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_title(f"{bmp.count} loose met2 squares, {bmp.pitch_um:.1f} um pitch", fontsize=10, loc="left")
    return _save(fig, "met2_logo.png")


def morse(ctx) -> Path:
    m = eggs.morse(str(paths.PUZZLE_GDS))
    fig, ax = plt.subplots(figsize=(11, 1.5))
    x_end = max(x1 for _, x1, _, _ in m.marks)
    height = (m.marks[0][3] - m.marks[0][2]) / 1000
    for x0, x1, y0, y1 in m.marks:
        ax.add_patch(Rectangle((x0 / 1000, 0), (x1 - x0) / 1000, height, fc=INK, ec="none"))
    # letter labels under each group
    groups, cur = [], [m.marks[0]]
    unit = min(x1 - x0 for x0, x1, _, _ in m.marks)
    for prev, mark in zip(m.marks, m.marks[1:]):
        if mark[0] - prev[1] > 1.5 * unit:
            groups.append(cur)
            cur = []
        cur.append(mark)
    groups.append(cur)
    letters = m.text.replace(" ", "")
    for g, letter in zip(groups, letters):
        ax.text((g[0][0] + g[-1][1]) / 2000, -0.8, letter, ha="center", va="top", fontsize=11, color=INK_2)
    ax.set_xlim(0, x_end / 1000 + 1)
    ax.set_ylim(-3.2, height + 0.6)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_title(f"Layer 200, below the die (dot = 1.38 um, dash = 4.14 um): {m.text}", fontsize=10, loc="left")
    return _save(fig, "morse.png")


def output(ctx) -> Path:
    runs = analysis.run_boards(ctx.netlist, ctx.lib, [ctx.solution], idle=22)
    data = runs[0].bytes
    start, stop = 112, 121 + 20
    fig, axes = plt.subplots(3, 1, figsize=(11, 2.9), sharex=True,
                             gridspec_kw={"height_ratios": [1, 1, 1.4]})
    cycles = list(range(start, stop))
    enable = [1 if c < 121 else 0 for c in cycles]
    success = [1 if (runs[0].success_cycle and c + 1 >= runs[0].success_cycle) else 0 for c in cycles]
    for ax, wave, name in ((axes[0], enable, "enable"), (axes[1], success, "success")):
        ax.step(cycles, wave, where="post", color=SERIES[0], lw=2)
        ax.set_yticks([])
        ax.set_ylim(-0.3, 1.4)
        ax.set_ylabel(name, rotation=0, ha="right", va="center", color=INK_2)
        ax.tick_params(axis="x", length=0)
        for sp in ax.spines.values():
            sp.set_visible(False)
    ax = axes[2]
    for c in cycles:
        b = data[c]
        ax.add_patch(Rectangle((c, 0), 1, 1, fc=NEUTRAL[0] if b else SURFACE, ec=GRID, lw=0.8))
        if b:
            ax.text(c + 0.5, 0.5, chr(b), ha="center", va="center", fontsize=11, color=INK,
                    fontfamily="DejaVu Sans Mono")
    ax.set_ylim(-0.2, 1.2)
    ax.set_yticks([])
    ax.set_ylabel("O[7:0]", rotation=0, ha="right", va="center", color=INK_2)
    for sp in ax.spines.values():
        sp.set_visible(False)
    ax.set_xlim(start, stop)
    ax.set_xlabel("clock cycle after reset")
    axes[0].set_title("Simulated outputs for the solved board", loc="left", fontsize=11)
    return _save(fig, "output.png")


def draw_all(ctx) -> list[Path]:
    _style()
    out = [layout(ctx), regions(ctx), impulse(ctx), logo(ctx), morse(ctx), output(ctx), floating_wire(ctx)]
    for p in out:
        ctx.say(f"wrote {p.relative_to(paths.ROOT)}")
    return out


def floating_wire(ctx) -> Path:
    """Zoom on the a31oi whose A1 pin is split in two li1 rectangles."""
    import klayout.db as db
    from . import extract
    _, rep = extract.extract(str(paths.PUZZLE_GDS), ctx.lib.pin_directions(), keep_geometry=True)
    geo = rep.geometry
    inst = ctx.netlist.instance(ctx.netlist.floating[0][0])
    x0, y0 = inst.x + 0.6, inst.y + 0.4
    x1, y1 = inst.x + inst.width + 2.6, inst.y + inst.height - 0.2
    window = db.Box(int(x0 * 1000), int(y0 * 1000), int(x1 * 1000), int(y1 * 1000))
    floating_net = inst.pins[ctx.netlist.floating[0][1]]
    from . import pipeline
    merged = pipeline.step_poly(ctx)["merged"]
    partner = next((n for g in merged if floating_net in g for n in g if n != floating_net), None)
    layout = db.Layout()
    layout.read(str(paths.PUZZLE_GDS))
    top = layout.top_cell()

    def shapes(layer):
        idx = layout.find_layer(*layer)
        region = db.Region(top.begin_shapes_rec(idx)) & db.Region(window)
        return list(region.merged().each())

    fig, ax = plt.subplots(figsize=(8.6, 4.6))

    def draw(poly, **kw):
        pts = [(p.x / 1000, p.y / 1000) for p in poly.each_point_hull()]
        ax.add_patch(matplotlib.patches.Polygon(pts, closed=True, **kw))

    for p in shapes((66, 20)):
        draw(p, fc="#f3d9d6", ec="#d9a9a3", lw=0.6, zorder=1)
    for net, items in geo.items():
        for layer, poly in items:
            if layer not in ("li1", "met1", "met2") or not poly.bbox().overlaps(window):
                continue
            clipped = db.Region(poly) & db.Region(window)
            for cp in clipped.each():
                if net == floating_net:
                    fc, z, a = SERIES[0], 4, 0.9
                elif net == partner:
                    fc, z, a = SERIES[1], 4, 0.9
                else:
                    fc, z, a = NEUTRAL[0], 2, 0.6
                edge = fc if fc != NEUTRAL[0] else GRID
                if layer == "li1":
                    draw(cp, fc=fc, ec="none", alpha=a * 0.7, zorder=z)
                elif layer == "met1":
                    draw(cp, fc="none", ec=edge, lw=1.4 if z > 2 else 0.8, zorder=z + 1, hatch="////")
                else:
                    draw(cp, fc="none", ec=edge, lw=1.4 if z > 2 else 0.8, zorder=z + 1, ls="--")
    poly_region = db.Region()
    for p in shapes((66, 20)):
        poly_region.insert(p)
    licon = db.Region()
    for p in shapes((66, 44)):
        licon.insert(p)
    for p in licon.interacting(poly_region).each():
        draw(p, fc=INK, ec="none", zorder=6)
    # name the two halves of the pin
    master_x = inst.x + inst.width  # the cell is rotated 180 degrees
    notes = (((2.905, 3.075), "A1, unlabelled half\n(and2 output lands here)", -0.35, "right"),
             ((1.955, 2.665), "A1, labelled half", 0.45, "left"))
    for rect, text, dx, ha in notes:
        cx = master_x - sum(rect) / 2
        ax.annotate(text, (cx, inst.y + 1.62), xytext=(cx + dx, inst.y + 2.3), ha=ha, fontsize=8.5,
                    color=INK, arrowprops=dict(arrowstyle="-", color=INK_2, lw=0.8), zorder=8,
                    bbox=dict(boxstyle="round,pad=0.2", fc=SURFACE, ec="none"))
    far = ctx.netlist.instance(ctx.netlist.floating[1][0])
    ax.annotate(f"A1 of the {short_type(far.cell)}", (179.17, 91.35), xytext=(178.75, 92.1), ha="right",
                fontsize=8.5, color=INK, arrowprops=dict(arrowstyle="-", color=INK_2, lw=0.8), zorder=8,
                bbox=dict(boxstyle="round,pad=0.2", fc=SURFACE, ec="none"))
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    ax.set_aspect("equal")
    ax.set_xlabel("x (um)")
    ax.set_ylabel("y (um)")
    for sp in ax.spines.values():
        sp.set_visible(False)
    handles = [Rectangle((0, 0), 1, 1, fc=SERIES[0], alpha=0.7),
               Rectangle((0, 0), 1, 1, fc=SERIES[1], alpha=0.7),
               Rectangle((0, 0), 1, 1, fc="#f3d9d6", ec="#d9a9a3"),
               Rectangle((0, 0), 1, 1, fc=INK),
               Rectangle((0, 0), 1, 1, fc="none", ec=MUTED, hatch="////"),
               Rectangle((0, 0), 1, 1, fc="none", ec=MUTED, ls="--")]
    labels = [f"{floating_net}: the 'floating' net (li1)", f"{partner}: and2 output (li1)", "gate poly",
              "poly contact", "met1", "met2"]
    ax.legend(handles, labels, loc="upper left", bbox_to_anchor=(1.01, 1.0), frameon=False, fontsize=8.5)
    ax.set_title(f"{short_type(inst.cell)} at ({inst.x:.1f}, {inst.y:.1f}) um: both nets land on the A1 pin, "
                 "on rectangles joined only by poly", loc="left", fontsize=10)
    return _save(fig, "floating_wire.png")
