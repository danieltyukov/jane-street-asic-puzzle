"""Check an extracted netlist against the warm-up's reference DEF.

The warm-up ships the post-route DEF, which still has the real instance and
net names. Instances are matched by cell type and placement, then the two
netlists must group the same (instance, pin) pairs into the same nets.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .netlist import Netlist, is_physical_only


@dataclass
class DefDesign:
    units: int
    components: dict[str, tuple[str, int, int, str]]  # name -> (cell, x, y, orient)
    nets: dict[str, list[tuple[str, str]]]             # net -> [(component or PIN, pin)]


def read_def(path: str | Path) -> DefDesign:
    text = Path(path).read_text()
    units = int(re.search(r"UNITS DISTANCE MICRONS (\d+)", text).group(1))
    comps = {}
    sect = re.search(r"^COMPONENTS \d+ ;(.*?)^END COMPONENTS", text, re.S | re.M).group(1)
    for m in re.finditer(r"-\s+(\S+)\s+(\S+).*?(?:PLACED|FIXED)\s+\(\s*(-?\d+)\s+(-?\d+)\s*\)\s+(\S+)", sect):
        comps[m.group(1)] = (m.group(2), int(m.group(3)), int(m.group(4)), m.group(5))
    nets = {}
    sect = re.search(r"^NETS \d+ ;(.*?)^END NETS", text, re.S | re.M).group(1)
    for block in re.split(r"\n\s*-\s+", "\n" + sect)[1:]:
        name = block.split()[0]
        head = block.split("+")[0]
        nets[name] = re.findall(r"\(\s*(\S+)\s+(\S+)\s*\)", head)
    return DefDesign(units, comps, nets)


@dataclass
class Comparison:
    matched_instances: int
    unmatched_extracted: list[str]
    unmatched_reference: list[str]
    nets_compared: int
    mismatched_nets: list[str]

    @property
    def ok(self) -> bool:
        return not (self.unmatched_extracted or self.unmatched_reference or self.mismatched_nets)


def compare(netlist: Netlist, design: DefDesign) -> Comparison:
    # 1. match instances by (cell, lower-left corner)
    by_place = {(cell, x, y): name for name, (cell, x, y, _) in design.components.items()}
    ext_to_ref, unmatched = {}, []
    for inst in netlist.instances:
        key = (inst.cell, round(inst.x * design.units), round(inst.y * design.units))
        ref = by_place.pop(key, None)
        if ref is None:
            unmatched.append(inst.name)
        else:
            ext_to_ref[inst.name] = ref
    unmatched_ref = sorted(by_place.values())

    # 2. every reference net must map onto exactly one extracted net, and the
    #    reverse, over the logical pins of matched instances
    ref_of = {}
    for net, conns in design.nets.items():
        for comp, pin in conns:
            ref_of[(comp, pin)] = net
    ext_groups: dict[str, set] = {}
    for inst in netlist.instances:
        if is_physical_only(inst.cell) or inst.name not in ext_to_ref:
            continue
        for pin, net in inst.pins.items():
            if net in netlist.supplies:
                continue
            ext_groups.setdefault(net, set()).add((ext_to_ref[inst.name], pin))
    for port in netlist.ports:
        ext_groups.setdefault(port, set()).add(("PIN", port))

    bad = []
    for net, members in ext_groups.items():
        refs = {ref_of.get(m) for m in members}
        if len(refs) != 1 or None in refs:
            bad.append(net)
            continue
        ref_net = refs.pop()
        if set(map(tuple, design.nets[ref_net])) != members:
            bad.append(net)
    return Comparison(len(ext_to_ref), unmatched, unmatched_ref, len(ext_groups), bad)
