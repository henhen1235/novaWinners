"""CLI.

    python -m loop run --scene fixtures/tiny_office.scene.json --scenario sim/examples/tiny_office_fire.json \
        -o out/loop/tiny [--rounds 2] [--decide cognition|policy] [--no-llm] [--seeds 42,43,44]
    python -m loop report out/loop/tiny
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from .report import write_report
from .run_loop import SEEDS, run


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    ap = argparse.ArgumentParser(prog="python -m loop")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--scene", required=True)
    r.add_argument("--scenario", required=True)
    r.add_argument("-o", "--out", required=True)
    r.add_argument("--rounds", type=int, default=2)
    r.add_argument("--decide", choices=["policy", "cognition"], default="policy")
    r.add_argument("--no-llm", action="store_true", help="heuristic adversary/optimizer, offline occupants")
    r.add_argument("--seeds", default=",".join(map(str, SEEDS)))
    p = sub.add_parser("report")
    p.add_argument("dir")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if a.cmd == "report":
        print(f"wrote {write_report(Path(a.dir))}")
        return 0
    if a.no_llm:
        os.environ["NOVA_NO_LLM"] = "1"
    board = run(a.scene, a.scenario, a.out, a.rounds, a.decide, a.no_llm, tuple(int(s) for s in a.seeds.split(",")))
    print((Path(a.out) / "report.md").read_text())
    print(f"wrote {write_report(Path(a.out))}")
    return 0 if board else 1


if __name__ == "__main__":
    raise SystemExit(main())
