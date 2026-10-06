"""Recover a gate-level netlist from a SKY130 GDS file.

The puzzle GDS keeps two useful things: the name of every standard cell
(``sky130_fd_sc_hd__nand2_2`` and friends) and the pin labels drawn inside each
cell master. Everything else has to be rebuilt from geometry:

1. Flatten every routing layer (li1, met1..met5) and merge touching shapes.
   Each merged polygon is one piece of copper.
2. Every via cut joins the polygon below it to the polygon above it. A
   union-find over all polygons turns that into electrical nets.
3. Every cell instance knows where its pin labels are. Transform each label
   into chip coordinates, find the polygon under it, and that polygon's net is
   what the pin connects to.

By default only li1 and the metals are used, which is what a label-based or
LEF-pin-based extraction sees. ``through_poly=True`` also follows gate poly
(through the licon contacts that land on poly). Diffusion is never followed:
source and drain would merge through the transistor channel.

The difference matters exactly once in this chip. The SKY130 ``a31oi_2`` cell
declares its A1 pin as two li1 rectangles that only meet through the gate
poly, and only one of them carries a label. The router landed one net on each
rectangle, so metal-only extraction reports a floating input there.
"""

from __future__ import annotations

import collections
from dataclasses import dataclass

import klayout.db as db

from .layers import CONDUCTORS, CUTS, LICON, POLY, POLY_RESISTOR, PR_BOUNDARY
from .netlist import CELL_PREFIX, Instance, Netlist

GRID = 2000  # spatial index bucket size in database units (2 um)
ROW_HEIGHT = 2720  # sky130_fd_sc_hd row height in database units


class UnionFind:
    def __init__(self, n: int):
        self.parent = list(range(n))

    def find(self, a: int) -> int:
        parent = self.parent
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


