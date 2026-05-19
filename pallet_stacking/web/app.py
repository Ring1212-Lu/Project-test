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
from pallet_stacking.core   import (
    optimize, compare_solutions, verify_layer, area_upper_bound,
)
from pallet_stacking.models import Carton, Pallet
from pallet_stacking.render import (
    draw_top_view, draw_pallet_3d, draw_single_carton_3d,
)
from pallet_stacking.export import export_pdf, export_excel


# ---------------------------------------------------------------------------
# Page config + theme overrides
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="棧板堆疊工具",
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
    font-family: "Inter", "Segoe UI", "PingFang TC", "Microsoft JhengHei",
                 "Noto Sans TC", "Helvetica Neue", system-ui, sans-serif;
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
st.markdown("# 📦 棧板堆疊工具")
st.markdown('<div class="accent-rule"></div>', unsafe_allow_html=True)
st.markdown(
    '<div class="muted">Cape Pack 風格堆疊優化器 · '
    'Top-5 依「總箱數 → 條碼朝外 → 面積利用率」排序</div>',
    unsafe_allow_html=True,
)
st.write("")


# ---------------------------------------------------------------------------
# Sidebar inputs
# ---------------------------------------------------------------------------

with st.sidebar:
    st.markdown("### 外箱")
    cl  = st.number_input("長 L (mm)",  value=378.0, min_value=1.0, step=10.0)
    cw  = st.number_input("寬 W (mm)",  value=198.0, min_value=1.0, step=10.0)
    ch  = st.number_input("高 H (mm)",  value=400.0, min_value=1.0, step=10.0)
    cwt = st.number_input("重量 (kg)",  value=1.5,   min_value=0.0, step=0.1)
    cn  = st.text_input  ("品名 / SKU", value="棧板群組")
    bc_label = st.selectbox("條碼面",
                            ["L (側面)", "W (正面)", "H (頂面)"], index=0)
    bc_axis  = {"L (側面)": "L", "W (正面)": "W", "H (頂面)": "H"}[bc_label]

    st.markdown("### 棧板")
    preset = st.selectbox("棧板規格", list(PALLET_PRESETS.keys()))
    preset_dims = PALLET_PRESETS[preset]
    default_l = preset_dims[0] if preset_dims else 1200
    default_w = preset_dims[1] if preset_dims else 1000
    pl  = st.number_input("長 (mm)",         value=float(default_l), step=10.0)
    pw  = st.number_input("寬 (mm)",         value=float(default_w), step=10.0)
    ph  = st.number_input("棧板高 (mm)",     value=120.0, step=5.0)
    pwt = st.number_input("棧板重量 (kg)",   value=30.0,  step=1.0)

    shipping = st.selectbox("運輸方式", list(SHIPPING_PRESETS.keys()))
    sh_max = SHIPPING_PRESETS[shipping]
    default_mh = float(sh_max) if sh_max else 2200.0
    mh = st.number_input("最大總高 (mm)", value=default_mh, step=10.0)

    st.markdown("### 安全距離")
    c1, c2 = st.columns(2)
    with c1:
        mf = st.number_input("邊距:前", value=0.0, step=5.0)
        ml = st.number_input("邊距:左", value=0.0, step=5.0)
    with c2:
        mb = st.number_input("邊距:後", value=0.0, step=5.0)
        mr = st.number_input("邊距:右", value=0.0, step=5.0)
    gap = st.number_input("箱間距 (mm)", value=0.0, min_value=0.0, step=1.0)

    st.markdown("### 演算法")
    allow_interlock = st.checkbox("允許交錯堆疊 (interlock)", value=True)
    top_n = st.number_input("顯示前幾名 (Top N)",
                            value=5, min_value=1, max_value=20, step=1)
    bcw   = st.number_input("條碼權重", value=100.0, step=10.0)
    aw    = st.number_input("面積權重", value=10.0,  step=5.0)

    st.write("")
    calc = st.button("⚡  計算", type="primary", use_container_width=True)


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
    st.info("請於左側輸入尺寸，並按下 **計算** 以產生最佳堆疊方案。")
    st.stop()

carton, pallet, sols = run_opt(cl, cw, ch, cwt, cn, bc_axis,
                                pl, pw, ph, mh, mf, mb, ml, mr, gap, pwt,
                                allow_interlock, top_n, bcw, aw)

if not sols:
    st.error("找不到可行的堆疊方案。請確認外箱可放入棧板可用區域且不超過最大總高。")
    st.stop()


# ---------------------------------------------------------------------------
# Top-N solution selector + KPI tiles
# ---------------------------------------------------------------------------

st.markdown("### Top-N 方案")
rows = compare_solutions(sols)
labels = [f"#{r['rank']}  {r['layout']}  ·  共 {r['total_cases']} 箱  ·  "
          f"條碼 {r['barcode_exposure_%']:.0f}%"
          for r in rows]
