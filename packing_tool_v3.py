#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
棧板包裝資料處理工具 v3.0
- 批次新增 CC（新/舊混合）
- R 版本 Excel 生成
- 特殊棧板 CSV 生成
需要套件: pip install pandas openpyxl
"""

import math, os, sys, threading, tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
import unicodedata
import traceback
from datetime import datetime
from copy import copy
import pandas as pd
import openpyxl

# ======================== Windows DPI ========================
if sys.platform == 'win32':
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

# ======================== 固定參數 ========================
LOADING_TYPE_MAP = {'空運':1,'空運一般':1,'海運一般':2,'陸運':4,'海運散貨':6}
DEFAULT_AIR_LIMIT, DEFAULT_SEA_LIMIT = 150, 220
PALLET_HEIGHT = {'EU':14.1,'L7-G':14.1,'EPAL-EU':16.5,'EPAL-STD':18.5}
PALLET_WIDTH = {'EU':80,'L7-G':100,'EPAL-EU':80,'EPAL-STD':100}
PALLET_TYPE_TO_CODE = {'EU':'EU','Standard':'L7-G'}
IGNORED_PALLET_TYPES = {'Big box','Big Box'}
PALLET_LENGTH = 120
PRODUCT_LINE_OPTIONS = ['AIO(PT)', 'PF']
LOADING_NAME = {1:'空運',2:'海運一般',4:'陸運',6:'海運散貨'}

PRODUCT_LINE_CONFIG = {
    'AIO(PT)': {
        'sheet1': 'ASUS AiO PC Info-PT',
        'sheet2': 'Freight simulation(PT)   ',
        'rev_cell': 'V2',
        'date_cell': 'S2',
        's2_rev_cell': 'P2',
        's2_date_cell': 'N2',
        'data_start': 6,
        'file_prefix': 'PT',
    },
    'PF': {
        'sheet1': 'ASUS Commercial PC Info-PF ',
        'sheet2': 'Freight simulation(PF) ',
        'rev_cell': 'W2',
        'date_cell': 'S2',
        's2_rev_cell': 'P2',
        's2_date_cell': 'N2',
        'data_start': 6,
        'file_prefix': 'PF',
    },
}


def detect_product_line(wb):
    names = wb.sheetnames
    if 'ASUS Commercial PC Info-PF ' in names:
        return 'PF'
    if 'ASUS AiO PC Info-PT' in names:
        return 'AIO(PT)'
    return None

SHEET1_TEMPLATE = [
    ('Air',  'Under 150cm', 'Standard', 150),
    ('',     '',            'Europe',   150),
    ('Sea',  'Under 220cm', 'Standard', 220),
    ('',     '',            'Europe',   220),
    ('',     'Under 210cm', 'Standard', 210),
    ('',     '',            'Europe',   210),
    ('',     'Under 200cm', 'Standard', 200),
    ('',     '',            'Europe',   200),
    ('',     'Under 180cm', 'Standard', 180),
    ('',     '',            'Europe',   180),
    ('',     'Under 150cm', 'Standard', 150),
    ('',     '',            'Europe',   150),
]

C = {
    'bg':'#f0f2f5','white':'#ffffff','surface2':'#f8f9fb',
    'blue':'#3b6cb5','blue_light':'#e8f0fb','blue_pale':'#e8f0fb',
    'green':'#2d8a4e','green_light':'#e6f5ec','green_pale':'#e6f5ec',
    'orange':'#c47a28','orange_light':'#fef5e8','orange_pale':'#fef5e8',
    'red':'#c03838','red_light':'#fef0f0','red_pale':'#fef0f0',
    'blue_info':'#3872b8',
    'border':'#e2e6ec','border_dark':'#cdd3dc','grid':'#e2e6ec',
    'text':'#1a2332','text_dim':'#5a6780','text_label':'#5a6780','text_muted':'#94a0b4',
    'input_bg':'#ffffff','input_bd':'#cdd3dc','input_focus':'#3b6cb5',
    'btn_fg':'#ffffff',
    'btn_blue':'#3b6cb5','btn_hover':'#325ea0',
    'btn_green':'#2d8a4e','btn_green_h':'#247840',
    'btn_orange':'#c47a28','btn_orange_h':'#a86820',
    'prog_bg':'#e2e6ec','prog_fg':'#2d8a4e',
    'edited':'#fff3cd','edited_border':'#f0c040',
    'special_bg':'#fef0dc','special_border':'#f0d4a8',
}


# ======================== 工具函數 ========================

def clean_str(raw):
    if pd.isna(raw):
        return ''
    s = str(raw)
    s = ''.join(' ' if unicodedata.category(c) == 'Zs' else c for c in s)
    return s.strip()


def has_rename_marker(raw_str):
    return '=>' in raw_str or '＝＞' in raw_str


def resolve_location(raw):
    s = clean_str(raw)
    if not s:
        return None
    for sep in ['=>', '＝＞']:
        if sep in s:
            n = s.split(sep)[-1].strip()
            for p in ['更名為', '改名為', '改為', '改']:
                if n.startswith(p):
                    n = n[len(p):]
                    break
            return n.strip() or None
    return s


def calc_layer(lim, pc, bh):
    if lim is None or bh is None or bh <= 0:
        return 1
    return max(1, math.floor((lim - PALLET_HEIGHT[pc]) / bh))


def get_desktop():
    if sys.platform == 'win32':
        desktop = Path(os.environ.get('USERPROFILE', '')) / 'Desktop'
    else:
        desktop = Path.home() / 'Desktop'
    desktop.mkdir(parents=True, exist_ok=True)
    return desktop


def find_product_col(hr, pl):
    t = ['PF'] if pl == 'PF' else ['AIO(PT)', 'PT\n(AIO)']
    for i, v in enumerate(hr):
        if not pd.isna(v) and str(v).strip() in t:
            return i
    return None


def _split_loading_codes(cd):
    if '/' in cd:
        return [c.strip() for c in cd.split('/')]
    return [cd]


# ======================== R 版本讀取 ========================

def read_r_file(r_path, product_line=None):
    wb = openpyxl.load_workbook(r_path, data_only=True)
    if product_line is None:
        product_line = detect_product_line(wb)
        if product_line is None:
            wb.close()
            raise ValueError("無法辨識 R 檔案的產品線，找不到對應的 Sheet")
    cfg = PRODUCT_LINE_CONFIG[product_line]
    ws1 = wb[cfg['sheet1']]
    rev_cell = ws1[cfg['rev_cell']].value or ''
    rev_num = int(''.join(c for c in str(rev_cell) if c.isdigit()) or '0')
    date_cell = ws1[cfg['date_cell']].value or ''
    cc_info = {}
    for row in range(cfg['data_start'], ws1.max_row + 1):
        cc = ws1.cell(row=row, column=19).value
        if cc and str(cc).strip():
            cc_key = str(cc).strip()
            models = str(ws1.cell(row=row, column=2).value or '').strip()
            pcs_std = ws1.cell(row=row, column=11).value
            pcs_eu = ws1.cell(row=row + 1, column=11).value if row + 1 <= ws1.max_row else None
            cc_info[cc_key] = {
                'models': models, 'row': row,
                'pcs_layer_std': pcs_std, 'pcs_layer_eu': pcs_eu,
            }
    ws2 = wb[cfg['sheet2']]
    freight_info = {}
    for row in range(13, ws2.max_row + 1):
        model = ws2.cell(row=row, column=1).value
        ptype = ws2.cell(row=row, column=2).value
        gw = ws2.cell(row=row, column=3).value
        if model and str(ptype).strip() == 'Standard':
            freight_info[str(model).strip()] = {
                'gw': gw, 'std_row': row, 'eu_row': row + 1,
            }
    wb.close()
    return {
        'rev': rev_num, 'date': date_cell, 'product_line': product_line,
        'cc_info': cc_info, 'freight_info': freight_info,
        'last_row_s1': ws1.max_row, 'last_row_s2': ws2.max_row,
    }


# ======================== R 版本生成 ========================

def copy_cell_style(src, dst):
    dst.font = copy(src.font)
    dst.fill = copy(src.fill)
    dst.border = copy(src.border)
    dst.alignment = copy(src.alignment)
    dst.number_format = src.number_format


def generate_r_version(r_path, tasks, output_dir=None, product_line=None):
    wb = openpyxl.load_workbook(r_path)
    if product_line is None:
        product_line = detect_product_line(wb)
        if product_line is None:
            wb.close()
            raise ValueError("無法辨識 R 檔案的產品線")
    cfg = PRODUCT_LINE_CONFIG[product_line]
    s1_name = cfg['sheet1']
    ws1 = wb[s1_name]
    ws2 = wb[cfg['sheet2']]
    rev_cell = str(ws1[cfg['rev_cell']].value or 'Rev:0')
    old_rev = int(''.join(c for c in rev_cell if c.isdigit()) or '0')
    new_rev = old_rev + 1
    today = datetime.now().strftime('%Y/%-m/%-d') if sys.platform != 'win32' \
        else datetime.now().strftime('%Y/%#m/%#d')
    ws1[cfg['rev_cell']] = f'Rev:{new_rev}'
    ws1[cfg['date_cell']] = f'Date:{today}'
    ws2[cfg['s2_rev_cell']] = f'Rev:{new_rev}'
    ws2[cfg['s2_date_cell']] = f'Date:{today}'
    s1_next = ws1.max_row + 1
    s2_next = ws2.max_row + 1
    ref_row_s1 = None
    for row in range(6, ws1.max_row + 1):
        if ws1.cell(row=row, column=19).value:
            ref_row_s1 = row
    ref_row_s2 = None
    for row in range(13, ws2.max_row + 1):
        if ws2.cell(row=row, column=1).value and \
           str(ws2.cell(row=row, column=2).value).strip() == 'Standard':
            ref_row_s2 = row
    for task in tasks:
        if task['type'] == 'new':
            _add_new_cc_sheet1(ws1, task, s1_next, ref_row_s1, product_line)
            _add_new_cc_sheet2(ws2, task, s2_next, s1_next, ref_row_s2, s1_name)
            s1_next += 12
            s2_next += 2
        elif task['type'] == 'existing':
            _add_existing_cc(ws1, ws2, task, s2_next, ref_row_s2)
            s2_next += 2
    if output_dir is None:
        output_dir = get_desktop()
    out_name = f'{cfg["file_prefix"]}_Pallet_info_R{new_rev}_0_{datetime.now().strftime("%Y%m%d")}.xlsx'
    out_path = Path(output_dir) / out_name
    wb.save(str(out_path))
    wb.close()
    return str(out_path), new_rev


def _add_new_cc_sheet1(ws, task, start_row, ref_row, product_line='AIO(PT)'):
    box_h_mm = task['box_height_mm']
    pcs_std = task['pcs_layer_std']
    pcs_eu = task['pcs_layer_eu']
    pcs_carton = task['pcs_carton']
    h_formula_ph = 14.1
    for i, (air_sea, limit_text, pallet_type, limit_cm) in enumerate(SHEET1_TEMPLATE):
        r = start_row + i
        is_std = (pallet_type == 'Standard')
        pallet_h = 14.1
        w = 100 if is_std else 80
        pcs_layer = pcs_std if is_std else pcs_eu
        layers = max(1, math.floor((limit_cm - pallet_h) / task['box_height_cm']))
        if ref_row:
            ref_offset = ref_row + i
            for col in range(1, 23):
                src = ws.cell(row=ref_offset, column=col)
                dst = ws.cell(row=r, column=col)
                copy_cell_style(src, dst)
        ws.cell(row=r, column=2, value=task['models'])
        ws.cell(row=r, column=3, value=air_sea if air_sea else None)
        ws.cell(row=r, column=4, value=limit_text if limit_text else None)
        ws.cell(row=r, column=5, value=pallet_type)
        ws.cell(row=r, column=6, value=120)
        ws.cell(row=r, column=7, value=w)
        ws.cell(row=r, column=8, value=f'=($V${start_row}*L{r}/10)+{h_formula_ph}')
        ws.cell(row=r, column=9, value=f'=F{r}*G{r}*H{r}/6000')
        ws.cell(row=r, column=10, value=pcs_carton)
        ws.cell(row=r, column=11, value=pcs_layer)
        ws.cell(row=r, column=12, value=layers)
        ws.cell(row=r, column=13, value=f'=K{r}*L{r}')
        ws.cell(row=r, column=14, value=f'=CONCATENATE(K{r},"*",L{r}," (layer)")')
        ws.cell(row=r, column=15, value=f'=(V{start_row}*L{r}/10)+18.5')
        ws.cell(row=r, column=16, value=f'=IF(O{r}<150,L{r},L{r}-1)')
        ws.cell(row=r, column=17, value=f'=M{r}')
        ws.cell(row=r, column=18, value=f'=CONCATENATE(K{r},"*",P{r}," (layer)")')
        if i == 0:
            ws.cell(row=r, column=19, value=task['cc'])
            ws.cell(row=r, column=20, value=task['carton_l_mm'])
            ws.cell(row=r, column=21, value=task['carton_w_mm'])
            ws.cell(row=r, column=22, value=box_h_mm)


def _add_new_cc_sheet2(ws, task, start_row, s1_start, ref_row, s1_name):
    pcs_std = task['pcs_layer_std']
    pcs_eu = task['pcs_layer_eu']
    for i, (ptype, pcs_layer) in enumerate([('Standard', pcs_std), ('Europe', pcs_eu)]):
        r = start_row + i
        if ref_row:
            src_row = ref_row + i
            for col in range(1, 17):
                src = ws.cell(row=src_row, column=col)
                dst = ws.cell(row=r, column=col)
                copy_cell_style(src, dst)
        if i == 0:
            ws.cell(row=r, column=1, value=task['models'])
        else:
            ws.cell(row=r, column=1, value=f'=A{start_row}')
        ws.cell(row=r, column=2, value=ptype)
        ws.cell(row=r, column=3, value=task['gw'])
        ws.cell(row=r, column=4, value=1)
        ws.cell(row=r, column=5, value=pcs_layer)
        s1_row = s1_start + i
        ws.cell(row=r, column=6, value=f"='{s1_name}'!$M${s1_row}")
        sea_rows = {
            220: s1_start + 2 + i, 210: s1_start + 4 + i,
            200: s1_start + 6 + i, 180: s1_start + 8 + i,
            150: s1_start + 10 + i,
        }
        sea_formula = (
            f'=IF($G$7="Sea 220cm",\'{s1_name}\'!M${sea_rows[220]},'
            f'IF($G$7="Sea 210cm",\'{s1_name}\'!M${sea_rows[210]},'
            f'IF($G$7="Sea 200cm",\'{s1_name}\'!M${sea_rows[200]},'
            f'IF($G$7="Sea 180cm",\'{s1_name}\'!M${sea_rows[180]},'
            f'IF($G$7="Sea 150cm",\'{s1_name}\'!M${sea_rows[150]},'
            f'"N/A")))))'
        )
        ws.cell(row=r, column=7, value=sea_formula)
        ws.cell(row=r, column=8, value=f"='{s1_name}'!I${s1_row}")
        ws.cell(row=r, column=9, value=f'=H{r}')
        ws.cell(row=r, column=10, value=f'=IF(I{r}<K{r},"<",">")')
        ws.cell(row=r, column=11, value=f'=(C{r}*F{r})+30')
        ws.cell(row=r, column=12, value=f'=IF($I{r}>$K{r},($I{r}/$F{r})*$D$3,($K{r}/$F{r})*$D$3)')
        ws.cell(row=r, column=13, value=f'=$D$5/N{r}')
        ws.cell(row=r, column=14, value=f'=G{r}*{"10" if i==0 else "11"}*N$7')
        ws.cell(row=r, column=15, value=f'=$D$7/P{r}')
        ws.cell(row=r, column=16, value=f'=G{r}*{"21" if i==0 else "24"}*P$7')


def _add_existing_cc(ws1, ws2, task, s2_next, ref_row_s2):
    cc = task['cc']
    for row in range(6, ws1.max_row + 1):
        cell_cc = ws1.cell(row=row, column=19).value
        if cell_cc and str(cell_cc).strip() == cc:
            old_models = str(ws1.cell(row=row, column=2).value or '')
            new_models = f"{old_models}/{task['models']}"
            for r in range(row, min(row + 12, ws1.max_row + 1)):
                m_cell = ws1.cell(row=r, column=2)
                if m_cell.value and str(m_cell.value).strip():
                    ws1.cell(row=r, column=2, value=new_models)
            pcs_std = ws1.cell(row=row, column=11).value or 10
            pcs_eu = ws1.cell(row=row + 1, column=11).value or 8
            break
    else:
        return
    existing_gw = task.get('gw')
    if not existing_gw:
        for row in range(13, ws2.max_row + 1):
            m = ws2.cell(row=row, column=1).value
            if m and old_models.split('/')[0] in str(m):
                existing_gw = ws2.cell(row=row, column=3).value
                break
        if not existing_gw:
            existing_gw = 0
    for i, (ptype, pcs_layer) in enumerate([('Standard', pcs_std), ('Europe', pcs_eu)]):
        r = s2_next + i
        if ref_row_s2:
            src_row = ref_row_s2 + i
            for col in range(1, 17):
                src = ws2.cell(row=src_row, column=col)
                dst = ws2.cell(row=r, column=col)
                copy_cell_style(src, dst)
        if i == 0:
            ws2.cell(row=r, column=1, value=task['models'])
        else:
            ws2.cell(row=r, column=1, value=f'=A{s2_next}')
        ws2.cell(row=r, column=2, value=ptype)
        ws2.cell(row=r, column=3, value=existing_gw)
        ws2.cell(row=r, column=4, value=1)
        ws2.cell(row=r, column=5, value=pcs_layer)
        air_layers = max(1, math.floor((150 - 14.1) / task.get('box_height_cm', 50)))
        sea_layers = max(1, math.floor((220 - 14.1) / task.get('box_height_cm', 50)))
        ws2.cell(row=r, column=6, value=pcs_layer * air_layers)
        ws2.cell(row=r, column=7, value=pcs_layer * sea_layers)
        h_air = 14.1 + task.get('box_height_cm', 50) * air_layers
        vw = 120 * (100 if i == 0 else 80) * h_air / 6000
        ws2.cell(row=r, column=8, value=round(vw, 2))
        ws2.cell(row=r, column=9, value=f'=H{r}')
        ws2.cell(row=r, column=10, value=f'=IF(I{r}<K{r},"<",">")')
        ws2.cell(row=r, column=11, value=f'=(C{r}*F{r})+30')
        ws2.cell(row=r, column=12, value=f'=IF($I{r}>$K{r},($I{r}/$F{r})*$D$3,($K{r}/$F{r})*$D$3)')
        ws2.cell(row=r, column=13, value=f'=$D$5/N{r}')
        ws2.cell(row=r, column=14, value=f'=G{r}*{"10" if i==0 else "11"}*N$7')
        ws2.cell(row=r, column=15, value=f'=$D$7/P{r}')
        ws2.cell(row=r, column=16, value=f'=G{r}*{"21" if i==0 else "24"}*P$7')


# ======================== 刪除線偵測 ========================

def get_strikethrough_locations(excel_path):
    wb = openpyxl.load_workbook(excel_path, data_only=False, read_only=True)
    strike_set = set()
    for sname in ['歐規棧板', '海空運限高', '陸運', 'EPAL-Amazon', '日字-實木. EPAL#2']:
        if sname not in wb.sheetnames:
            continue
        ws = wb[sname]
        for row in ws.iter_rows(min_row=3, max_col=2):
            if len(row) < 2:
                continue
            cell = row[1]
            if cell.value and cell.font and cell.font.strikethrough:
                raw = clean_str(cell.value)
                if raw:
                    strike_set.add(raw)
    wb.close()
    return strike_set


# ======================== CSV 生成核心 ========================

def collect_layer_rules(excel_path, product_line, box_height):
    strike_raw_set = get_strikethrough_locations(excel_path)
    sheets = pd.read_excel(excel_path, sheet_name=None, header=None)
    bh = float(box_height) if box_height else None
    anomalies = []

    def check_strike(raw_str):
        return raw_str in strike_raw_set

    def process_location(raw_val, sheet_name, pf_col, row_series, extra_fields=None):
        raw_loc = clean_str(raw_val)
        loc = resolve_location(raw_val)
        if not loc:
            return None, None, True
        pf_val = str(row_series.iloc[pf_col]).strip() if pf_col is not None else ''
        if 'V' not in pf_val:
            return None, None, True
        ef = extra_fields or {}
        if check_strike(raw_loc):
            anomalies.append({
                'location': raw_loc, 'sheet': sheet_name,
                'pallet_type': ef.get('pallet_type', ''),
                'loading_code': ef.get('loading_code', ''),
                'limitation': ef.get('limitation', ''),
                'reason': '有刪除線(已停用)，已排除', 'type': 'strikethrough'})
            return None, None, True
        if has_rename_marker(raw_loc):
            anomalies.append({
                'location': raw_loc, 'sheet': sheet_name,
                'pallet_type': ef.get('pallet_type', ''),
                'loading_code': ef.get('loading_code', ''),
                'limitation': ef.get('limitation', ''),
                'reason': f'含改名標記(=>)，解析為: {loc}', 'type': 'renamed'})
        return loc, raw_loc, False

    sea_air_data = {}
    sa = sheets.get('海空運限高')
    if sa is not None:
        pf = find_product_col(sa.iloc[1].tolist(), product_line)
        if pf is not None:
            for i in range(2, len(sa)):
                r = sa.iloc[i]
                pt = clean_str(r.iloc[9])
                cd = clean_str(r.iloc[11])
                lim_raw = r.iloc[10]
                lim_str = clean_str(lim_raw) if not pd.isna(lim_raw) else ''
                loc, raw_loc, skip = process_location(
                    r.iloc[1], '海空運限高', pf, r,
                    {'pallet_type': pt, 'loading_code': cd, 'limitation': lim_str})
                if skip:
                    continue
                if pt in IGNORED_PALLET_TYPES:
                    anomalies.append({'location': loc, 'sheet': '海空運限高',
                        'pallet_type': pt, 'loading_code': cd, 'limitation': lim_str,
                        'reason': f'Pallet type={pt}，已設定忽略', 'type': 'ignored'})
                    continue
                if pt and pt not in PALLET_TYPE_TO_CODE:
                    anomalies.append({'location': loc, 'sheet': '海空運限高',
                        'pallet_type': pt, 'loading_code': cd, 'limitation': lim_str,
                        'reason': f'未知的 Pallet type="{pt}"', 'type': 'unknown'})
                if cd:
                    for sc in _split_loading_codes(cd):
                        if sc and sc not in LOADING_TYPE_MAP:
                            anomalies.append({'location': loc, 'sheet': '海空運限高',
                                'pallet_type': pt, 'loading_code': cd, 'limitation': lim_str,
                                'reason': f'未知的堆疊方式Code="{sc}"', 'type': 'unknown'})
                try:
                    lv = float(lim_raw)
                except (ValueError, TypeError):
                    lv = None
                    if lim_str:
                        anomalies.append({'location': loc, 'sheet': '海空運限高',
                            'pallet_type': pt, 'loading_code': cd, 'limitation': lim_str,
                            'reason': f'限高無法解析: "{lim_str}"', 'type': 'parse_error'})
                pc = PALLET_TYPE_TO_CODE.get(pt, 'EU')
                sea_air_data.setdefault(loc, []).append((cd, pc, lv))

    land_data, land_lim = {}, {}
    ls = sheets.get('陸運')
    if ls is not None:
        pf = find_product_col(ls.iloc[1].tolist(), product_line)
        if pf is not None:
            for i in range(2, len(ls)):
                r = ls.iloc[i]
                pt = clean_str(r.iloc[8])
                loc, raw_loc, skip = process_location(
                    r.iloc[1], '陸運', pf, r,
                    {'pallet_type': pt, 'loading_code': '陸運'})
                if skip:
                    continue
                if pt in IGNORED_PALLET_TYPES:
                    anomalies.append({'location': loc, 'sheet': '陸運',
                        'pallet_type': pt, 'loading_code': '陸運', 'limitation': '',
                        'reason': f'Pallet type={pt}，已設定忽略', 'type': 'ignored'})
                    continue
                if pt and pt not in PALLET_TYPE_TO_CODE:
                    anomalies.append({'location': loc, 'sheet': '陸運',
                        'pallet_type': pt, 'loading_code': '陸運', 'limitation': '',
                        'reason': f'未知的 Pallet type="{pt}"', 'type': 'unknown'})
                pc = PALLET_TYPE_TO_CODE.get(pt, 'EU')
                land_data.setdefault(loc, []).append(pc)
                try:
                    land_lim[loc] = float(r.iloc[9])
                except (ValueError, TypeError):
                    pass

    eu_locations = set()
    es = sheets.get('歐規棧板')
    if es is not None:
        pf = find_product_col(es.iloc[1].tolist(), product_line)
        if pf is not None:
            for i in range(2, len(es)):
                r = es.iloc[i]
                loc, raw_loc, skip = process_location(
                    r.iloc[1], '歐規棧板', pf, r, {'pallet_type': 'EU'})
                if skip:
                    continue
                eu_locations.add(loc)

    epal_eu_locs, epal_std_locs = set(), set()
    ep = sheets.get('EPAL-Amazon')
    if ep is not None:
        pf = find_product_col(ep.iloc[1].tolist(), product_line)
        if pf is not None:
            for i in range(2, len(ep)):
                r = ep.iloc[i]
                loc, raw_loc, skip = process_location(
                    r.iloc[1], 'EPAL-Amazon', pf, r, {'pallet_type': 'EPAL-EU'})
                if skip:
                    continue
                epal_eu_locs.add(loc)
    ep2 = sheets.get('日字-實木. EPAL#2')
    if ep2 is not None:
        pf = find_product_col(ep2.iloc[1].tolist(), product_line)
        if pf is not None:
            for i in range(2, len(ep2)):
                r = ep2.iloc[i]
                loc, raw_loc, skip = process_location(
                    r.iloc[1], '日字-實木 EPAL#2', pf, r, {'pallet_type': 'EPAL-STD'})
                if skip:
                    continue
                epal_std_locs.add(loc)

    rule_map = {}
    def add_rule(pc, lt, lim, source):
        actual_lim = float(lim) if lim is not None else \
            float(DEFAULT_AIR_LIMIT if lt == 1 else DEFAULT_SEA_LIMIT)
        key = (pc, lt, actual_lim)
        if key not in rule_map:
            rule_map[key] = {
                'pallet_code': pc, 'loading_type': lt,
                'loading_name': LOADING_NAME.get(lt, str(lt)),
                'limitation': actual_lim, 'is_default': (lim is None),
                'calc_layer': calc_layer(actual_lim, pc, bh), 'source': source}

    for loc in eu_locations:
        e = sea_air_data.get(loc, [])
        al = [x[2] for x in e if '空運' in x[0] and x[1] == 'EU' and x[2] is not None]
        sl = [x[2] for x in e if '海運' in x[0] and '散貨' not in x[0]
              and x[1] == 'EU' and x[2] is not None]
        if al:
            for a in al:
                add_rule('EU', 1, a, '歐規棧板')
        else:
            sea_min = min(sl) if sl else None
            if sea_min is not None and sea_min <= DEFAULT_AIR_LIMIT:
                add_rule('EU', 1, sea_min, '海空運限高(空運跟隨海運)')
            else:
                add_rule('EU', 1, None, '歐規棧板')
        for s in sl:
            add_rule('EU', 2, s, '海空運限高')
        if not sl:
            add_rule('EU', 2, None, '歐規棧板(預設)')

    for loc, entries in sea_air_data.items():
        for cd, pc, lv in entries:
            if pc == 'EU' and loc in eu_locations:
                if cd == '海運散貨':
                    add_rule('EU', 6, lv, '海空運限高')
                continue
            for sc in _split_loading_codes(cd):
                lt = LOADING_TYPE_MAP.get(sc)
                if lt is None:
                    continue
                if lt == 4 and loc not in eu_locations:
                    continue
                if pc == 'L7-G' and lt in (2, 4) and lv is not None and lv == 220:
                    continue
                add_rule(pc, lt, lv, '海空運限高')

    for loc, pcs in land_data.items():
        if loc not in eu_locations:
            continue
        for pc in pcs:
            add_rule(pc, 4, land_lim.get(loc), '陸運')

    if epal_eu_locs:
        add_rule('EPAL-EU', 1, None, 'EPAL-Amazon')
        add_rule('EPAL-EU', 2, 175, 'EPAL-Amazon')
    if epal_std_locs:
        add_rule('EPAL-STD', 1, None, '日字EPAL#2')
        add_rule('EPAL-STD', 2, 180, '日字EPAL#2')
    add_rule('EU', 1, None, 'ASTPHQ(固定)')
    add_rule('EU', 2, None, 'ASTPHQ(固定)')

    rules = sorted(rule_map.values(),
                   key=lambda x: (x['pallet_code'], x['loading_type'], x['limitation']))
    loc_data = {
        'sea_air_data': sea_air_data, 'land_data': land_data,
        'land_lim': land_lim, 'eu_locations': eu_locations,
        'epal_eu_locs': epal_eu_locs, 'epal_std_locs': epal_std_locs,
    }
    return rules, loc_data, anomalies


def generate_csv(carton_code, box_height, bl1, bl2, loc_data,
                 layer_lookup, excluded_locations=None):
    hv = float(box_height) if box_height else None
    sad = loc_data['sea_air_data']
    ld, ll = loc_data['land_data'], loc_data['land_lim']
    eu_set = loc_data['eu_locations']
    excl = excluded_locations or set()
    rows = []

    def gl(pc, lt, lim):
        key = (pc, lt, float(lim) if lim is not None else
               float(DEFAULT_AIR_LIMIT if lt == 1 else DEFAULT_SEA_LIMIT))
        if key in layer_lookup:
            return layer_lookup[key]
        if lim is not None and hv and hv > 0:
            return calc_layer(lim, pc, hv)
        return calc_layer(float(DEFAULT_AIR_LIMIT if lt == 1 else DEFAULT_SEA_LIMIT), pc, hv)

    def mr(pc, loc, lt, bl, layer):
        total_height = PALLET_HEIGHT[pc] + hv * layer if hv else None
        return {
            'Carton Code': carton_code, 'Length': PALLET_LENGTH,
            'Width': PALLET_WIDTH[pc], 'Hight': total_height,
            'Ship to locaton': loc, 'Pallet Code': pc,
            'Loading Type': lt, 'Bottom Loading': bl,
            'Bottom Layer': layer, 'Bottom Place Type': 'H',
            'Top Loading': None, 'Top Layer': None, 'Top Place Type': None,
            'Top Cover': None, 'Side Cover': None,
            'Corner Protector Weight': None,
            'MOQ': layer if pc == 'EPAL-EU' else None}

    for loc in sorted(eu_set):
        if loc in excl:
            continue
        e = sad.get(loc, [])
        al = [x[2] for x in e if '空運' in x[0] and x[1] == 'EU' and x[2] is not None]
        sl = [x[2] for x in e if '海運' in x[0] and '散貨' not in x[0]
              and x[1] == 'EU' and x[2] is not None]
        if al:
            air_lim = min(al)
        else:
            sea_min = min(sl) if sl else None
            if sea_min is not None and sea_min <= DEFAULT_AIR_LIMIT:
                air_lim = sea_min
            else:
                air_lim = None
        rows.append(mr('EU', loc, 1, bl1, gl('EU', 1, air_lim)))
        rows.append(mr('EU', loc, 2, bl1, gl('EU', 2, min(sl) if sl else None)))

    for loc in sorted(sad.keys()):
        if loc in excl:
            continue
        for cd, pc, lv in sad[loc]:
            if pc == 'EU' and loc in eu_set:
                if cd == '海運散貨':
                    rows.append(mr('EU', loc, 6, bl1, gl('EU', 6, lv)))
                continue
            bl = bl1 if pc in ('EU', 'EPAL-EU') else bl2
            for sc in _split_loading_codes(cd):
                lt = LOADING_TYPE_MAP.get(sc)
                if lt is None:
                    continue
                if lt == 4 and loc not in eu_set:
                    continue
                if pc == 'L7-G' and lt in (2, 4) and lv is not None and lv == 220:
                    continue
                rows.append(mr(pc, loc, lt, bl, gl(pc, lt, lv)))

    for loc in sorted(ld.keys()):
        if loc in excl or loc not in eu_set:
            continue
        for pc in ld[loc]:
            bl = bl1 if pc in ('EU', 'EPAL-EU') else bl2
            rows.append(mr(pc, loc, 4, bl, gl(pc, 4, ll.get(loc))))

    for loc in sorted(loc_data['epal_eu_locs']):
        if loc in excl:
            continue
        rows.append(mr('EPAL-EU', loc, 1, bl1, gl('EPAL-EU', 1, None)))
        rows.append(mr('EPAL-EU', loc, 2, bl1, gl('EPAL-EU', 2, 175)))

    for loc in sorted(loc_data['epal_std_locs']):
        if loc in excl:
            continue
        rows.append(mr('EPAL-STD', loc, 1, bl2, gl('EPAL-STD', 1, None)))
        rows.append(mr('EPAL-STD', loc, 2, bl2, gl('EPAL-STD', 2, 180)))

    if 'ASTPHQ' not in excl:
        rows.append(mr('EU', 'ASTPHQ', 1, bl1, gl('EU', 1, None)))
        rows.append(mr('EU', 'ASTPHQ', 2, bl1, gl('EU', 2, None)))

    df = pd.DataFrame(rows)
    co = ['Carton Code', 'Length', 'Width', 'Hight', 'Ship to locaton',
          'Pallet Code', 'Loading Type', 'Bottom Loading', 'Bottom Layer',
          'Bottom Place Type', 'Top Loading', 'Top Layer', 'Top Place Type',
          'Top Cover', 'Side Cover', 'Corner Protector Weight', 'MOQ']
    df = df[co].drop_duplicates(
        subset=['Ship to locaton', 'Pallet Code', 'Loading Type'], keep='first')
    df = df.sort_values(
        ['Pallet Code', 'Ship to locaton', 'Loading Type']).reset_index(drop=True)

    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f'{carton_code}_{ts}.csv'
    op = get_desktop() / filename
    df.to_csv(op, index=False, encoding='big5', lineterminator='\r\n')
    return str(op), len(df)


# ======================== GUI v3.0 批次模式 ========================

class App:
    def __init__(self, root):
        self.root = root
        self.root.title("棧板包裝資料處理工具 v3.0")
        self.root.geometry("960x900")
        self.root.minsize(960, 700)
        self.root.resizable(True, True)
        self.root.configure(bg=C['bg'])
        self.r_info = None
        self.r_path = None
        self.task_list = []
        self._running = False
        self._build()

    def _section(self, parent, title, ck='blue'):
        color, light = C[ck], C[f'{ck}_light']
        outer = tk.Frame(parent, bg=C['bg'])
        outer.pack(fill='x', padx=14, pady=(0, 6))
        hdr = tk.Frame(outer, bg=light, highlightbackground=C['border'],
                       highlightthickness=1)
        hdr.pack(fill='x')
        tk.Frame(hdr, bg=color, width=4).pack(side='left', fill='y')
        tk.Label(hdr, text=f"  {title}", font=('Microsoft JhengHei UI', 10, 'bold'),
                 bg=light, fg=color, pady=5).pack(side='left')
        bw = tk.Frame(outer, bg=C['border'])
        bw.pack(fill='both', expand=True)
        body = tk.Frame(bw, bg=C['white'])
        body.pack(fill='both', expand=True, padx=1, pady=(0, 1))
        content = tk.Frame(body, bg=C['white'], padx=10, pady=5)
        content.pack(fill='both', expand=True)
        return content

    def _field_row(self, parent, label, default='', combo=None, idx=0, width=28):
        bg = C['white'] if idx % 2 == 0 else C['surface2']
        row = tk.Frame(parent, bg=bg)
        row.pack(fill='x')
        lf = tk.Frame(row, bg=C['surface2'], width=260)
        lf.pack(side='left', fill='y')
        lf.pack_propagate(False)
        tk.Label(lf, text=label, font=('Microsoft JhengHei UI', 9),
                 bg=C['surface2'], fg=C['text_dim'],
                 anchor='e', padx=12, pady=5).pack(fill='both', expand=True)
        tk.Frame(row, bg=C['grid'], width=1).pack(side='left', fill='y')
        vf = tk.Frame(row, bg=bg, padx=8, pady=2)
        vf.pack(side='left', fill='both', expand=True)
        var = tk.StringVar(value=default)
        if combo:
            ttk.Combobox(vf, textvariable=var, values=combo,
                         state='readonly', width=width, font=('Consolas', 10)).pack(side='left', pady=2)
        else:
            tk.Entry(vf, textvariable=var, width=width, font=('Consolas', 10),
                     bg=C['input_bg'], fg=C['text'], relief='solid', bd=1,
                     highlightthickness=1, highlightcolor=C['input_focus'],
                     highlightbackground=C['input_bd']).pack(side='left', pady=2)
        return var

    def _group_label(self, parent, text):
        gf = tk.Frame(parent, bg=C['surface2'])
        gf.pack(fill='x')
        tk.Label(gf, text=f"  -- {text} --", font=('Microsoft JhengHei UI', 8),
                 bg=C['surface2'], fg=C['text_muted'], anchor='w',
                 pady=3, padx=4).pack(fill='x')
        tk.Frame(parent, bg=C['border'], height=1).pack(fill='x')

    def _ref_row(self, parent, pairs, idx=0):
        bg = C['white'] if idx % 2 == 0 else C['surface2']
        row = tk.Frame(parent, bg=bg)
        row.pack(fill='x')
        for i, (k, v) in enumerate(pairs):
            if i > 0:
                tk.Frame(row, bg=C['grid'], width=1).pack(side='left', fill='y')
            cell = tk.Frame(row, bg=bg, padx=6, pady=2)
            cell.pack(side='left', fill='both', expand=True)
            if k:
                tk.Label(cell, text=k, font=('Microsoft JhengHei UI', 7),
                         bg=bg, fg=C['text_muted']).pack(anchor='w')
            tk.Label(cell, text=v, font=('Microsoft JhengHei UI', 8, 'bold'),
                     bg=bg, fg=C['text']).pack(anchor='w')
        tk.Frame(parent, bg=C['grid'], height=1).pack(fill='x')

    def _pill(self, parent, text, ck):
        color, light = C[ck], C[f'{ck}_light']
        f = tk.Frame(parent, bg=light, highlightbackground=C['border'],
                     highlightthickness=1, padx=6, pady=1)
        f.pack(side='left', padx=2)
        tk.Label(f, text=text, font=('Microsoft JhengHei UI', 7, 'bold'),
                 bg=light, fg=color).pack()

    def _bind_mousewheel(self, canvas):
        def _on_wheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        def _on_linux_up(event):
            canvas.yview_scroll(-1, "units")
        def _on_linux_down(event):
            canvas.yview_scroll(1, "units")
        canvas.bind_all("<MouseWheel>", _on_wheel)
        canvas.bind_all("<Button-4>", _on_linux_up)
        canvas.bind_all("<Button-5>", _on_linux_down)

    def _build(self):
        top = tk.Frame(self.root, bg=C['white'], padx=12, pady=8)
        top.pack(fill='x')
        icon_frame = tk.Frame(top, bg=C['blue'], width=38, height=38)
        icon_frame.pack(side='left', padx=(0, 10))
        icon_frame.pack_propagate(False)
        tk.Label(icon_frame, text="P", font=('Microsoft JhengHei UI', 16),
                 bg=C['blue'], fg=C['btn_fg']).pack(expand=True)
        tf = tk.Frame(top, bg=C['white'])
        tf.pack(side='left')
        tk.Label(tf, text="Pallet Packing Configuration Tool",
                 font=('Microsoft JhengHei UI', 14, 'bold'),
                 bg=C['white'], fg=C['text']).pack(anchor='w')
        tk.Label(tf, text="v3.0 batch mode",
                 font=('Microsoft JhengHei UI', 8), bg=C['white'], fg=C['text_muted']).pack(anchor='w')
        flow = tk.Frame(top, bg=C['white'])
        flow.pack(side='right')
        for txt, ck in [("R file", 'blue'), ("->", ""), ("+CC", 'green'),
                        ("->", ""), ("Preview", 'orange'), ("->", ""), ("Output", 'green')]:
            if txt == "->":
                tk.Label(flow, text="->", font=('Microsoft JhengHei UI', 8),
                         bg=C['white'], fg=C['text_muted']).pack(side='left', padx=2)
            else:
                self._pill(flow, txt, ck)
        tk.Frame(self.root, bg=C['border'], height=1).pack(fill='x')

        container = tk.Frame(self.root, bg=C['bg'])
        container.pack(fill='both', expand=True)
        self.canvas = tk.Canvas(container, bg=C['bg'], highlightthickness=0)
        vsb = ttk.Scrollbar(container, orient='vertical', command=self.canvas.yview)
        self.main = tk.Frame(self.canvas, bg=C['bg'])
        self.main.bind('<Configure>',
                       lambda e: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.create_window((0, 0), window=self.main, anchor='nw', tags='main_win')
        self.canvas.configure(yscrollcommand=vsb.set)
        def on_resize(e):
            self.canvas.itemconfig('main_win', width=e.width)
        self.canvas.bind('<Configure>', on_resize)
        self._bind_mousewheel(self.canvas)
        self.canvas.pack(side='left', fill='both', expand=True)
        vsb.pack(side='right', fill='y')

        fc = self._section(self.main, "Step 0: Select R version file", 'blue')
        frow = tk.Frame(fc, bg=C['white'])
        frow.pack(fill='x', pady=2)
        tk.Label(frow, text="R file", font=('Microsoft JhengHei UI', 9),
                 bg=C['white'], fg=C['text_label']).pack(side='left')
        self.r_file_var = tk.StringVar()
        tk.Entry(frow, textvariable=self.r_file_var, width=50,
                 font=('Consolas', 9), bg=C['input_bg'], fg=C['text'],
                 relief='solid', bd=1, highlightthickness=1,
                 highlightcolor=C['input_focus'],
                 highlightbackground=C['input_bd']).pack(side='left', padx=6)
        tk.Button(frow, text="Browse", font=('Microsoft JhengHei UI', 9),
                  bg=C['btn_blue'], fg=C['btn_fg'],
                  activebackground=C['btn_hover'],
                  relief='flat', padx=10, pady=2, cursor='hand2',
                  command=self._browse_r).pack(side='left')
        self.r_info_label = tk.Label(fc, text="", font=('Microsoft JhengHei UI', 9),
                                     bg=C['white'], fg=C['text_dim'])
        self.r_info_label.pack(anchor='w', pady=(4, 0))

        ic = self._section(self.main, "Step 1: Add Carton Code", 'green')
        self._group_label(ic, "Product / Model")
        self.cc_var = self._field_row(ic, "Carton Code", '', idx=0)
        self.models_var = self._field_row(ic, "Models", '', idx=1)
        self._group_label(ic, "Box specs (new CC only)")
        self.height_var = self._field_row(ic, "Box Height (cm)", '', idx=0)
        self.carton_l_var = self._field_row(ic, "Carton L (mm)", '', idx=1)
        self.carton_w_var = self._field_row(ic, "Carton W (mm)", '', idx=0)
        self.gw_var = self._field_row(ic, "G.W. (kg)", '', idx=1)
        self.pcs_carton_var = self._field_row(ic, "Pcs/@Carton", '1', idx=0)
        self._group_label(ic, "Pallet config (new CC only)")
        self.bl_eu_var = self._field_row(ic, "Bottom Loading EU / EPAL-EU", '', idx=0)
        self.bl_l7g_var = self._field_row(ic, "Bottom Loading L7-G / EPAL-STD", '', idx=1)
        self._group_label(ic, "A file (new CC, for special pallet CSV)")
        arow = tk.Frame(ic, bg=C['white'])
        arow.pack(fill='x', pady=2)
        tk.Label(arow, text="A file", font=('Microsoft JhengHei UI', 9),
                 bg=C['white'], fg=C['text_label']).pack(side='left')
        self.a_file_var = tk.StringVar()
        tk.Entry(arow, textvariable=self.a_file_var, width=40,
                 font=('Consolas', 9), bg=C['input_bg'], fg=C['text'],
                 relief='solid', bd=1, highlightthickness=1,
                 highlightcolor=C['input_focus'],
                 highlightbackground=C['input_bd']).pack(side='left', padx=6)
        tk.Button(arow, text="Browse", font=('Microsoft JhengHei UI', 9),
                  bg=C['btn_blue'], fg=C['btn_fg'],
                  activebackground=C['btn_hover'],
                  relief='flat', padx=10, pady=2, cursor='hand2',
                  command=self._browse_a).pack(side='left')
        self.a_product_var = self._field_row(ic, "A file product line", 'PF',
                                             PRODUCT_LINE_OPTIONS, idx=1)
        bf = tk.Frame(ic, bg=C['white'])
        bf.pack(fill='x', pady=(8, 4))
        self.add_btn = tk.Button(
            bf, text="+ Add to list",
            font=('Microsoft JhengHei UI', 10, 'bold'), bg=C['btn_green'], fg=C['btn_fg'],
            activebackground=C['btn_green_h'], relief='flat', padx=20, pady=6,
            cursor='hand2', command=self._add_to_list)
        self.add_btn.pack()

        self.list_section = self._section(self.main, "Task list", 'orange')
        self.list_frame = tk.Frame(self.list_section, bg=C['white'])
        self.list_frame.pack(fill='x')
        self.list_empty_label = tk.Label(self.list_frame,
            text="No items yet. Fill in above and click Add.",
            font=('Microsoft JhengHei UI', 9), bg=C['white'], fg=C['text_muted'], pady=10)
        self.list_empty_label.pack()

        self.preview_frame = tk.Frame(self.main, bg=C['bg'])
        self.preview_frame.pack(fill='x')

        bf2 = tk.Frame(self.main, bg=C['bg'])
        bf2.pack(pady=(6, 4))
        self.gen_btn = tk.Button(
            bf2, text="Generate R+1 + CSV",
            font=('Microsoft JhengHei UI', 12, 'bold'), bg=C['btn_blue'], fg=C['btn_fg'],
            activebackground=C['btn_hover'], relief='flat', padx=28, pady=10,
            cursor='hand2', command=self._generate_all, state='disabled')
        self.gen_btn.pack()

        rc = self._section(self.main, "Reference", 'orange')
        self._ref_row(rc, [('Loading Type', 'Air:1  Sea:2  Land:4  Bulk:6')], 0)
        self._ref_row(rc, [('Pallet H(cm)',
                            'EU/L7-G:14.1  EPAL-EU:16.5  EPAL-STD:18.5')], 1)
        self._ref_row(rc, [('Default limit', 'Air:150cm  Sea:220cm'),
                           ('Formula', 'floor((limit-pallet_h)/box_h)')], 2)
        self._ref_row(rc, [('Fixed', 'ASTPHQ -> EU Air+Sea'),
                           ('EPAL-EU', 'MOQ = Bottom Layer')], 3)

        sf = tk.Frame(self.root, bg=C['surface2'], height=28)
        sf.pack(fill='x', side='bottom')
        self.status_var = tk.StringVar(value="Ready")
        tk.Label(sf, textvariable=self.status_var, font=('Microsoft JhengHei UI', 8),
                 bg=C['surface2'], fg=C['text_dim'], padx=10).pack(side='left')

    def _browse_r(self):
        fp = filedialog.askopenfilename(
            title="Select R version Excel",
            filetypes=[("Excel", "*.xlsx")])
        if not fp:
            return
        self.r_file_var.set(fp)
        try:
            self.r_info = read_r_file(fp)
            self.r_path = fp
            pl = self.r_info['product_line']
            rev = self.r_info['rev']
            cc_count = len(self.r_info['cc_info'])
            self.r_info_label.config(
                text=f"Loaded: {pl}, Rev: {rev}, {cc_count} CCs",
                fg=C['green'])
            self.status_var.set(f"Loaded R{rev} ({pl})")
        except Exception as e:
            self.r_info = None
            self.r_path = None
            self.r_info_label.config(text=f"Load failed: {e}", fg=C['red'])

    def _browse_a(self):
        fp = filedialog.askopenfilename(
            title="Select A file",
            filetypes=[("Excel", "*.xlsx")])
        if fp:
            self.a_file_var.set(fp)

    def _add_to_list(self):
        if not self.r_info:
            messagebox.showerror("Error", "Please select R version file first!")
            return
        cc = self.cc_var.get().strip()
        models = self.models_var.get().strip()
        if not cc:
            messagebox.showerror("Error", "Please enter Carton Code!")
            return
        if not models:
            messagebox.showerror("Error", "Please enter Models!")
            return
        for t in self.task_list:
            if t['cc'] == cc:
                messagebox.showerror("Error", f"{cc} already in list!")
                return
        is_new = cc not in self.r_info['cc_info']
        if is_new:
            errs = []
            try:
                bh = float(self.height_var.get().strip())
                if bh <= 0: raise ValueError
            except (ValueError, TypeError):
                errs.append("Box Height (cm)")
            try:
                cl = int(self.carton_l_var.get().strip())
            except (ValueError, TypeError):
                errs.append("Carton L (mm)")
            try:
                cw = int(self.carton_w_var.get().strip())
            except (ValueError, TypeError):
                errs.append("Carton W (mm)")
            try:
                gw = float(self.gw_var.get().strip())
            except (ValueError, TypeError):
                errs.append("G.W. (kg)")
            try:
                pcs = int(self.pcs_carton_var.get().strip())
            except (ValueError, TypeError):
                errs.append("Pcs/@Carton")
            try:
                b1 = int(self.bl_eu_var.get().strip())
            except (ValueError, TypeError):
                errs.append("Bottom Loading EU")
            try:
                b2 = int(self.bl_l7g_var.get().strip())
            except (ValueError, TypeError):
                errs.append("Bottom Loading L7-G")
            if errs:
                messagebox.showerror("Missing fields",
                                     "The following fields are missing or invalid:\n\n" +
                                     "\n".join(f"  - {e}" for e in errs))
                return
            if b1 >= b2:
                messagebox.showerror("Error",
                    f"Bottom Loading error!\n\n"
                    f"EU/EPAL-EU ({b1}) must be less than L7-G/EPAL-STD ({b2})")
                return
            if not self.a_file_var.get().strip():
                messagebox.showerror("Error", "New CC requires A file for special pallet CSV!")
                return
            task = {
                'type': 'new', 'cc': cc, 'models': models,
                'box_height_cm': bh, 'box_height_mm': int(bh * 10),
                'carton_l_mm': cl, 'carton_w_mm': cw,
                'pcs_carton': pcs, 'pcs_layer_std': b2, 'pcs_layer_eu': b1,
                'gw': gw, 'bl_eu': b1, 'bl_l7g': b2,
                'a_file': self.a_file_var.get().strip(),
                'a_product_line': self.a_product_var.get(),
            }
        else:
            existing = self.r_info['cc_info'][cc]
            task = {
                'type': 'existing', 'cc': cc, 'models': models,
                'existing_models': existing['models'],
                'box_height_cm': 50.0,
            }
        self.task_list.append(task)
        self._refresh_list()
        self.cc_var.set('')
        self.models_var.set('')
        if is_new:
            self.height_var.set('')
            self.carton_l_var.set('')
            self.carton_w_var.set('')
            self.gw_var.set('')
            self.bl_eu_var.set('')
            self.bl_l7g_var.set('')
        self.status_var.set(f"Added {cc} ({'new' if is_new else 'existing'}), {len(self.task_list)} total")

    def _refresh_list(self):
        for w in self.list_frame.winfo_children():
            w.destroy()
        if not self.task_list:
            self.list_empty_label = tk.Label(self.list_frame,
                text="No items yet.",
                font=('Microsoft JhengHei UI', 9), bg=C['white'], fg=C['text_muted'], pady=10)
            self.list_empty_label.pack()
            self.gen_btn.config(state='disabled', bg=C['border'])
            return
        hdr = tk.Frame(self.list_frame, bg=C['surface2'])
        hdr.pack(fill='x')
        tk.Frame(self.list_frame, bg=C['border'], height=2).pack(fill='x')
        for txt, w in [("#", 3), ("CC", 10), ("Type", 6), ("Models", 25), ("Del", 6)]:
            tk.Label(hdr, text=txt, font=('Microsoft JhengHei UI', 8, 'bold'),
                     bg=C['surface2'], fg=C['text_muted'], width=w, pady=4,
                     anchor='w', padx=4).pack(side='left')
        for idx, task in enumerate(self.task_list):
            bg = C['white'] if idx % 2 == 0 else C['surface2']
            is_new = task['type'] == 'new'
            row = tk.Frame(self.list_frame, bg=bg)
            row.pack(fill='x')
            tk.Label(row, text=str(idx + 1), font=('Consolas', 9),
                     bg=bg, fg=C['text_dim'], width=3, anchor='w', padx=4).pack(side='left')
            tk.Label(row, text=task['cc'], font=('Consolas', 10, 'bold'),
                     bg=bg, fg=C['text'], width=10, anchor='w').pack(side='left')
            type_bg = C['orange_light'] if is_new else C['blue_light']
            type_fg = C['orange'] if is_new else C['blue']
            type_text = "NEW" if is_new else "OLD"
            tf = tk.Frame(row, bg=type_bg, padx=4, pady=1)
            tf.pack(side='left', padx=4)
            tk.Label(tf, text=type_text, font=('Microsoft JhengHei UI', 8, 'bold'),
                     bg=type_bg, fg=type_fg).pack()
            model_text = task['models']
            if not is_new:
                model_text = f"{task['existing_models']} + {task['models']}"
            tk.Label(row, text=model_text, font=('Consolas', 9),
                     bg=bg, fg=C['text'], width=25, anchor='w').pack(side='left')
            def make_del(i):
                return lambda: self._remove_task(i)
            tk.Button(row, text="X", font=('Microsoft JhengHei UI', 8),
                      bg=bg, fg=C['red'], relief='flat', cursor='hand2',
                      command=make_del(idx), padx=4).pack(side='left')
        self.gen_btn.config(state='normal', bg=C['btn_blue'])

    def _remove_task(self, idx):
        if 0 <= idx < len(self.task_list):
            removed = self.task_list.pop(idx)
            self._refresh_list()
            self.status_var.set(f"Removed {removed['cc']}")

    def _generate_all(self):
        if not self.task_list or not self.r_path:
            return
        if self._running:
            return
        self._running = True
        self.gen_btn.config(state='disabled', bg=C['border'])
        self.status_var.set("Generating...")

        tasks_snapshot = list(self.task_list)
        r_path_snapshot = self.r_path

        def work():
            try:
                results = []
                out_r, new_rev = generate_r_version(r_path_snapshot, tasks_snapshot)
                results.append(f"R{new_rev} -> {os.path.basename(out_r)}")
                new_cc_count = 0
                for task in tasks_snapshot:
                    if task['type'] != 'new':
                        continue
                    new_cc_count += 1
                    a_file = task.get('a_file', '')
                    if not a_file:
                        results.append(f"{task['cc']} - A file not specified, skipped CSV")
                        continue
                    a_pl = task.get('a_product_line', 'PF')
                    bh = str(task['box_height_cm'])
                    bl1 = task['bl_eu']
                    bl2 = task['bl_l7g']
                    cc = task['cc']
                    try:
                        rules, loc_data, anomalies = collect_layer_rules(a_file, a_pl, bh)
                        lookup = {
                            (r['pallet_code'], r['loading_type'], r['limitation']): r['calc_layer']
                            for r in rules}
                        csv_path, csv_cnt = generate_csv(cc, bh, bl1, bl2, loc_data, lookup)
                        results.append(f"{cc} CSV -> {os.path.basename(csv_path)} ({csv_cnt} rows)")
                    except Exception as e:
                        results.append(f"{cc} CSV failed: {e}")
                msg = "Done!\n\n" + "\n".join(results) + "\n\nFiles saved to Desktop"
                self.root.after(0, lambda: messagebox.showinfo("Done", msg))
                self.root.after(0, lambda: self.status_var.set(
                    f"Done - R{new_rev} + {new_cc_count} CSVs"))

                def _post_generate():
                    self.task_list.clear()
                    self._refresh_list()
                    self.r_path = out_r
                    self.r_file_var.set(out_r)
                    try:
                        self.r_info = read_r_file(out_r)
                        pl = self.r_info['product_line']
                        self.r_info_label.config(
                            text=f"Loaded: {pl}, Rev: {new_rev}, {len(self.r_info['cc_info'])} CCs",
                            fg=C['green'])
                    except Exception:
                        pass

                self.root.after(0, _post_generate)
            except Exception as e:
                tb = traceback.format_exc()
                self.root.after(0, lambda: messagebox.showerror("Failed", f"{e}\n\n{tb}"))
                self.root.after(0, lambda: self.status_var.set("Failed"))
            finally:
                self._running = False
                self.root.after(0, lambda: self.gen_btn.config(
                    state='normal' if self.task_list else 'disabled',
                    bg=C['btn_blue'] if self.task_list else C['border']))

        threading.Thread(target=work, daemon=True).start()


def main():
    root = tk.Tk()
    import tkinter.font as tkfont
    default_font = tkfont.nametofont("TkDefaultFont")
    default_font.configure(family='Microsoft JhengHei UI', size=9)
    root.option_add("*Font", default_font)
    style = ttk.Style()
    for theme in ['clam', 'vista', 'winnative']:
        if theme in style.theme_names():
            style.theme_use(theme)
            break
    App(root)
    root.mainloop()


if __name__ == '__main__':
    main()
