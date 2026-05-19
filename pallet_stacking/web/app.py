"""Streamlit + Anthropic-style web UI for the pallet stacking tool.

Launch with:
    streamlit run pallet_stacking/web/app.py

Theme: warm cream background, charcoal text, coral / orange accent —
modelled after the Anthropic / Claude product family.
"""
from __future__ import annotations

import io
import os
import sys
import tempfile

# Make the package importable when running as a script
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import streamlit as st

from pallet_stacking import PALLET_PRESETS, SHIPPING_PRESETS, FACE_COLORS
from pallet_stacking.core   import optimize, compare_solutions
from pallet_stacking.models import Carton, Pallet
from pallet_stacking.render import (
    draw_top_view, draw_pallet_3d, draw_single_carton_3d,
)
from pallet_stacking.export import export_pdf, export_excel


# ---------------------------------------------------------------------------
# Page config + theme overrides
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Pallet Stacking Tool",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Anthropic-style colour palette
CSS = """
<style>
:root {
    --bg-cream:    #FAF9F5;
    --bg-sidebar:  #F0EEE6;
    --bg-card:     #FFFFFF;
    --text-main:   #1F1E1D;
    --text-muted:  #6B6864;
    --accent:      #D97757;
    --accent-soft: #F4D2C1;
    --border:      #E8E4DA;
}
.stApp {
    background-color: var(--bg-cream);
    color: var(--text-main);
    font-family: "Inter", "Segoe UI", "Helvetica Neue", system-ui, sans-serif;
}
section[data-testid="stSidebar"] {
    background-color: var(--bg-sidebar);
    border-right: 1px solid var(--border);
}
h1, h2, h3 { color: var(--text-main); font-weight: 600; letter-spacing: -0.01em; }
.stMarkdown p { color: var(--text-main); }
.muted { color: var(--text-muted); font-size: 0.9rem; }

/* Cards */
.card {
    background-color: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 18px 22px;
    box-shadow: 0 1px 2px rgba(31,30,29,0.04);
}

/* Primary buttons */
.stButton button[kind="primary"] {
    background-color: var(--accent);
    color: white;
    border-radius: 999px;
    border: none;
    padding: 0.55rem 1.25rem;
    font-weight: 600;
}
.stButton button[kind="primary"]:hover { background-color: #C36441; }
.stButton button[kind="secondary"] {
    background-color: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 999px;
    color: var(--text-main);
}

/* Inputs */
input[type="number"], input[type="text"] {
    background: var(--bg-card) !important;
    border-radius: 10px !important;
}
div[data-baseweb="select"] {
    border-radius: 10px !important;
}

/* Metric tiles */
[data-testid="stMetric"] {
    background-color: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 14px 18px;
}
[data-testid="stMetricLabel"] { color: var(--text-muted); font-size: 0.85rem; }
[data-testid="stMetricValue"] { color: var(--text-main); font-weight: 700; }

/* Dataframe */
.stDataFrame { border-radius: 12px; overflow: hidden;
               border: 1px solid var(--border); }

/* Top accent rule */
.accent-rule { height: 3px; background: var(--accent); border-radius: 2px;
               width: 38px; margin: 6px 0 18px 0; }

hr { border-color: var(--border); }
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
st.markdown("# 📦 Pallet Stacking Tool")
st.markdown('<div class="accent-rule"></div>', unsafe_allow_html=True)
st.markdown(
    '<div class="muted">Cape Pack–style stacking optimiser · '
    'Top-5 ranked by case count → barcode exposure → area</div>',
    unsafe_allow_html=True,
)
st.write("")


# ---------------------------------------------------------------------------
# Sidebar inputs
# ---------------------------------------------------------------------------

with st.sidebar:
    st.markdown("### Carton")
    cl  = st.number_input("Length L (mm)", value=378.0, min_value=1.0, step=10.0)
    cw  = st.number_input("Width  W (mm)", value=198.0, min_value=1.0, step=10.0)
    ch  = st.number_input("Height H (mm)", value=400.0, min_value=1.0, step=10.0)
    cwt = st.number_input("Weight (kg)",   value=1.5,   min_value=0.0, step=0.1)
    cn  = st.text_input  ("SKU / Name",    value="Pallet Group")
    bc_label = st.selectbox("Barcode face", ["L (side)", "W (front)", "H (top)"],
                            index=0)
    bc_axis  = {"L (side)": "L", "W (front)": "W", "H (top)": "H"}[bc_label]

    st.markdown("### Pallet")
    preset = st.selectbox("Preset", list(PALLET_PRESETS.keys()))
    preset_dims = PALLET_PRESETS[preset]
    default_l = preset_dims[0] if preset_dims else 1200
    default_w = preset_dims[1] if preset_dims else 1000
    pl = st.number_input("Length (mm)", value=float(default_l), step=10.0)
    pw = st.number_input("Width (mm)",  value=float(default_w), step=10.0)
    ph = st.number_input("Pallet H (mm)", value=120.0, step=5.0)
    pwt = st.number_input("Pallet Weight (kg)", value=30.0, step=1.0)

    shipping = st.selectbox("Shipping mode", list(SHIPPING_PRESETS.keys()))
    sh_max = SHIPPING_PRESETS[shipping]
    default_mh = float(sh_max) if sh_max else 2200.0
    mh = st.number_input("Max Total H (mm)", value=default_mh, step=10.0)

    st.markdown("### Safety distances")
    c1, c2 = st.columns(2)
    with c1:
        mf = st.number_input("Edge: Front", value=0.0, step=5.0)
        ml = st.number_input("Edge: Left",  value=0.0, step=5.0)
    with c2:
        mb = st.number_input("Edge: Back",  value=0.0, step=5.0)
        mr = st.number_input("Edge: Right", value=0.0, step=5.0)
    gap = st.number_input("Carton gap (between cartons)",
                          value=0.0, min_value=0.0, step=1.0)

    st.markdown("### Algorithm")
    allow_interlock = st.checkbox("Allow interlock stacking", value=True)
    top_n = st.number_input("Top N", value=5, min_value=1, max_value=20, step=1)
    bcw   = st.number_input("Barcode weight", value=100.0, step=10.0)
    aw    = st.number_input("Area weight",    value=10.0,  step=5.0)

    st.write("")
    calc = st.button("⚡  Calculate", type="primary", use_container_width=True)


# ---------------------------------------------------------------------------
# Build models
# ---------------------------------------------------------------------------

def build_models():
    carton = Carton(length=cl, width=cw, height=ch, weight=cwt,
                    name=cn, barcode_face_axis=bc_axis)
    pallet = Pallet(length=pl, width=pw, height=ph,
                    max_total_height=mh,
                    margin_front=mf, margin_back=mb,
                    margin_left=ml, margin_right=mr,
                    carton_gap=gap, weight=pwt)
    return carton, pallet


# ---------------------------------------------------------------------------
# Run optimisation (cache on every input combination)
# ---------------------------------------------------------------------------

@st.cache_data(show_spinner=False)
def run_opt(cl, cw, ch, cwt, cn, bc_axis, pl, pw, ph, mh,
            mf, mb, ml, mr, gap, pwt, allow_interlock, top_n, bcw, aw):
    carton = Carton(length=cl, width=cw, height=ch, weight=cwt,
                    name=cn, barcode_face_axis=bc_axis)
    pallet = Pallet(length=pl, width=pw, height=ph,
                    max_total_height=mh,
                    margin_front=mf, margin_back=mb,
                    margin_left=ml, margin_right=mr,
                    carton_gap=gap, weight=pwt)
    sols = optimize(carton, pallet, top_n=int(top_n),
                    allow_interlock=allow_interlock,
                    barcode_weight=bcw, area_weight=aw)
    return carton, pallet, sols


if "calc_done" not in st.session_state:
    st.session_state.calc_done = False
if calc:
    st.session_state.calc_done = True

if not st.session_state.calc_done:
    st.info("Enter dimensions in the sidebar and press **Calculate** to "
            "generate optimal stacking solutions.")
    st.stop()

carton, pallet, sols = run_opt(cl, cw, ch, cwt, cn, bc_axis,
                                pl, pw, ph, mh, mf, mb, ml, mr, gap, pwt,
                                allow_interlock, top_n, bcw, aw)

if not sols:
    st.error("No valid stacking solution. Check that the carton fits inside "
             "the usable pallet area and below the height limit.")
    st.stop()


# ---------------------------------------------------------------------------
# Top-N solution selector + KPI tiles
# ---------------------------------------------------------------------------

st.markdown("### Top solutions")
rows = compare_solutions(sols)
labels = [f"#{r['rank']}  {r['layout']}  ·  {r['total_cases']} cartons  ·  "
          f"bc {r['barcode_exposure_%']:.0f}%"
          for r in rows]
sel_idx = st.radio("Pick a solution to inspect", labels, horizontal=True,
                   label_visibility="collapsed")
chosen = sols[labels.index(sel_idx)]

st.write("")

k1, k2, k3, k4, k5 = st.columns(5)
with k1: st.metric("Cases per layer",   chosen.cases_per_layer)
with k2: st.metric("Layers",            chosen.layer_count)
with k3: st.metric("Total cases",       chosen.total_cases)
with k4: st.metric("Area utilization",  f"{chosen.area_utilization*100:.1f} %")
with k5: st.metric("Barcode exposure",  f"{chosen.barcode_exposure*100:.1f} %")


# ---------------------------------------------------------------------------
# 4-panel preview
# ---------------------------------------------------------------------------

st.markdown("### Layout preview")

def _fig_top():
    fig, ax = plt.subplots(figsize=(5, 4), facecolor="white")
    draw_top_view(ax, chosen)
    return fig

def _fig_3d():
    fig = plt.figure(figsize=(5, 4), facecolor="white")
    ax = fig.add_subplot(111, projection="3d")
    draw_pallet_3d(ax, chosen)
    return fig

def _fig_carton():
    fig = plt.figure(figsize=(5, 4), facecolor="white")
    ax = fig.add_subplot(111, projection="3d")
    draw_single_carton_3d(ax, carton.length, carton.width, carton.height,
                          title=f"Carton {carton.length:.0f}×"
                                f"{carton.width:.0f}×{carton.height:.0f}")
    return fig

col1, col2, col3 = st.columns(3)
with col1: st.pyplot(_fig_carton(), clear_figure=True)
with col2: st.pyplot(_fig_top(),    clear_figure=True)
with col3: st.pyplot(_fig_3d(),     clear_figure=True)


# ---------------------------------------------------------------------------
# Comparison table
# ---------------------------------------------------------------------------

st.markdown("### Top-N comparison")
import pandas as pd
df = pd.DataFrame(rows)
df = df[["rank", "layout", "cases_per_layer", "layer_count", "total_cases",
         "area_util_%", "volume_util_%", "barcode_exposure_%",
         "interlock", "score"]]
df.columns = ["Rank", "Layout", "Cases/Layer", "Layers", "Total",
              "Area %", "Volume %", "Barcode %", "Interlock", "Score"]
st.dataframe(df, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Export buttons
# ---------------------------------------------------------------------------

st.markdown("### Export")
c1, c2 = st.columns(2)
with c1:
    if st.button("📄 Generate PDF report", use_container_width=True):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            export_pdf(f.name, chosen, top_solutions=sols,
                       product_name=carton.name, product_code=carton.name,
                       pallet_type=preset, pallet_weight=pwt,
                       load_ref=chosen.layout_name)
            with open(f.name, "rb") as r:
                st.download_button("Download PDF", r.read(),
                                   file_name="pallet_report.pdf",
                                   mime="application/pdf",
                                   use_container_width=True)
with c2:
    if st.button("📊 Generate Excel summary", use_container_width=True):
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            export_excel(f.name, chosen, top_solutions=sols,
                         product_name=carton.name, product_code=carton.name,
                         pallet_type=preset, pallet_weight=pwt)
            with open(f.name, "rb") as r:
                st.download_button("Download Excel", r.read(),
                                   file_name="pallet_summary.xlsx",
                                   mime="application/vnd.openxmlformats-"
                                        "officedocument.spreadsheetml.sheet",
                                   use_container_width=True)