sel_idx = st.radio("選擇方案", labels, horizontal=True,
                   label_visibility="collapsed")
chosen = sols[labels.index(sel_idx)]

st.write("")

k1, k2, k3, k4, k5 = st.columns(5)
with k1: st.metric("每層箱數",     chosen.cases_per_layer)
with k2: st.metric("層數",         chosen.layer_count)
with k3: st.metric("總箱數",       chosen.total_cases)
with k4: st.metric("面積利用率",   f"{chosen.area_utilization*100:.1f} %")
with k5: st.metric("條碼朝外比例", f"{chosen.barcode_exposure*100:.1f} %")


# ---------------------------------------------------------------------------
# 4-panel preview
# ---------------------------------------------------------------------------

st.markdown("### 排版預覽")

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
                          title=f"外箱 {carton.length:.0f}×"
                                f"{carton.width:.0f}×{carton.height:.0f}")
    return fig

col1, col2, col3 = st.columns(3)
with col1: st.pyplot(_fig_carton(), clear_figure=True)
with col2: st.pyplot(_fig_top(),    clear_figure=True)
with col3: st.pyplot(_fig_3d(),     clear_figure=True)


# ---------------------------------------------------------------------------
# Comparison table
# ---------------------------------------------------------------------------

st.markdown("### Top-N 比較")
import pandas as pd
df = pd.DataFrame(rows)
df = df[["rank", "layout", "cases_per_layer", "layer_count", "total_cases",
         "area_util_%", "volume_util_%", "barcode_exposure_%",
         "interlock", "score"]]
df.columns = ["排名", "排版", "每層箱數", "層數", "總箱數",
              "面積 %", "體積 %", "條碼 %", "交錯", "分數"]
st.dataframe(df, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Optimality verification (CP-SAT)
# ---------------------------------------------------------------------------

st.markdown("### 驗證最佳解")
st.caption("以獨立的約束求解器 (CP-SAT) 檢驗**目前選中**方案的每層箱數是否為"
           "理論最大。UNSAT 即證明已最佳，SAT 則代表演算法漏算。")
v1, v2, v3 = st.columns([1, 1, 2])
with v1:
    st.metric("目前選中", f"{chosen.cases_per_layer} 箱/層")
with v2:
    st.metric("面積上限",
              f"{area_upper_bound((chosen.case_dx, chosen.case_dy), pallet)} "
              f"箱/層")
with v3:
    budget = st.number_input("求解時間預算 (秒)", value=60.0,
                             min_value=5.0, max_value=600.0, step=5.0)
    run_verify = st.button("🔍 證明最佳 (CP-SAT)",
                           use_container_width=True)

if run_verify:
    with st.spinner("CP-SAT 求解中 …"):
        vr = verify_layer(chosen, pallet, time_limit=float(budget))
    if vr.optimal is True:
        st.success(
            f"**已證明最佳。** 在此棧板上找不到 {vr.optimizer_count + 1} "
            f"個 {chosen.case_dx:.0f} × {chosen.case_dy:.0f} 外箱的擺放方式。"
            f"狀態：`{vr.status}`（耗時 {vr.wall_s:.1f} 秒）。"
        )
    elif vr.optimal is False:
        st.error(
            f"**未達最佳！** 求解器找到可放 {vr.exact_count} 箱/層的方案 — "
            f"演算法漏掉 {vr.exact_count - vr.optimizer_count} 箱。"
            f"狀態：`{vr.status}`。"
        )
    else:
        st.warning(
            f"**未能在時間內判定。** 求解器嘗試 {vr.optimizer_count + 1} 箱"
            f"時超時。演算法的 {vr.optimizer_count} 箱可行,但是否為全域最大未知 — "
            f"請加大時間預算重試。狀態:`{vr.status}`。"
        )


# ---------------------------------------------------------------------------
# Export buttons
# ---------------------------------------------------------------------------

st.markdown("### 匯出")
c1, c2 = st.columns(2)
with c1:
    if st.button("📄 產生 PDF 報告", use_container_width=True):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            export_pdf(f.name, chosen, top_solutions=sols,
                       product_name=carton.name, product_code=carton.name,
                       pallet_type=preset, pallet_weight=pwt,
                       load_ref=chosen.layout_name)
            with open(f.name, "rb") as r:
                st.download_button("下載 PDF", r.read(),
                                   file_name="pallet_report.pdf",
                                   mime="application/pdf",
                                   use_container_width=True)
with c2:
    if st.button("📊 產生 Excel 摘要", use_container_width=True):
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            export_excel(f.name, chosen, top_solutions=sols,
                         product_name=carton.name, product_code=carton.name,
                         pallet_type=preset, pallet_weight=pwt)
            with open(f.name, "rb") as r:
                st.download_button("下載 Excel", r.read(),
                                   file_name="pallet_summary.xlsx",
                                   mime="application/vnd.openxmlformats-"
                                        "officedocument.spreadsheetml.sheet",
                                   use_container_width=True)
