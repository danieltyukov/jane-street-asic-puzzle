# Contributing

Issues and pull requests are welcome, especially:

- explanations that would have helped you when you were stuck
- support for other GDS puzzles or other SKY130 libraries (`sky130_fd_sc_hs`, `_ms`, ...)
- faster or clearer versions of any step

## Getting set up

```
git clone --recursive https://github.com/danieltyukov/jane-street-asic-puzzle
cd jane-street-asic-puzzle
make setup pdk
make test
```

`make test` runs the unit tests (no puzzle files needed) and the end-to-end checks, which assert every
number quoted in the README. If you change behaviour, update the README and the test together.

## Style

- Plain Python 3.10+, standard library plus `klayout`, `z3-solver`, `numpy` and `matplotlib`.
- Each step in `src/asicre/pipeline.py` returns plain data and prints a short report. Keep new steps
  the same shape so `asicre all` and `build/results.json` pick them up.
- Prefer recovering facts from the netlist or from simulation over hard-coding instance names.

## Regenerating figures

```
make figures
```

writes everything in `docs/img/`. Commit the PNGs together with the code that changed them.
