"""Entry point: launch GUI, or run a headless CLI optimisation that can
also write a PDF + Excel report.

Examples
--------
    python -m pallet_stacking.main                       # launch GUI

    python -m pallet_stacking.main --cli \\
        --case-l 400 --case-w 300 --case-h 250 --case-weight 8 \\
        --pallet-l 1200 --pallet-w 1000 --max-height 1800 \\
        --margin-front 10 --margin-back 10 --margin-left 10 --margin-right 10 \\
        --pdf out.pdf --excel out.xlsx
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Optional

from .core   import optimize, compare_solutions
from .models import Carton, Pallet
from .export import export_pdf, export_excel


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pallet_stacking",
                                description="Pallet stacking optimizer")
    p.add_argument("--cli",   action="store_true",
                   help="Run headless (no GUI).")
    p.add_argument("--pdf",   type=str, default=None,
                   help="Write a PDF report to this path.")
    p.add_argument("--excel", type=str, default=None,
                   help="Write an Excel summary to this path.")

    # carton
    p.add_argument("--case-l", type=float, help="Case length (mm)")
    p.add_argument("--case-w", type=float, help="Case width  (mm)")
    p.add_argument("--case-h", type=float, help="Case height (mm)")
    p.add_argument("--case-weight", type=float, default=0.0)
    p.add_argument("--case-name", type=str, default="Case")
    p.add_argument("--barcode-face", choices=["L", "W", "H"], default="L",
                   help="Which case axis the barcode face is normal to "
                        "(default L = side face).")

    # pallet
    p.add_argument("--pallet-l", type=float, default=1200)
    p.add_argument("--pallet-w", type=float, default=1000)
    p.add_argument("--pallet-h", type=float, default=150)
    p.add_argument("--pallet-weight", type=float, default=0.0)
    p.add_argument("--max-height",    type=float, default=1800)
    p.add_argument("--margin-front",  type=float, default=0)
    p.add_argument("--margin-back",   type=float, default=0)
    p.add_argument("--margin-left",   type=float, default=0)
    p.add_argument("--margin-right",  type=float, default=0)

    # algorithm
    p.add_argument("--top-n",          type=int,   default=5)
    p.add_argument("--no-interlock",   action="store_true")
    p.add_argument("--barcode-weight", type=float, default=100.0)
    p.add_argument("--area-weight",    type=float, default=10.0)
    p.add_argument("--cases-weight",   type=float, default=1000.0)
    return p


def run_cli(args) -> int:
    missing = [n for n in ("case_l", "case_w", "case_h")
               if getattr(args, n) is None]
    if missing:
        print(f"Missing required CLI args: {missing}", file=sys.stderr)
        return 2

    carton = Carton(length=args.case_l, width=args.case_w, height=args.case_h,
                    weight=args.case_weight, name=args.case_name,
                    barcode_face_axis=args.barcode_face)
    pallet = Pallet(length=args.pallet_l, width=args.pallet_w,
                    height=args.pallet_h, max_total_height=args.max_height,
                    margin_front=args.margin_front,
                    margin_back=args.margin_back,
                    margin_left=args.margin_left,
                    margin_right=args.margin_right,
                    weight=args.pallet_weight)

    sols = optimize(carton, pallet,
                    top_n=args.top_n,
                    allow_interlock=not args.no_interlock,
                    cases_weight=args.cases_weight,
                    barcode_weight=args.barcode_weight,
                    area_weight=args.area_weight)
    if not sols:
        print("No valid stacking solution found.", file=sys.stderr)
        return 2

    rows = compare_solutions(sols)
    print(json.dumps(rows, indent=2))

    primary = sols[0]
    if args.pdf:
        export_pdf(args.pdf, primary, top_solutions=sols,
                   product_name=carton.name, product_code=carton.name,
                   pallet_type="Standard", pallet_weight=args.pallet_weight,
                   load_ref=primary.layout_name)
        print(f"PDF written to {args.pdf}")
    if args.excel:
        export_excel(args.excel, primary, top_solutions=sols,
                     product_name=carton.name, product_code=carton.name,
                     pallet_type="Standard", pallet_weight=args.pallet_weight)
        print(f"Excel written to {args.excel}")
    return 0


def main(argv: Optional[list] = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.cli:
        return run_cli(args)
    from .gui import run
    run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
