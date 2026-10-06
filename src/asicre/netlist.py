"""A small gate-level netlist model plus a structural Verilog writer and reader."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

CELL_PREFIX = "sky130_fd_sc_hd__"

# Cells with no logic function. They are kept in the netlist (they are real
# geometry) but skipped when writing simulation models.
PHYSICAL_ONLY = ("tapvpwrvgnd", "decap", "fill", "diode", "tap")


def short_type(cell_type: str) -> str:
    return cell_type.removeprefix(CELL_PREFIX)


def is_physical_only(cell_type: str) -> bool:
    return short_type(cell_type).startswith(PHYSICAL_ONLY)


@dataclass
class Instance:
    name: str
    cell: str
    pins: dict[str, str]
    x: float = 0.0  # placement origin in microns
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0
    orient: str = "R0"

    @property
    def center(self) -> tuple[float, float]:
        return self.x, self.y


@dataclass
class Netlist:
    top: str
    ports: dict[str, str] = field(default_factory=dict)  # port name -> "input"/"output"/"inout"
    instances: list[Instance] = field(default_factory=list)
    supplies: set[str] = field(default_factory=lambda: {"VPWR", "VGND"})
    floating: list[tuple[str, str]] = field(default_factory=list)  # (instance, pin) with no net

    def logic_instances(self) -> list[Instance]:
        return [i for i in self.instances if not is_physical_only(i.cell)]

    def nets(self) -> set[str]:
        out = set(self.ports)
        for inst in self.instances:
            out.update(inst.pins.values())
        return out

    def instance(self, name: str) -> Instance:
        for inst in self.instances:
            if inst.name == name:
                return inst
        raise KeyError(name)

    # -- persistence -----------------------------------------------------
    def to_json(self, path: str | Path) -> None:
        data = {
            "top": self.top,
            "ports": self.ports,
            "supplies": sorted(self.supplies),
            "floating": self.floating,
            "instances": [inst.__dict__ for inst in self.instances],
        }
        Path(path).write_text(json.dumps(data, indent=1))

    @classmethod
    def from_json(cls, path: str | Path) -> "Netlist":
        data = json.loads(Path(path).read_text())
        nl = cls(top=data["top"], ports=data["ports"], supplies=set(data["supplies"]))
        nl.floating = [tuple(f) for f in data.get("floating", [])]
        nl.instances = [Instance(**d) for d in data["instances"]]
        return nl

    # -- Verilog ---------------------------------------------------------
    def to_verilog(self, path: str | Path, lib=None) -> None:
        """Write a structural Verilog netlist that instantiates SKY130 cells.

        Supply nets become constant 1/0 so the file simulates with the
        functional cell models (no USE_POWER_PINS needed). Physical-only cells
        are left out. If ``lib`` (a Liberty library) is given, only the logical
        pins of each cell are connected.
        """
        lines = [f"// Extracted from GDS by asicre. Net names are generated.", ""]
        port_list = ", ".join(_vid(p) for p in self._port_bits())
        lines.append(f"module {self.top} ({port_list});")
        for bus, (direction, width) in self._port_decls().items():
            rng = f"[{width - 1}:0] " if width else ""
            lines.append(f"  {direction} {rng}{_vid(bus)};")
        internal = sorted(self.nets() - set(self.ports) - self.supplies, key=_natural)
        for net in internal:
            lines.append(f"  wire {_vid(net)};")
        lines.append("  wire VPWR = 1'b1;")
        lines.append("  wire VGND = 1'b0;")
        lines.append("")
        for inst in self.instances:
            if is_physical_only(inst.cell):
                continue
            pins = inst.pins
            if lib is not None:
                logical = lib.cells[inst.cell].pins
                pins = {p: n for p, n in pins.items() if p in logical}
            conns = ", ".join(f".{p}({_vref(n)})" for p, n in sorted(pins.items()))
            lines.append(f"  {inst.cell} {_vid(inst.name)} ({conns});")
        lines.append("endmodule")
        Path(path).write_text("\n".join(lines) + "\n")

    def _port_decls(self) -> dict[str, tuple[str, int]]:
        decls: dict[str, tuple[str, int]] = {}
        for port, direction in self.ports.items():
            m = re.fullmatch(r"(.+)\[(\d+)\]", port)
            if m:
                bus, bit = m.group(1), int(m.group(2))
                prev = decls.get(bus, (direction, 0))[1]
                decls[bus] = (direction, max(prev, bit + 1))
            else:
                decls[port] = (direction, 0)
        return decls

    def _port_bits(self) -> list[str]:
        return list(self._port_decls())


def _natural(s: str):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


def _vid(name: str) -> str:
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", name):
        return name
    return "\\" + name + " "


def _vref(net: str) -> str:
    m = re.fullmatch(r"(.+)\[(\d+)\]", net)
    if m and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", m.group(1)):
        return f"{m.group(1)}[{m.group(2)}]"
    return _vid(net)


# ---------------------------------------------------------------------------
# A reader for the flat structural netlists that Yosys/OpenROAD write. It is
# only used to compare our extraction of the warm-up against the reference.

_INST_RE = re.compile(r"^\s*(sky130_fd_sc_hd__\w+)\s+(\S+)\s*\((.*?)\);", re.S | re.M)
_PIN_RE = re.compile(r"\.(\w+)\s*\(\s*([^()]*?)\s*\)")


def read_structural_verilog(path: str | Path) -> list[tuple[str, str, dict[str, str]]]:
    text = Path(path).read_text()
    out = []
    for cell, name, body in _INST_RE.findall(text):
        pins = {p: n.strip() for p, n in _PIN_RE.findall(body) if n.strip()}
        out.append((cell, name.lstrip("\\"), pins))
    return out
