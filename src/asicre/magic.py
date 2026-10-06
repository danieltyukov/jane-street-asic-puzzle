"""Independent extraction with Magic VLSI, used to check ours.

Magic reads the GDS with the SKY130 technology file, extracts every cell down
to transistors, and ``ext2spice`` writes a hierarchical SPICE netlist whose top
level instantiates the standard cells. That is the LVS-style flow the puzzle
authors expected people to use. Here it serves as a second opinion: the two
netlists are compared structurally (same graph) and functionally (same
outputs for the same inputs).
"""

from __future__ import annotations

import collections
import hashlib
import os
import shutil
import subprocess
from pathlib import Path

from .netlist import Instance, Netlist, is_physical_only

SCRIPT = """gds readonly true
gds flatten false
gds read {gds}
load {top}
select top cell
extract do local
extract all
ext2spice lvs
ext2spice -o {out}
quit -noprompt
"""


def available() -> bool:
    return shutil.which("magic") is not None


def run(gds: Path, pdk_root: Path, workdir: Path, top: str = "puzzle") -> Path:
    workdir.mkdir(parents=True, exist_ok=True)
    out = workdir / f"{top}_magic.spice"
    script = workdir / "extract.tcl"
    script.write_text(SCRIPT.format(gds=gds.resolve(), top=top, out=out.name))
    rc = pdk_root / "sky130A" / "libs.tech" / "magic" / "sky130A.magicrc"
    env = dict(os.environ, PDK_ROOT=str(pdk_root.resolve()), PDK="sky130A")
    with open(workdir / "magic.log", "w") as log:
        subprocess.run(["magic", "-dnull", "-noconsole", "-rcfile", str(rc), script.name],
                       cwd=workdir, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
    return out


def _logical_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.splitlines():
        if raw.startswith("+") and lines:
            lines[-1] += " " + raw[1:].strip()
        elif raw.strip() and not raw.startswith("*"):
            lines.append(raw.strip())
    return lines


def read_spice(path: Path, top: str = "puzzle") -> Netlist:
    lines = _logical_lines(Path(path).read_text())
    ports: dict[str, list[str]] = {}
    current = None
    body: list[str] = []
    top_ports: list[str] = []
    for line in lines:
        words = line.split()
        if words[0].lower() == ".subckt":
            current = words[1]
            ports[current] = words[2:]
            if current == top:
                top_ports = words[2:]
        elif words[0].lower() == ".ends":
            current = None
        elif current == top and words[0].upper().startswith("X"):
            body.append(line)
    nl = Netlist(top=top)
    for p in top_ports:
        if p not in ("VPWR", "VGND"):
            nl.ports[p] = "inout"
    for line in body:
        words = line.split()
        name, nets, cell = words[0], words[1:-1], words[-1]
        pins = {}
        for pin, net in zip(ports[cell], nets):
            if pin in ("VPB", "VNB"):
                continue
            pins[pin] = net
        nl.instances.append(Instance(name=name, cell=cell, pins=pins))
    return nl


def set_directions(nl: Netlist, lib) -> None:
    driven = {n for i in nl.instances if not is_physical_only(i.cell)
              for p, n in i.pins.items() if p in lib.cells[i.cell].outputs}
    for port in nl.ports:
        nl.ports[port] = "output" if port in driven else "input"


def wl_signature(nl: Netlist, rounds: int = 6) -> collections.Counter:
    """Weisfeiler-Lehman colour refinement on the bipartite cell/net graph.

    Two netlists that are the same graph (up to renaming of instances and
    internal nets) end with the same multiset of colours. Port nets keep
    their names so the refinement is anchored to the outside world.
    """
    insts = [i for i in nl.instances if not is_physical_only(i.cell)]
    net_members = collections.defaultdict(list)
    for k, inst in enumerate(insts):
        for pin, net in inst.pins.items():
            net_members[net].append((k, pin))
    inst_col = [inst.cell for inst in insts]
    net_col = {n: (n if n in nl.ports or n in nl.supplies else "net") for n in net_members}

    def h(x) -> str:
        return hashlib.sha1(repr(x).encode()).hexdigest()[:16]

    for _ in range(rounds):
        new_inst = [h((inst_col[k], sorted((p, net_col[n]) for p, n in inst.pins.items())))
                    for k, inst in enumerate(insts)]
        new_net = {n: h((net_col[n], sorted((p, inst_col[k]) for k, p in members)))
                   for n, members in net_members.items()}
        inst_col, net_col = new_inst, new_net
    return collections.Counter(inst_col) + collections.Counter(net_col.values())


def compare(ours: Netlist, theirs: Netlist) -> dict:
    def hist(nl):
        return collections.Counter(i.cell for i in nl.instances if not i.cell.endswith("tapvpwrvgnd_1"))
    same_cells = hist(ours) == hist(theirs)
    same_graph = wl_signature(ours) == wl_signature(theirs)
    return {"same_cell_counts": same_cells, "same_graph": same_graph,
            "cells_ours": sum(hist(ours).values()), "cells_magic": sum(hist(theirs).values())}
