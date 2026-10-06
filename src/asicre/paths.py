"""Where the puzzle files, the PDK and generated outputs live."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PUZZLE = Path(os.environ.get("ASIC_PUZZLE_DIR", ROOT / "puzzle"))
BUILD = Path(os.environ.get("ASICRE_BUILD", ROOT / "build"))
PDK_ROOT = Path(os.environ.get("ASICRE_PDK_ROOT", ROOT / "pdk"))

HD = PDK_ROOT / "sky130A" / "libs.ref" / "sky130_fd_sc_hd"
LIBERTY = HD / "lib" / "sky130_fd_sc_hd__tt_025C_1v80.lib"
VERILOG_MODELS = [HD / "verilog" / "primitives.v", HD / "verilog" / "sky130_fd_sc_hd.v"]

PUZZLE_GDS = PUZZLE / "puzzle.gds"
PUZZLE_VCD = PUZZLE / "example_inputs.vcd"
WARMUP = PUZZLE / "warmup"


def require(path: Path, hint: str) -> Path:
    if not path.exists():
        raise SystemExit(f"missing {path}\n  {hint}")
    return path


def liberty_path() -> Path:
    return require(LIBERTY, "run `make pdk` to download the SKY130 HD cell library")


def puzzle_file(name: str) -> Path:
    return require(PUZZLE / name, "run `git submodule update --init` to fetch the puzzle files")
