"""Tkinter desktop GUI for the pallet stacking tool."""
from __future__ import annotations

import os
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from typing import List, Optional

import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk

from .optimizer import Case, Pallet, optimize, compare_solutions, StackingSolution
from . import renderer
from . import pdf_export


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _labeled_entry(parent, label, default, row, col=0, width=12):
    ttk.Label(parent, text=label).grid(row=row, column=col, sticky="w",
                                       padx=4, pady=3)
    var = tk.StringVar(value=str(default))
    ent = ttk.Entry(parent, textvariable=var, width=width)
    ent.grid(row=row, column=col + 1, sticky="w", padx=4, pady=3)
    return var


def _to_float(s: str, name: str) -> float:
    try:
        return float(s)
    except ValueError:
        raise ValueError(f"Invalid number for '{name}': {s!r}")


# ---------------------------------------------------------------------------
# Main GUI
# ---------------------------------------------------------------------------

class PalletStackingGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Pallet Stacking Tool")
        self.geometry("1180x780")
        self.minsize(960, 680)

        self.solutions: List[StackingSolution] = []
        self.current_solution: Optional[StackingSolution] = None
        self._build()

    # ---------------- UI layout ----------------
    def _build(self):
        outer = ttk.Frame(self, padding=8)
        outer.pack(fill="both", expand=True)

        # left input panel
        left = ttk.Frame(outer)
        left.pack(side="left", fill="y")

        right = ttk.Frame(outer)
        right.pack(side="right", fill="both", expand=True, padx=(8, 0))

        # ---- Inputs ----
        box = ttk.LabelFrame(left, text="Case (mm)", padding=6)
        box.grid(row=0, column=0, sticky="ew", pady=4)
        self.v_cl = _labeled_entry(box, "Length (L)", 400, 0)
        self.v_cw = _labeled_entry(box, "Width  (W)", 300, 1)
        self.v_ch = _labeled_entry(box, "Height (H)", 250, 2)
        self.v_cwt = _labeled_entry(box, "Weight (kg)", 8.0, 3)
        self.v_cn  = _labeled_entry(box, "Name", "SKU-001", 4, width=16)

        plt_box = ttk.LabelFrame(left, text="Pallet (mm)", padding=6)
        plt_box.grid(row=1, column=0, sticky="ew", pady=4)
        self.v_pl = _labeled_entry(plt_box, "Length", 1200, 0)
        self.v_pw = _labeled_entry(plt_box, "Width",  1000, 1)
        self.v_ph = _labeled_entry(plt_box, "Pallet H", 150, 2)
        self.v_mh = _labeled_entry(plt_box, "Max Total H", 1800, 3)

        mg = ttk.LabelFrame(left, text="Reserved Margins (mm)", padding=6)
        mg.grid(row=2, column=0, sticky="ew", pady=4)
        self.v_mf = _labeled_entry(mg, "Front", 0, 0)
        self.v_mb = _labeled_entry(mg, "Back",  0, 1)
        self.v_ml = _labeled_entry(mg, "Left",  0, 2)
        self.v_mr = _labeled_entry(mg, "Right", 0, 3)

        opts = ttk.LabelFrame(left, text="Algorithm", padding=6)
        opts.grid(row=3, column=0, sticky="ew", pady=4)
        self.v_interlock = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="Allow interlock stacking",
                        variable=self.v_interlock).grid(row=0, column=0,
                                                        columnspan=2,
                                                        sticky="w", padx=4)
        self.v_bw = _labeled_entry(opts, "Barcode weight", 0.15, 1)
        self.v_topn = _labeled_entry(opts, "Top N", 5, 2)

        btns = ttk.Frame(left)
        btns.grid(row=4, column=0, sticky="ew", pady=8)
        ttk.Button(btns, text="Calculate", command=self.on_calculate)\
            .grid(row=0, column=0, sticky="ew", padx=2, pady=2)
        ttk.Button(btns, text="Preview Layout", command=self.on_preview)\
            .grid(row=1, column=0, sticky="ew", padx=2, pady=2)
        ttk.Button(btns, text="Export PDF...", command=self.on_export_pdf)\
            .grid(row=2, column=0, sticky="ew", padx=2, pady=2)
        btns.columnconfigure(0, weight=1)

        # ---- Top-N comparison list ----
        cmp = ttk.LabelFrame(left, text="Top Solutions", padding=4)
        cmp.grid(row=5, column=0, sticky="nsew", pady=6)
        left.rowconfigure(5, weight=1)

        cols = ("rank", "cases", "layers", "total",
                "area%", "vol%", "bc%", "layout")
        self.tree = ttk.Treeview(cmp, columns=cols, show="headings", height=8)
        widths = {"rank": 36, "cases": 50, "layers": 50, "total": 60,
                  "area%": 50, "vol%": 50, "bc%": 50, "layout": 110}
        for c in cols:
            self.tree.heading(c, text=c.upper())
            self.tree.column(c, width=widths[c], anchor="center")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        # ---- Plot area ----
        self.plot_frame = ttk.Frame(right)
        self.plot_frame.pack(fill="both", expand=True)

        self.status = tk.StringVar(value="Ready.")
        ttk.Label(self, textvariable=self.status, anchor="w",
                  relief="sunken").pack(side="bottom", fill="x")

        # initial empty figure
        self._render_figure(None)

    # ---------------- Event handlers ----------------
    def _parse_inputs(self):
        case = Case(
            length=_to_float(self.v_cl.get(), "case length"),
            width =_to_float(self.v_cw.get(), "case width"),
            height=_to_float(self.v_ch.get(), "case height"),
            weight=_to_float(self.v_cwt.get(), "case weight"),
            name  =self.v_cn.get(),
        )
        pallet = Pallet(
            length=_to_float(self.v_pl.get(), "pallet length"),
            width =_to_float(self.v_pw.get(), "pallet width"),
            height=_to_float(self.v_ph.get(), "pallet height"),
            max_total_height=_to_float(self.v_mh.get(), "max total height"),
            margin_front=_to_float(self.v_mf.get(), "margin front"),
            margin_back =_to_float(self.v_mb.get(), "margin back"),
            margin_left =_to_float(self.v_ml.get(), "margin left"),
            margin_right=_to_float(self.v_mr.get(), "margin right"),
        )
        top_n = int(_to_float(self.v_topn.get(), "top n"))
        barcode_weight = _to_float(self.v_bw.get(), "barcode weight")
        return case, pallet, top_n, barcode_weight

    def on_calculate(self):
        try:
            case, pallet, top_n, bw = self._parse_inputs()
        except ValueError as e:
            messagebox.showerror("Input error", str(e))
            return
        self.status.set("Optimizing...")
        self.update_idletasks()

        try:
            sols = optimize(case, pallet,
                            top_n=top_n,
                            allow_interlock=self.v_interlock.get(),
                            barcode_weight=bw)
        except Exception as e:
            messagebox.showerror("Optimization failed", str(e))
            self.status.set("Failed.")
            return

        if not sols:
            messagebox.showwarning(
                "No solution",
                "No valid stacking solution. Check that the case fits inside "
                "the usable pallet area and below the height limit.")
            self.status.set("No solution.")
            return

        self.solutions = sols
        self.current_solution = sols[0]

        # populate tree
        self.tree.delete(*self.tree.get_children())
        for row in compare_solutions(sols):
            self.tree.insert("", "end", values=(
                row["rank"], row["cases_per_layer"], row["layers"],
                row["total_cases"], f'{row["area_util_%"]:.1f}',
                f'{row["volume_util_%"]:.1f}',
                f'{row["barcode_exposure_%"]:.1f}',
                row["layout"],
            ))
        # auto select rank 1
        first = self.tree.get_children()[0]
        self.tree.selection_set(first)
        self._render_figure(self.current_solution)

        self.status.set(f"Done. Top solution: {sols[0].total_cases} cases "
                        f"({sols[0].layout_name}).")

    def _on_tree_select(self, _evt):
        sel = self.tree.selection()
        if not sel or not self.solutions:
            return
        idx = self.tree.index(sel[0])
        if 0 <= idx < len(self.solutions):
            self.current_solution = self.solutions[idx]
            self._render_figure(self.current_solution)

    def on_preview(self):
        if self.current_solution is None:
            messagebox.showinfo("Preview", "Click 'Calculate' first.")
            return
        # spawn a new top-level window with a large overview figure
        win = tk.Toplevel(self)
        win.title(f"Preview — {self.current_solution.total_cases} cases")
        win.geometry("1100x780")
        fig = renderer.build_overview_figure(self.current_solution)
        canvas = FigureCanvasTkAgg(fig, master=win)
        canvas.draw()
        canvas.get_tk_widget().pack(fill="both", expand=True)
        NavigationToolbar2Tk(canvas, win).update()

    def on_export_pdf(self):
        if self.current_solution is None:
            messagebox.showinfo("Export", "Click 'Calculate' first.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".pdf",
            filetypes=[("PDF files", "*.pdf")],
            initialfile="pallet_report.pdf",
            title="Save Pallet Report")
        if not path:
            return
        self.status.set("Exporting PDF...")
        self.update_idletasks()
        try:
            pdf_export.export_pdf(
                path, self.current_solution,
                top_solutions=self.solutions,
                product_name=self.current_solution.case.name,
                product_code=self.current_solution.case.name,
                pallet_type="Standard",
                pallet_weight=0.0,
                load_ref=self.current_solution.layout_name,
                datafile_name=os.path.basename(path))
        except Exception as e:
            messagebox.showerror("PDF export failed", str(e))
            self.status.set("PDF export failed.")
            return
        self.status.set(f"PDF saved to {path}")
        messagebox.showinfo("Export", f"PDF saved:\n{path}")

    # ---------------- Plotting ----------------
    def _render_figure(self, solution: Optional[StackingSolution]):
        # clear plot frame
        for w in self.plot_frame.winfo_children():
            w.destroy()
        if solution is None:
            fig = plt.figure(figsize=(8, 6))
            ax = fig.add_subplot(111)
            ax.set_axis_off()
            ax.text(0.5, 0.5, "Enter dimensions and press Calculate",
                    ha="center", va="center", fontsize=14, color="gray")
        else:
            fig = renderer.build_overview_figure(solution)
        canvas = FigureCanvasTkAgg(fig, master=self.plot_frame)
        canvas.draw()
        canvas.get_tk_widget().pack(fill="both", expand=True)
        NavigationToolbar2Tk(canvas, self.plot_frame).update()


def run():
    app = PalletStackingGUI()
    app.mainloop()
