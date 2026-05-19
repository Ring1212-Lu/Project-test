"""Entry point: launch GUI by default, or run headless CLI optimisation
to produce a PDF report.

Usage:
    python -m pallet_stacking.main                # launch GUI
    python -m pallet_stacking.main --cli ...      # headless mode
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Optional

from .optimizer import Case, Pallet, optimize, compare_solutions
from . import pdf_export


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pallet_stacking",
                                description="Pallet stacking optimizer")
    p.add_argument("--cli", action="store_true",
                   help="Run headless (no GUI). Requires the --case-* args.")
    p.add_argument("--pdf", type=str, default=None,
                   help="Write a PDF report to this path (CLI mode).")

    p.add_argument("--case-l", type=float, help="Case length (mm)")
    p.add_argument("--case-w", type=float, help="Case width  (mm)")
    p.add_argument("--case-h", type=float, help="Case height (mm)")
    p.add_argument("--case-weight", type=float, default=0.0)
    p.add_argument("--case-name", type=str, default="Case")

    p.add_argument("--pallet-l", type=float, default=1200)
    p.add_argument("--pallet-w", type=float, default=1000)
    p.add_argument("--pallet-h", type=float, default=150)
    p.add_argument("--pallet-weight", type=float, default=0.0,
                   help="Empty pallet weight in kg (for the Materials table)")
    p.add_argument("--max-height", type=float, default=1800)
    p.add_argument("--margin-front", type=float, default=0)
    p.add_argument("--margin-back",  type=float, default=0)
    p.add_argument("--margin-left",  type=float, default=0)
    p.add_argument("--margin-right", type=float, default=0)

    p.add_argument("--top-n", type=int, default=5)
    p.add_argument("--no-interlock", action="store_true")
    p.add_argument("--barcode-weight", type=float, default=0.15)
    return p


def run_cli(args) -> int:
    missing = [n for n in ("case_l", "case_w", "case_h")
               if getattr(args, n) is None]
    if missing:
        print(f"Missing required CLI args: {missing}", file=sys.stderr)
        return 2

    case = Case(length=args.case_l, width=args.case_w, height=args.case_h,
                weight=args.case_weight, name=args.case_name)
    pallet = Pallet(length=args.pallet_l, width=args.pallet_w,
                    height=args.pallet_h, max_total_height=args.max_height,
                    margin_front=args.margin_front,
                    margin_back=args.margin_back,
                    margin_left=args.margin_left,
                    margin_right=args.margin_right)

    sols = optimize(case, pallet,
                    top_n=args.top_n,
                    allow_interlock=not args.no_interlock,
                    barcode_weight=args.barcode_weight)
    if not sols:
        print("No valid stacking solution found.", file=sys.stderr)
        return 2

    rows = compare_solutions(sols)
    print(json.dumps(rows, indent=2))

    if args.pdf:
        primary = sols[0]
        pdf_export.export_pdf(args.pdf, primary,
                              top_solutions=sols,
                              product_name=case.name,
                              product_code=case.name,
                              pallet_type="Standard",
                              pallet_weight=args.pallet_weight,
                              load_ref=primary.layout_name)
        print(f"PDF written to {args.pdf}")
    return 0


def main(argv: Optional[list] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.cli:
        return run_cli(args)
    from . import gui
    gui.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
