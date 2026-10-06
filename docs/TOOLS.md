# Looking at the files yourself

The scripts do everything automatically, but the puzzle is more fun when you poke at the layout and
the waveforms by hand. These are the free tools the puzzle authors pointed to, and how to use each one
on this repository. Paths assume you ran `make setup pdk` and `make all` first.

## KLayout: the layout

[KLayout](https://www.klayout.de/) opens GDS files directly.

```
klayout puzzle/puzzle.gds
```

For proper SKY130 layer names and colours, load the layer properties that came with the PDK:
File > Load Layer Properties, then `pdk/sky130A/libs.tech/klayout/tech/sky130A.lyp`.

Things to look at:

- Hide everything except met2 (69/20) and zoom into the lower-left of the core, around x = 35 to 52 um,
  y = 35 to 52 um. The loose squares there are the logo.
- Show layer 200/0 and look under the die (negative y). The row of marks is the Morse code.
- Double-click any standard cell to descend into it. The pin labels (67/5) are still there; this is
  what the extractor reads.
- The `a31oi_2` at x = 173, y = 89.8 um is the cell from egg 7. Show li1 (67/20), licon (66/44) and
  poly (66/20) together to see the two halves of its A1 pin.

KLayout also has a Python module (`pip install klayout`), which is what
[`src/asicre/extract.py`](../src/asicre/extract.py) uses for geometry.

## Magic: the layout, and an LVS-style extraction

[Magic](https://github.com/rtimothyedwards/magic) is the other classic open layout tool. It needs the
SKY130 tech file, which `make pdk` already downloaded:

```
export PDK_ROOT=$PWD/pdk PDK=sky130A
magic -rcfile pdk/sky130A/libs.tech/magic/sky130A.magicrc
```

then, in the Magic console:

```
gds read puzzle/puzzle.gds
load puzzle
```

`asicre magic` runs the same thing headless and extracts a SPICE netlist (it takes about 90 seconds;
the script is written to `build/magic/extract.tcl` if you want to run it by hand). The result is in
`build/magic/puzzle_magic.spice`.

## TinyTapeout GDS viewer: no install

The [TinyTapeout GDS viewer](https://gds-viewer.tinytapeout.com/) renders a GDS in the browser in 3D.
Upload `puzzle/puzzle.gds` and turn layers on and off to see how the metal stack sits on top of the
cells.

## Surfer or GTKWave: the waveforms

[Surfer](https://surfer-project.org/) and [GTKWave](https://gtkwave.sourceforge.net/) both open VCD
files.

```
surfer puzzle/example_inputs.vcd     # the two failed attempts from the puzzle
surfer build/solution.vcd            # the winning run, written by `asicre solve`
```

In Surfer, add `O` and change its format to ASCII to read the messages as text. In
`example_inputs.vcd`, look at `I` while `enable` is high: those 121-bit stretches are the two boards
that hide `The night sky awaits`.

## Yosys: the extracted netlist

`build/puzzle_extracted.v` is plain structural Verilog over SKY130 cells, so standard tools read it:

```
LIB=pdk/sky130A/libs.ref/sky130_fd_sc_hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib
yosys -p "read_liberty -lib $LIB; read_verilog build/puzzle_extracted.v; hierarchy -top puzzle; stat -liberty $LIB"
```

That prints the cell counts and a chip area of 8391.8 square microns for the logic cells.

## Icarus Verilog: a second simulator

`asicre crosscheck` writes a testbench and a stimulus file to `build/icarus/` and runs:

```
H=pdk/sky130A/libs.ref/sky130_fd_sc_hd/verilog
iverilog -g2012 -DFUNCTIONAL -DUNIT_DELAY=#1 -o build/icarus/tb.vvp \
  build/icarus/tb.v build/puzzle_extracted.v $H/primitives.v $H/sky130_fd_sc_hd.v
vvp -n build/icarus/tb.vvp
```

`-DFUNCTIONAL` selects the zero-delay functional cell models, which is all you need for logic.

## z3: SAT and the Star Battle

[z3](https://github.com/Z3Prover/z3) (`pip install z3-solver`) does both the Star Battle solve and the
bounded model check of the raw netlist. See [`starbattle.py`](../src/asicre/starbattle.py) and
[`sat.py`](../src/asicre/sat.py); both are short.

## ciel: the PDK

The SKY130 files come from [ciel](https://github.com/fossi-foundation/ciel), which downloads prebuilt
open_pdks releases. `make pdk` only pulls the HD standard cell library:

```
ciel enable --pdk-family sky130 --pdk-root ./pdk --include-libraries sky130_fd_sc_hd <version>
```

The files the code uses are the Liberty timing file (cell functions), the functional Verilog models
(for Icarus), the LEF (to understand pin shapes) and the Magic tech file.
