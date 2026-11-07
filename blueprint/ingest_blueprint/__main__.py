"""CLI: python -m ingest_blueprint INPUT -o OUT_DIR [--no-llm] [--corrections file]."""

from __future__ import annotations

import argparse
import sys

from ingest_blueprint.pipeline import run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Turn a floor plan into scene.json")
    parser.add_argument("input")
    parser.add_argument("-o", "--output", required=True)
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--corrections", default=None)
    parser.add_argument("--ml", action="store_true", help="Use CubiCasa weights when data/cubicasa/model_weights.pth exists")
    args = parser.parse_args(argv)
    try:
        _scene, code = run(args.input, args.output, no_llm=args.no_llm, corrections_path=args.corrections, use_ml=args.ml)
    except Exception as exc:  # noqa: BLE001 — a bad plan should still leave a message, not a stack dump
        print(f"ingest failed: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {args.output}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
