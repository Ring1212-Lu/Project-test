"""Tkinter desktop GUI."""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from typing import List, Optional

import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import (
    FigureCanvasTkAgg, NavigationToolbar2Tk,
)

from ..core    import optimize, compare_solutions
from ..models  import Carton, Pallet, StackingResult
from ..render  import build_overview_figure
from ..export  import export_pdf, export_excel


# ---------------------------------------------------------------------------
# Field helpers
# ---------------------------------------------------------------------------

def _labeled_entry(parent, label, default, row, col=0, width=12):
    ttk.Label(parent, text=label).grid(row=row, column=col,
                                       sticky="w", padx=4, pady=3)
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
# Main window
# ---------------------------------------------------------------------------

class PalletStackingGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Pallet Stacking Tool")
        self.geometry("1220x800")
        self.minsize(960, 700)

        self.solutions: List[StackingResult] = []
        self.current: Optional[StackingResult] = None
        self._build()

    # ---------- layout ----------
    def _build(self):
        outer = ttk.Frame(self, padding=8); outer.pack(fill="both", expand=True)
        left  = ttk.Frame(outer);  left.pack(side="left", fill="y")
        right = ttk.Frame(outer);  right.pack(side="right", fill="both",
                                              expand=True, padx=(8, 0))

        # ---- Carton ----
        box = ttk.LabelFrame(left, text="Carton (mm / kg)", padding=6)
        box.grid(row=0, column=0, sticky="ew", pady=4)
        self.v_cl  = _labeled_entry(box, "Length (L)", 400, 0)
        self.v_cw  = _labeled_entry(box, "Width  (W)", 300, 1)
        self.v_ch  = _labeled_entry(box, "Height (H)", 250, 2)
        self.v_cwt = _labeled_entry(box, "Weight (kg)", 8.0, 3)
        self.v_cn  = _labeled_entry(box, "SKU / Name", "SKU-001", 4, width=16)

        ttk.Label(box, text="Barcode face").grid(row=5, column=0, sticky="w",
                                                 padx=4, pady=3)
        self.v_bc = tk.StringVar(value="L (side)")
        cmb = ttk.Combobox(box, textvariable=self.v_bc,
                           values=["L (side)", "W (front)", "H (top)"],
                           state="readonly", width=10)
        cmb.grid(row=5, column=1, sticky="w", padx=4, pady=3)

        # ---- Pallet ----
        plt_box = ttk.LabelFrame(left, text="Pallet (mm / kg)", padding=6)
        plt_box.grid(row=1, column=0, sticky="ew", pady=4)
        self.v_pl  = _labeled_entry(plt_box, "Length",       1200, 0)
        self.v_pw  = _labeled_entry(plt_box, "Width",        1000, 1)
        self.v_ph  = _labeled_entry(plt_box, "Pallet H",      150, 2)
        self.v_mh  = _labeled_entry(plt_box, "Max Total H",  1800, 3)
        self.v_pwt = _labeled_entry(plt_box, "Pallet Weight", 30.0, 4)

        # ---- Margins ----
        mg = ttk.LabelFrame(left, text="Reserved Margins (mm)", padding=6)
        mg.grid(row=2, column=0, sticky="ew", pady=4)
        self.v_mf = _labeled_entry(mg, "Front", 0, 0)
        self.v_mb = _labeled_entry(mg, "Back",  0, 1)
        self.v_ml = _labeled_entry(mg, "Left",  0, 2)
        self.v_mr = _labeled_entry(mg, "Right", 0, 3)

        # ---- Algorithm ----
        opts = ttk.LabelFrame(left, text="Algorithm", padding=6)
        opts.grid(row=3, column=0, sticky="ew", pady=4)
        self.v_interlock = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="Allow interlock stacking",
                        variable=self.v_interlock).grid(row=0, column=0,
                                                        columnspan=2,
                                                        sticky="w", padx=4)
        self.v_topn = _labeled_entry(opts, "Top N", 5, 1)
        self.v_bw   = _labeled_entry(opts, "Barcode weight", 100, 2)
        self.v_aw   = _labeled_entry(opts, "Area weight",    10,  3)

        # ---- Buttons ----
        btns = ttk.Frame(left); btns.grid(row=4, column=0, sticky="ew", pady=8)
        btns.columnconfigure(0, weight=1)
        ttk.Button(btns, text="Calculate",
                   command=self.on_calculate).grid(row=0, column=0,
                                                   sticky="ew", padx=2, pady=2)
        ttk.Button(btns, text="Preview Layout",
                   command=self.on_preview).grid(row=1, column=0,
                                                 sticky="ew", padx=2, pady=2)
        ttk.Button(btns, text="Export PDF...",
                   command=self.on_export_pdf).grid(row=2, column=0,
                                                    sticky="ew", padx=2, pady=2)
        ttk.Button(btns, text="Export Excel...",
                   command=self.on_export_excel).grid(row=3, column=0,
                                                      sticky="ew", padx=2, pady=2)

        # ---- Top-N table ----
        cmp = ttk.LabelFrame(left, text="Top Solutions", padding=4)
        cmp.grid(row=5, column=0, sticky="nsew", pady=6)
        left.rowconfigure(5, weight=1)
        cols = ("rank", "cases", "layers", "total",
                "area%", "vol%", "bc%", "score", "layout")
        widths = {"rank": 36, "cases": 50, "layers": 50, "total": 60,
                  "area%": 52, "vol%": 52, "bc%": 52, "score": 64, "layout": 100}
        self.tree = ttk.Treeview(cmp, columns=cols, show="headings", height=8)
        for c in cols:
            self.tree.heading(c, text=c.upper())
            self.tree.column(c, width=widths[c], anchor="center")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        # ---- Plot ----
        self.plot_frame = ttk.Frame(right)
        self.plot_frame.pack(fill="both", expand=True)

        # ---- Status bar ----
        self.status = tk.StringVar(value="Ready.")
        ttk.Label(self, textvariable=self.status, anchor="w",
                  relief="sunken").pack(side="bottom", fill="x")
        self._render(None)

    # ---------- handlers ----------
    def _parse(self):
        barcode_face = {"L (side)": "L", "W (front)": "W",
                        "H (top)":  "H"}[self.v_bc.get()]
        carton = Carton(
            length=_to_float(self.v_cl.get(), "case length"),
            width =_to_float(self.v_cw.get(), "case width"),
            height=_to_float(self.v_ch.get(), "case height"),
            weight=_to_float(self.v_cwt.get(), "case weight"),
            name  =self.v_cn.get(),
            barcode_face_axis=barcode_face,
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
            weight=_to_float(self.v_pwt.get(), "pallet weight"),
        )
        return (carton, pallet,
                int(_to_float(self.v_topn.get(), "top n")),
                _to_float(self.v_bw.get(), "barcode weight"),
                _to_float(self.v_aw.get(), "area weight"))

    def on_calculate(self):
        try:
            carton, pallet, top_n, bw, aw = self._parse()
        except ValueError as e:
            messagebox.showerror("Input error", str(e)); return
        self.status.set("Optimizing..."); self.update_idletasks()
        try:
            sols = optimize(carton, pallet, top_n=top_n,
                            allow_interlock=self.v_interlock.get(),
                            barcode_weight=bw, area_weight=aw)
        except Exception as e:
            messagebox.showerror("Optimization failed", str(e))
            self.status.set("Failed."); return
        if not sols:
            messagebox.showwarning(
                "No solution",
                "No valid stacking solution. Check that the carton fits in "
                "the usable pallet area and below the height limit.")
            self.status.set("No solution."); return

        self.solutions = sols
        self.current = sols[0]
        self.tree.delete(*self.tree.get_children())
        for row in compare_solutions(sols):
            self.tree.insert("", "end", values=(
                row["rank"], row["cases_per_layer"], row["layer_count"],
                row["total_cases"], f'{row["area_util_%"]:.1f}',
                f'{row["volume_util_%"]:.1f}',
                f'{row["barcode_exposure_%"]:.1f}',
                f'{row["score"]:.0f}', row["layout"],
            ))
        first = self.tree.get_children()[0]
        self.tree.selection_set(first)
        self._render(self.current)
        self.status.set(f"Done. Top: {sols[0].total_cases} cases "
                        f"({sols[0].layout_name}), "
                        f"score={sols[0].score:.0f}.")

    def _on_tree_select(self, _evt):
        sel = self.tree.selection()
        if not sel or not self.solutions:
            return
        idx = self.tree.index(sel[0])
        if 0 <= idx < len(self.solutions):
            self.current = self.solutions[idx]
            self._render(self.current)

    def on_preview(self):
        if self.current is None:
            messagebox.showinfo("Preview", "Click 'Calculate' first.")
            return
        win = tk.Toplevel(self)
        win.title(f"Preview — {self.current.total_cases} cartons")
        win.geometry("1180x800")
        fig = build_overview_figure(self.current)
        canvas = FigureCanvasTkAgg(fig, master=win); canvas.draw()
        canvas.get_tk_widget().pack(fill="both", expand=True)
        NavigationToolbar2Tk(canvas, win).update()

    def on_export_pdf(self):
        if self.current is None:
            messagebox.showinfo("Export", "Click 'Calculate' first.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".pdf",
            filetypes=[("PDF files", "*.pdf")],
            initialfile="pallet_report.pdf", title="Save Pallet Report")
        if not path:
            return
        self.status.set("Exporting PDF..."); self.update_idletasks()
        try:
            export_pdf(path, self.current,
                       top_solutions=self.solutions,
                       product_name=self.current.carton.name,
                       product_code=self.current.carton.name,
                       pallet_type="Standard",
                       pallet_weight=self.current.pallet.weight,
                       load_ref=self.current.layout_name,
                       datafile_name=os.path.basename(path))
        except Exception as e:
            messagebox.showerror("PDF export failed", str(e))
            self.status.set("PDF export failed."); return
        self.status.set(f"PDF saved to {path}")
        messagebox.showinfo("Export", f"PDF saved:\n{path}")

    def on_export_excel(self):
        if self.current is None:
            messagebox.showinfo("Export", "Click 'Calculate' first.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".xlsx",
            filetypes=[("Excel files", "*.xlsx")],
            initialfile="pallet_summary.xlsx", title="Save Excel Summary")
        if not path:
            return
        self.status.set("Exporting Excel..."); self.update_idletasks()
        try:
            export_excel(path, self.current,
                         top_solutions=self.solutions,
                         product_name=self.current.carton.name,
                         product_code=self.current.carton.name,
                         pallet_type="Standard",
                         pallet_weight=self.current.pallet.weight)
        except Exception as e:
            messagebox.showerror("Excel export failed", str(e))
            self.status.set("Excel export failed."); return
        self.status.set(f"Excel saved to {path}")
        messagebox.showinfo("Export", f"Excel saved:\n{path}")

    # ---------- plotting ----------
    def _render(self, result: Optional[StackingResult]):
        for w in self.plot_frame.winfo_children():
            w.destroy()
        if result is None:
            fig = plt.figure(figsize=(8, 6))
            ax = fig.add_subplot(111); ax.set_axis_off()
            ax.text(0.5, 0.5, "Enter dimensions and press Calculate",
                    ha="center", va="center", fontsize=14, color="gray")
        else:
            fig = build_overview_figure(result)
        canvas = FigureCanvasTkAgg(fig, master=self.plot_frame); canvas.draw()
        canvas.get_tk_widget().pack(fill="both", expand=True)
        NavigationToolbar2Tk(canvas, self.plot_frame).update()


def run():
    app = PalletStackingGUI()
    app.mainloop()
