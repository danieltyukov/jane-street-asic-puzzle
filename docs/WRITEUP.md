# Write-up: from polygons to `(* TWO STARS *)`

This is the long version of the README. It goes through the solve in the order it actually
happened, with the details that matter if you want to build the same tools yourself or point them at
another chip. Every number here comes out of `make all`; `build/results.log` has the raw output.

Contents

1. [What you are given](#1-what-you-are-given)
2. [The warm-up is your test bench](#2-the-warm-up-is-your-test-bench)
3. [Extracting a netlist](#3-extracting-a-netlist)
4. [A simulator you can trust](#4-a-simulator-you-can-trust)
5. [Finding out what the chip does](#5-finding-out-what-the-chip-does)
6. [The solve](#6-the-solve)
7. [SAT: the answer without the understanding](#7-sat-the-answer-without-the-understanding)
8. [The output generator and its LFSR](#8-the-output-generator-and-its-lfsr)
9. [Easter eggs](#9-easter-eggs)
10. [The floating wire, properly](#10-the-floating-wire-properly)
11. [Things worth knowing before you start your own](#11-things-worth-knowing-before-you-start-your-own)

## 1. What you are given

The [puzzle repository](https://github.com/janestreet/asic-puzzle-2026) has four things:

- `puzzle.gds`: the final mask layout, 200 x 300 um, made with the open SKY130 process and the
  LibreLane flow.
- `example_inputs.vcd`: a waveform of someone feeding the chip two wrong inputs.
- `layout.png`: a picture of the layout with the I/O pins named and the output generator marked.
- `warmup/`: a small design (two shift registers, an adder and a comparator) at every stage of the
  flow, from Verilog source to final GDS.

The I/O are `clk`, `rst_n`, `enable` and a serial input `I` on the left, an 8-bit output bus `O` and
a `success` flag on the right.

A GDS file is a tree of cells, each holding polygons on (layer, datatype) pairs. Opening this one with
the KLayout Python module shows one top cell, `puzzle`, that places 9875 instances: 1618 standard
cells, a few thousand via cells (`VIA_M1M2_PR` and friends), and 36 tiny custom cells on a layer that
SKY130 does not use (more on those in [section 9](#9-easter-eggs)). The cell names survived, so every
instance tells you its type. Inside each standard cell master the pin labels survived too. Instance
names and net names did not.

The SKY130 layers that carry signals:

| Layer | GDS drawing | Pin | Label | Notes |
|---|---|---|---|---|
| poly | 66/20 | 66/16 | 66/5 | transistor gates |
| licon | 66/44 | | | contact from li1 down to poly or diffusion |
| li1 | 67/20 | 67/16 | 67/5 | local interconnect, where cell pins live |
| mcon | 67/44 | | | li1 to met1 |
| met1 | 68/20 | 68/16 | 68/5 | supply rails run here |
| via1..via4 | 68/44 .. 71/44 | | | between metals |
| met2..met5 | 69/20 .. 72/20 | | | routing, power straps |

## 2. The warm-up is your test bench

The warm-up exists so you can test your tools on something where the answer is known. The key file is
`03_post_place_and_route.def`. It is the same physical design as `04_final.gds`, but it still has the
real instance names, placements and nets.

So the extractor was written against the warm-up first. `asicre warmup` extracts `04_final.gds`,
matches each extracted instance to a DEF component by cell type and lower-left corner, and then checks
that every DEF net groups exactly the same (instance, pin) pairs as the extracted net. Result: 230 of
230 instances, 84 of 84 nets. Then it simulates the extracted adder and checks
`S == (A + B == 496)` for 1024 input pairs, including every pair that actually sums to 496.

Only after both of those passed did the extractor touch the puzzle.

## 3. Extracting a netlist

[`extract.py`](../src/asicre/extract.py) builds connectivity from geometry, then attaches pins.

Start with the copper. For each routing layer (li1 and met1 to met5), flatten every shape in the
design into one region, include the pin shapes, and merge. After merging, one polygon is one piece of
continuous metal. On the puzzle that is 6680 li1 polygons, 3001 on met1, 2060 on met2, 811 on met3, 45 on met4 and 18 on
met5.

Then the vias. Each cut shape (mcon, via1 to via4) sits inside a polygon on the layer below and a
polygon on the layer above. Look both up by the via's centre and join them in a union-find. A bucket
grid over the polygon bounding boxes keeps the lookups fast. There are 33,323 cuts. If a cut has nothing above or
below it, that is a red flag; there are none.

The top cell has text labels on met3 (the I/O pins) and met4/met5 (VPWR, VGND). Any net
that contains a labelled polygon gets that name.

Pins come last. Every standard cell master has labels like `A`, `B`, `Y` on li1 and `VPWR`/`VGND` on
met1. For each placed instance, transform each label position by the instance transform and find the
polygon under it. Its net is the pin's net. Some pins have several labels; they must all agree, and on
this chip they always do.

One thing went wrong on the first try. The tap cell (`tapvpwrvgnd_1`) has no `prBoundary` shape, so
its bounding box comes from the well layer, which is wider than the cell. Matching against the DEF
failed for exactly the 93 tap cells until the placement box came from the met1 rails instead. Body
labels (`VPB`, `VNB`) are skipped on purpose: they sit on the well layers, not on routing, and the tap
cells tie them to the rails.

Why stop at li1 and not follow contacts down into the transistors? Because diffusion would join source
to drain through every transistor, and nets would merge through the cells. Poly is safe to follow, and
section 10 shows the one place where it matters.

Result: 1618 cells, of which 728 have a logic function (the rest are 676 taps, 204 decaps and 10
antenna diodes), 92 flip-flops (84 `dfrtp` with async reset, 4 `dfstp` with async set, 4 `dfxtp`
with no reset), and two input pins whose net has no driver.

## 4. A simulator you can trust

Cell behaviour comes from the Liberty timing file. Each output pin has a `function` such as
`(!A1&!B1) | (!A2&!B1) | (!A3&!B1)` (that is `a31oi`), and each flop has an `ff` group naming its clock,
next state and async clear or preset. [`liberty.py`](../src/asicre/liberty.py) parses the 10 MB file
once and caches the 428 cells as JSON.

All 92 flops must be clocked from `clk` through buffers only; the simulator checks this
by walking back from every clock pin and refuses to run otherwise. The 642 combinational gates are
sorted topologically (the longest path between flops is 14 gates) and the whole sorted list becomes one generated
Python function.

The part that makes everything else cheap: each net is a Python integer and bit k of it is the value in simulation k.
`a AND b` is `a & b`, `NOT a` is `~a` (masked at the end). One evaluation of the netlist therefore runs
as many independent simulations as you like, which is what makes the experiments in the next section
cheap.

One clock cycle goes like this: apply inputs, settle the logic, apply async resets, then on the clock edge every flop
takes its D. Inputs change on the falling edge and outputs are compared after the rising edge, which is
the convention in the sample VCD.

The results post warns about simulators that reproduce the sample waveform and are still wrong (one
solver's model left every tie-high cell at 0, which silently turned off the adjacency check). So this
one is checked three ways:

1. Replaying `example_inputs.vcd`: 1248 output samples over 312 cycles, no mismatches.
2. The warm-up adder, 1024 cases.
3. Icarus Verilog running `build/puzzle_extracted.v` against the official SKY130 functional models,
   on 13 boards (the solution, empty, full, two with touching stars, eight random): 1903 cycles agree.
   Icarus reports X on 8 more cycles. Those are exactly the bytes that pass through the floating wire,
   which Icarus models as Z instead of guessing.

## 5. Finding out what the chip does

### The impulse-response experiment

The cheapest question you can ask a sequential circuit is "what changes if I flip one input bit?".
This experiment is the part of the solve most worth stealing for other chips.
With 122 lanes: lane p puts a single star at board position p, lane 121 puts none. After 121 input
cycles, XOR every flop with the empty lane. The positions that flipped a flop are what that flop
"hears". Keeping a second record of every position that flipped it at any time during the run
separates counters (same set at the end as during the run) from pipelines (they forget).

[`analysis.classify`](../src/asicre/analysis.py) sorts the 92 flops with nothing but those two sets:

| Pattern | Count | Meaning |
|---|---|---|
| hears one column of 11 cells, same set during the run | 11 | lowest bit of a column counter |
| hears an irregular group, same set during the run | 11 | lowest bit of a region counter |
| hears one position at the end but many during the run | 12 | stage of a delay line |
| hears every position | 1 | lowest bit of the star counter |
| hears a scrambled half of the board, different set during the run | 8 | LFSR in the output generator |
| hears nothing | 49 | control state, upper counter bits |

The eleven region groups have sizes 4, 5, 6, 7, 8, 8, 9, 11, 14, 21 and 28. They add up to 121 and
never overlap, so they partition the board. That is the region map, read straight out of the hardware.

### The islands

The puzzle notes say the layout is arranged to hint at the function. Single-linkage clustering of the
logic cells (two cells join an island when their boxes are within 3 um) gives 17 islands. Naming them
needs no guessing: an island is named after the flops it contains, and an island with no flops is named
after what it drives. The 147-gate island with no flops, fed by the position counters and driving the
region counters, is the region ROM. A small 4-gate island fed by the region counters is the "all
regions have two" check.

### The control flops

The 49 flops that hear nothing are easiest to read from their waveforms in the empty lane
(`u852` is the low bit of the column index, toggling every cycle and wrapping at 10):

- The position counters are a 4-bit column index counting 0 to 10, a 4-bit row index that steps when the
  column wraps, and a done flag that sets after the 121st input.
- The row check is cheaper than eleven counters. Rows arrive one after another, so the design keeps a single 2-bit counter (`u634`,
  `u560`) that counts stars in the current row and clears at the end of each row, plus a sticky flag
  (`u517`) that sets when a row ends with a count other than 2. A board with two stars in row 0 and
  nothing else sets it at the end of row 1; three stars in row 0 sets it at the end of row 0.
- The adjacency check is a 12-stage delay line that holds the last 12 inputs. When a star arrives, its
  left neighbour went in 1 cycle ago, and its upper-right, upper and upper-left neighbours went in 10, 11 and
  12 cycles ago. That is every neighbour that has already been seen, so a 12-bit window is enough. A
  sticky flag (`u860`) sets on the first touching pair.
- The star counter is a 7-bit binary count (`u331` is bit 0, then `u206`, `u248`, `u292`, `u185`,
  `u225`, `u269`), enough for 0 to 121.
- In the output generator, four `dfxtp` flops count the output bytes, three more hold the play state and
  the registered `success` output.

## 6. The solve

The Star Battle rules: two stars in every row, every column and every region, and no two stars
touching, not even diagonally. [`starbattle.py`](../src/asicre/starbattle.py) writes that as
pseudo-boolean constraints for z3 and asks for solutions, adding a blocking clause after each one.
There is exactly one.

The board goes into the chip row by row, top-left first, one cell per clock with `enable` high.
`success` goes high on the clock after the 121st bit (122 clocks after reset in the simulation), and the
output bus plays `(* TWO STARS *)`, one byte per cycle. `build/solution.vcd` has the whole run and opens
in Surfer or GTKWave.

## 7. SAT: the answer without the understanding

[`sat.py`](../src/asicre/sat.py) treats the chip as a black box. It unrolls the netlist for 121 input
cycles plus 3 idle ones. Each cycle, each flop gets a fresh boolean variable constrained to equal the
previous cycle's D input, and the combinational logic is written out as z3 expressions over those
variables and the input bit. Flops with an async reset start at their reset value; the four `dfxtp`
flops start as free variables so the solver cannot depend on a lucky power-up state.

Building the model takes about 6 seconds, solving takes a fraction of one. z3 returns one input
sequence, proves there is no second one, and it is the same board as above. This is the shortest route
to the answer, and it tells you nothing about the chip, which is why the rest of this write-up exists.

## 8. The output generator and its LFSR

The results post says the solution string is stored XORed with a "checksum" of the board computed by an
LFSR. Everything below was recovered from simulation traces, using the eight flops the impulse
experiment flagged as scrambled.

First, the chain. Over 120 cycles of 16 random boards, flop B is the next stage after flop A if
B(t+1) == A(t) on every cycle in every lane. That gives one shift chain:
`u1149 -> u1130 -> u1098 -> u1116 -> u1046 -> u1081 -> u1013 -> u1027`.

Next, the feedback. Trying all subsets of stages finds the head's next value is
`I xor s3 xor s4 xor s5 xor s7`. That is the polynomial `x^8 + x^6 + x^5 + x^4 + 1`, the standard
maximal-length 8-bit LFSR, period 255. With the board bit XORed in, the register ends up holding an
8-bit signature of the board. The reset value is `0xa5` (four `dfstp` flops preset to 1, four `dfrtp`
cleared to 0). A software model of the register gives the same signature as the simulated chip.

Playback works differently. Once loading is done, the register's next state is still a linear map of
its current state, now without the input bit. That map is the loading map raised to the 8th power, so the register jumps 8 LFSR steps
per output byte.

To get the ROM, force the `success` flop high after a wrong board. The chip plays gibberish, and
`output XOR state` gives the same 15 bytes for every board:
`4d ad fb 83 13 79 1c b5 79 63 c7 68 93 f5 8f`. That is the encrypted message.

And the crack: the key is only 8 bits. Trying all 256 seeds and keeping the decodes that are all
printable ASCII leaves exactly one: seed `0x65`, which decodes to `(* TWO STARS *)`. And `0x65` is the
signature of the solved board. So the secret could be read without solving the puzzle, which is the
attack the results post mentions.

## 9. Easter eggs

### The waveform's hidden text

The two failed attempts in `example_inputs.vcd` are 121-bit boards.
Read each board row as a 7-bit character with bit 0 in the leftmost column (the last four columns are
always 0). Eleven rows per attempt, two attempts: `The night sky awaits` and two trailing spaces.

### The VCD header

`$date Sat Dec 31 23:59:60 2016`. Second 60 does exist, once in a while: a leap
second was inserted at the end of 2016. Python's `datetime` refuses to parse it, which is a nice way to
detect it. The `$version` line says to look at the file in a waveform viewer instead of reading it.

### Morse code below the die

The 36 custom cells sit in one row under the chip on layer 200. They come
in two widths, 1.38 um (`INTERNAL_3`) and 4.14 um (`INTERNAL_7`), exactly 1 and 3 units. The gaps are 1,
3 and 7 units, the textbook spacing between symbols, letters and words. Decoded:
`PER ARENAM AD ASTRA`, "through the sand to the stars", which fits a chip made from silicon.

### The failure messages

The empty board prints `EMPTY SKY`, the full board `BIG BANG`. A board that
passes every count but has touching stars prints `TWO NOT TOUCH` (or `TWO"NOT TOUCH`, see below).
Everything else, including a single star, prints `TRY AGAIN`. Sweeping the number of stars shows the
star counter picks `EMPTY SKY` only at 0 and `BIG BANG` only at 121.

### The logo

The top cell has 1366 met2 boxes of exactly 0.3 x 0.3 um on a 0.3 um grid, connected to
nothing. Drawn as pixels they form a 57 x 57 Jane Street logo.

### The letters

Three of the eleven regions are drawn as J, S and C. You only notice once you colour
them in, which is presumably the point.

## 10. The floating wire, properly

The metal-only extraction finds one net, `n277`, that reaches two inputs (A1 of `u507`, an `a31oi_2`,
and A1 of `u508`, an `a311o_2`) and has no driver. What it prints depends on the value you assume:

- tied low: `TWO"NOT TOUCH`. The quote is `0x22`, a space (`0x20`) with bit 1 set.
- tied high: `TWO NOT TOUCJ` and two junk bytes.
- Icarus: X in exactly those bytes.

Connecting each net driven within 30 um in turn as the missing driver, six of them give the intended
`TWO NOT TOUCH` and leave every other message right on a battery of 63 boards (the solution, empty,
full, 30 touching boards, 30 random ones). So the simulation knows something is missing but cannot
say what.

The geometry answers it. `n277` is a met1 wire from the `a311o` over to the `a31oi`, landing on li1 at
the A1 pin. Right next to it, a different net, `n252` (the output of an `and2_2`, which also drives a
`nor3`), lands on another small li1 rectangle inside the same `a31oi`, and that rectangle has no label.
The SKY130 LEF explains it:

```
PIN A1
  PORT
    LAYER li1 ;
      RECT 1.955 0.995 2.665 1.615 ;
      RECT 2.905 0.995 3.075 1.325 ;
```

The A1 pin is two li1 rectangles. Inside the cell they only connect through the gate poly (each has a
licon down to the same poly shape). The router treated both as A1, which they are, and used the cell
as a feedthrough: the `and2` output goes into one half and the wire to the `a311o` leaves from the
other. Only the first rectangle carries a text label, so a label-based extraction never sees the second
half as part of the pin.

`asicre poly` re-runs the extraction following poly through the licon contacts that sit on poly, and
not through the ones on diffusion. Poly drawn under the resistor marker (66/15) is excluded, because
the tie cells use a poly resistor between the rail and their HI/LO output. The result changes exactly
one thing: `n252` and `n277` merge. No input is left floating, and the touching-stars board prints
`TWO NOT TOUCH`.

`asicre magic` runs Magic VLSI on the GDS with the SKY130 tech file and parses its SPICE output. Magic
drops the 676 tap cells (they have no devices) and keeps the other 942. The poly-aware netlist and
Magic's netlist have identical Weisfeiler-Lehman graph signatures and give the same outputs on 26
boards. The metal-only netlist differs from Magic's by that one merge.

So in silicon the `and2` output does reach both gates, through about a micron of the `a31oi`'s gate
poly. Jane Street's results post says the wire started as a bug in their layout, that their LVS run
caught it, and that they kept it as the seventh egg.

## 11. Things worth knowing before you start your own

- Start from the warm-up and make your extractor match the DEF exactly before you look at the puzzle.
  A correct-looking netlist with one wrong pin is worse than a crash.
- Check simulators against something independent, not only the sample waveform. Here that was Icarus
  with the vendor cell models, and Magic for the extraction. The two bugs that would have hurt (the
  tap-cell box, and tie cells if they had been missed) were caught that way.
- Simulate many inputs at once. Bit-parallel lanes cost nothing extra and turn "what does this flop
  do" into a single run.
- Name things from evidence. Every block name in `layout.png` comes from flop behaviour or wiring,
  not from looking at the picture and guessing.
- A SAT solver will get you the answer quickly. It is worth doing, and it is worth going back
  afterwards to find out why that answer works.