class PolygonIndex:
    """Bucket grid over the merged polygons of one layer, for point lookups."""

    def __init__(self, polygons: list[db.Polygon], offset: int):
        self.polygons = polygons
        self.offset = offset  # global id of polygons[0]
        self.buckets: dict[tuple[int, int], list[int]] = collections.defaultdict(list)
        for i, poly in enumerate(polygons):
            box = poly.bbox()
            for gx in range(box.left // GRID, box.right // GRID + 1):
                for gy in range(box.bottom // GRID, box.top // GRID + 1):
                    self.buckets[(gx, gy)].append(i)

    def find(self, pt: db.Point) -> int | None:
        for i in self.buckets.get((pt.x // GRID, pt.y // GRID), ()):
            poly = self.polygons[i]
            if poly.bbox().contains(pt) and poly.inside(pt):
                return self.offset + i
        return None


@dataclass
class ExtractionReport:
    polygons: dict[str, int]
    cuts: dict[str, int]
    dangling_cuts: dict[str, int]
    nets: int
    pin_conflicts: list[str]
    unlabelled_pins: list[str]
    geometry: dict[str, list[tuple[str, db.Polygon]]] | None = None  # net -> [(layer, polygon)]


def _flat_region(layout: db.Layout, top: db.Cell, *layers: tuple[int, int]) -> db.Region:
    region = db.Region()
    for ln, dt in layers:
        idx = layout.find_layer(ln, dt)
        if idx is not None:
            region.insert(db.Region(top.begin_shapes_rec(idx)))
    return region


def _abutment_box(layout: db.Layout, master: db.Cell) -> db.Box:
    """The placement box of a standard cell in its own coordinates.

    Most cells carry a prBoundary shape. The few that do not (the tap cell)
    still have met1 supply rails that span exactly the cell width, and every
    HD cell is one 2.72 um row tall with its origin at the lower-left corner.
    """
    box = _flat_region(layout, master, PR_BOUNDARY).bbox()
    if not box.empty():
        return box
    rails = _flat_region(layout, master, CONDUCTORS[1].drawing).bbox()
    return db.Box(rails.left, 0, rails.right, ROW_HEIGHT)


def _texts(layout: db.Layout, cell: db.Cell, layer: tuple[int, int]):
    idx = layout.find_layer(*layer)
    if idx is None:
        return
    for shape in cell.shapes(idx).each(db.Shapes.STexts):
        d = shape.text.trans.disp
        yield shape.text_string, db.Point(d.x, d.y)


def extract(gds_path: str, pin_directions: dict[str, dict[str, str]] | None = None,
            top_name: str | None = None, keep_geometry: bool = False,
            through_poly: bool = False) -> tuple[Netlist, ExtractionReport]:
    """Extract a netlist from ``gds_path``.

    ``pin_directions`` maps cell type -> pin -> "input"/"output" (from the
    Liberty file). It is used to classify top-level ports and to find nets that
    have loads but no driver.
    """
    layout = db.Layout()
    layout.read(gds_path)
    top = layout.cell(top_name) if top_name else layout.top_cell()
    dbu = layout.dbu

    # 1. merged copper, one index per conductor layer
    conductors = ([POLY] if through_poly else []) + CONDUCTORS
    cuts = ([LICON] if through_poly else []) + CUTS
    index: dict[str, PolygonIndex] = {}
    offset = 0
    counts = {}
    for cond in conductors:
        region = _flat_region(layout, top, cond.drawing, cond.pin).merged()
        if cond is POLY:
            # poly under the resistor marker is a resistor body (the tie cells
            # use one between the rail and HI/LO), not a wire
            region -= _flat_region(layout, top, POLY_RESISTOR)
        polys = list(region.each())
        index[cond.name] = PolygonIndex(polys, offset)
        counts[cond.name] = len(polys)
        offset += len(polys)
    uf = UnionFind(offset)

    # 2. cuts join the layer below to the layer above
    cut_counts, dangling = {}, {}
    for cut in cuts:
        n = bad = 0
        for poly in _flat_region(layout, top, cut.layer).each():
            c = poly.bbox().center()
            lo = index[cut.below].find(c)
            if lo is None and cut is LICON:
                continue  # a diffusion contact, not a poly contact
            n += 1
            hi = index[cut.above].find(c)
            if lo is None or hi is None:
                bad += 1
                continue
            uf.union(lo, hi)
        cut_counts[cut.name], dangling[cut.name] = n, bad

    # 3. names for nets that carry a top-level label (I/O pins and supplies)
    net_names: dict[int, str] = {}
    for cond in conductors:
        for text, pos in _texts(layout, top, cond.label):
            pid = index[cond.name].find(pos)
            if pid is not None:
                net_names.setdefault(uf.find(pid), text)

    # 4. instances and their pins
    cell_pins: dict[int, list[tuple[str, str, db.Point]]] = {}
    cell_boxes: dict[int, db.Box] = {}
    instances: list[tuple[db.Instance, db.Cell]] = []
    for inst in top.each_inst():
        master = inst.cell
        if not master.name.startswith(CELL_PREFIX):
            continue  # via cells and decorations
        if master.cell_index() not in cell_pins:
            pins = []
            for cond in CONDUCTORS[:2]:  # pins live on li1, rails on met1
                for text, pos in _texts(layout, master, cond.label):
                    pins.append((text, cond.name, pos))
            cell_pins[master.cell_index()] = pins
            cell_boxes[master.cell_index()] = _abutment_box(layout, master)
        instances.append((inst, master))

    # deterministic order: bottom-to-top, left-to-right
    def origin(entry):
        inst, master = entry
        box = cell_boxes[master.cell_index()].transformed(inst.trans)
        return (box.bottom, box.left)

    instances.sort(key=origin)

    pin_conflicts: list[str] = []
    unlabelled: list[str] = []
    raw: list[tuple[str, str, dict[str, int], db.Box, db.Trans]] = []
    for n, (inst, master) in enumerate(instances):
        name = f"u{n}"
        pins: dict[str, int] = {}
        for pin, layer_name, pos in cell_pins[master.cell_index()]:
            pid = index[layer_name].find(inst.trans * pos)
            if pid is None:
                unlabelled.append(f"{name}.{pin}")
                continue
            root = uf.find(pid)
            if pin in pins and pins[pin] != root:
                pin_conflicts.append(f"{name}.{pin}")
            pins.setdefault(pin, root)
        box = cell_boxes[master.cell_index()].transformed(inst.trans)
        raw.append((name, master.name, pins, box, inst.trans))

    # 5. name the remaining nets in placement order
    counter = 0
    for _, _, pins, _, _ in raw:
        for pin in sorted(pins):
            root = pins[pin]
            if root not in net_names:
                net_names[root] = f"n{counter}"
                counter += 1

    netlist = Netlist(top=top.name)
    for name, cell, pins, box, trans in raw:
        netlist.instances.append(Instance(
            name=name, cell=cell,
            pins={p: net_names[r] for p, r in pins.items()},
            x=round(box.left * dbu, 3), y=round(box.bottom * dbu, 3),
            width=round(box.width() * dbu, 3), height=round(box.height() * dbu, 3),
            orient=_orient(trans),
        ))

    # 6. ports: every labelled top-level net that is not a supply
    drivers, loads = _drivers_and_loads(netlist, pin_directions)
    for root, label in net_names.items():
        if label in netlist.supplies or label.startswith("n") and label[1:].isdigit():
            continue
        netlist.ports[label] = "output" if drivers.get(label) else "input"
    netlist.ports = dict(sorted(netlist.ports.items(), key=lambda kv: _port_key(kv[0])))
    for net, sinks in sorted(loads.items()):
        if net not in netlist.ports and net not in netlist.supplies and not drivers.get(net):
            netlist.floating.extend(sinks)

    geometry = None
    if keep_geometry:
        geometry = collections.defaultdict(list)
        for cond in conductors:
            idx = index[cond.name]
            for k, poly in enumerate(idx.polygons):
                name = net_names.get(uf.find(idx.offset + k))
                if name is not None:
                    geometry[name].append((cond.name, poly))

    report = ExtractionReport(
        polygons=counts, cuts=cut_counts, dangling_cuts=dangling,
        nets=len(set(uf.find(i) for i in range(offset))),
        pin_conflicts=pin_conflicts, unlabelled_pins=unlabelled,
        geometry=geometry,
    )
    return netlist, report


def _drivers_and_loads(netlist: Netlist, pin_directions):
    drivers: dict[str, list[tuple[str, str]]] = collections.defaultdict(list)
    loads: dict[str, list[tuple[str, str]]] = collections.defaultdict(list)
    if not pin_directions:
        return drivers, loads
    for inst in netlist.instances:
        dirs = pin_directions.get(inst.cell, {})
        for pin, net in inst.pins.items():
            d = dirs.get(pin)
            if d == "output":
                drivers[net].append((inst.name, pin))
            elif d == "input":
                loads[net].append((inst.name, pin))
    return drivers, loads


def _port_key(name: str):
    order = ["clk", "rst_n", "enable", "I", "en", "A", "B"]
    base = name.split("[")[0]
    bit = int(name.split("[")[1][:-1]) if "[" in name else -1
    return (order.index(base) if base in order else len(order), base, bit)


def _orient(trans: db.Trans) -> str:
    names = {0: "R0", 1: "R90", 2: "R180", 3: "R270", 4: "MX", 5: "MX90", 6: "MY", 7: "MY90"}
    return names.get(trans.rot + (4 if trans.is_mirror() else 0), str(trans))
