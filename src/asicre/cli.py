"""Command line entry point: ``asicre <step>`` or ``asicre all``."""

from __future__ import annotations

import argparse
import json
import sys

from . import pipeline

ORDER = ["extract", "warmup", "replay", "analyze", "solve", "sat", "crack", "eggs", "crosscheck", "poly", "magic"]

HELP = {
    "extract": "recover the gate-level netlist from puzzle.gds",
    "warmup": "extract the warm-up GDS and check it against the reference DEF",
    "replay": "replay example_inputs.vcd through the extracted netlist",
    "analyze": "impulse-response analysis and layout islands; prints the region map",
    "solve": "solve the Star Battle and feed the answer to the chip",
    "sat": "find the winning input with a SAT solver, no understanding needed",
    "crack": "recover the output LFSR and decode the message ROM by brute force",
    "eggs": "find the Easter eggs",
    "crosscheck": "compare the Python simulator against Icarus Verilog",
    "poly": "extract again following gate poly and show what changes",
    "magic": "compare with an independent Magic VLSI extraction (needs magic)",
    "figures": "draw the figures in docs/img",
    "all": "run every step and write build/results.json",
}


def _jsonable(obj):
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items() if k not in ("netlist", "report")}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="asicre", description="Jane Street ASIC puzzle toolkit")
    sub = parser.add_subparsers(dest="step", required=True)
    for name in ORDER + ["figures", "all"]:
        sub.add_parser(name, help=HELP[name])
    args = parser.parse_args(argv)
    ctx = pipeline.Context()

    if args.step == "figures":
        from . import figures
        figures.draw_all(ctx)
        return 0

    steps = ORDER if args.step == "all" else [args.step]
    results = {}
    failed = []
    for name in steps:
        ctx.say(f"\n== {name}: {HELP[name]}")
        out = pipeline.STEPS[name](ctx)
        results[name] = out
        if out.get("ok") is False:
            failed.append(name)
    if args.step == "all":
        path = ctx.build / "results.json"
        path.write_text(json.dumps(_jsonable(results), indent=1))
        (ctx.build / "results.log").write_text("\n".join(ctx.log) + "\n")
        ctx.say(f"\nwrote {path}")
        ctx.say(f"answer: {results['solve']['answer']}")
    if failed:
        ctx.say(f"checks failed: {failed}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
