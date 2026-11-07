"""Export the DCF as a live Excel model (formulas, not pasted values).

Inputs are in blue font and every calculated cell is an Excel formula, following
the usual banking convention, so the downloaded workbook can be audited and flexed.
"""

from __future__ import annotations

from io import BytesIO

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .model import DCFAssumptions

BLUE = Font(color="0000FF")
BOLD = Font(bold=True)
TITLE = Font(bold=True, size=14)
HEADER_FILL = PatternFill("solid", fgColor="1F3864")
HEADER_FONT = Font(bold=True, color="FFFFFF")
TOP_BORDER = Border(top=Side(style="thin"))
MONEY = '#,##0.0,,;(#,##0.0,,)'   # shown in millions
PCT = "0.0%"
MULT = '0.0"x"'
PRICE = '#,##0.00'

# Assumption cells (column B)
CELLS = {
    "base_revenue": 4, "tax_rate": 5, "da_pct_revenue": 6, "capex_pct_revenue": 7,
    "nwc_pct_change_revenue": 8, "wacc": 9, "terminal_growth": 10, "exit_multiple": 11,
    "mid_year": 12, "terminal_method": 13, "total_debt": 14, "cash": 15, "shares_outstanding": 16,
}
LABELS = {
    "base_revenue": ("Base-year revenue ($mm)", MONEY),
    "tax_rate": ("Tax rate", PCT),
    "da_pct_revenue": ("D&A % of revenue", PCT),
    "capex_pct_revenue": ("CapEx % of revenue", PCT),
    "nwc_pct_change_revenue": ("ΔNWC % of Δrevenue", PCT),
    "wacc": ("WACC", PCT),
    "terminal_growth": ("Terminal growth (Gordon)", PCT),
    "exit_multiple": ("Exit EV/EBITDA multiple", MULT),
    "mid_year": ("Mid-year convention (1 = yes)", "0"),
    "terminal_method": ('Terminal method ("gordon" / "exit_multiple")', "@"),
    "total_debt": ("Total debt ($mm)", MONEY),
    "cash": ("Cash & equivalents ($mm)", MONEY),
    "shares_outstanding": ("Shares outstanding (mm)", MONEY),
}

PROJ_HEADER_ROW = 19
ROWS = {
    "growth": 20, "margin": 21, "revenue": 22, "ebit": 23, "taxes": 24, "nopat": 25,
    "da": 26, "ebitda": 27, "capex": 28, "nwc": 29, "fcf": 30, "period": 31,
    "df": 32, "pv": 33,
}
VAL_ROW = 36  # first row of the valuation summary


def _b(key: str) -> str:
    return f"$B${CELLS[key]}"


def build_workbook(a: DCFAssumptions, company: str, historicals: pd.DataFrame | None = None) -> Workbook:
    wb = Workbook()
    ws = wb.active
    ws.title = "DCF"
    ws.column_dimensions["A"].width = 44
    ws["A1"] = f"{company} — Discounted Cash Flow Model"
    ws["A1"].font = TITLE
    ws["A2"] = "Blue = hard-coded input, black = formula. $ figures in millions except per share."
    ws["A2"].font = Font(italic=True, color="666666")

    # ---- assumptions ------------------------------------------------------
    ws["A3"] = "Assumptions"
    ws["A3"].font = BOLD
    values = {
        "base_revenue": a.base_revenue, "tax_rate": a.tax_rate, "da_pct_revenue": a.da_pct_revenue,
        "capex_pct_revenue": a.capex_pct_revenue, "nwc_pct_change_revenue": a.nwc_pct_change_revenue,
        "wacc": a.wacc, "terminal_growth": a.terminal_growth, "exit_multiple": a.exit_multiple,
        "mid_year": 1 if a.mid_year else 0, "terminal_method": a.terminal_method,
        "total_debt": a.total_debt, "cash": a.cash, "shares_outstanding": a.shares_outstanding,
    }
    for key, row in CELLS.items():
        label, fmt = LABELS[key]
        ws.cell(row=row, column=1, value=label)
        c = ws.cell(row=row, column=2, value=values[key])
        c.font, c.number_format = BLUE, fmt

    # ---- projections ------------------------------------------------------
    n = a.years
    first_col, last_col = 3, 2 + n
    last = get_column_letter(last_col)
    hdr = ws.cell(row=PROJ_HEADER_ROW, column=1, value="Projections")
    hdr.font, hdr.fill = HEADER_FONT, HEADER_FILL
    ws.cell(row=PROJ_HEADER_ROW, column=2).fill = HEADER_FILL

    growth = [a.growth_start + (a.growth_end - a.growth_start) * i / max(n - 1, 1) for i in range(n)]
    margin = [a.margin_start + (a.margin_end - a.margin_start) * i / max(n - 1, 1) for i in range(n)]

    labels = {
        "growth": "Revenue growth", "margin": "EBIT margin", "revenue": "Revenue", "ebit": "EBIT",
        "taxes": "(–) Taxes", "nopat": "NOPAT", "da": "(+) D&A", "ebitda": "EBITDA (memo)",
        "capex": "(–) CapEx", "nwc": "(–) Change in NWC", "fcf": "Unlevered free cash flow",
        "period": "Discount period", "df": "Discount factor", "pv": "PV of FCF",
    }
    for key, row in ROWS.items():
        ws.cell(row=row, column=1, value=labels[key])
    for key in ("revenue", "fcf", "pv"):
        ws.cell(row=ROWS[key], column=1).font = BOLD

    for i in range(n):
        col = first_col + i
        L = get_column_letter(col)
        prev_rev = _b("base_revenue") if i == 0 else f"{get_column_letter(col - 1)}{ROWS['revenue']}"
        r = {k: f"{L}{v}" for k, v in ROWS.items()}

        h = ws.cell(row=PROJ_HEADER_ROW, column=col, value=f"Year {i + 1}")
        h.font, h.fill, h.alignment = HEADER_FONT, HEADER_FILL, Alignment(horizontal="right")
        ws.column_dimensions[L].width = 13

        cells = {
            "growth": (growth[i], PCT),
            "margin": (margin[i], PCT),
            "revenue": (f"={prev_rev}*(1+{r['growth']})", MONEY),
            "ebit": (f"={r['revenue']}*{r['margin']}", MONEY),
            "taxes": (f"={r['ebit']}*{_b('tax_rate')}", MONEY),
            "nopat": (f"={r['ebit']}-{r['taxes']}", MONEY),
            "da": (f"={r['revenue']}*{_b('da_pct_revenue')}", MONEY),
            "ebitda": (f"={r['ebit']}+{r['da']}", MONEY),
            "capex": (f"={r['revenue']}*{_b('capex_pct_revenue')}", MONEY),
            "nwc": (f"=({r['revenue']}-{prev_rev})*{_b('nwc_pct_change_revenue')}", MONEY),
            "fcf": (f"={r['nopat']}+{r['da']}-{r['capex']}-{r['nwc']}", MONEY),
            "period": (f"={i + 1}-0.5*{_b('mid_year')}", "0.0"),
            "df": (f"=1/(1+{_b('wacc')})^{r['period']}", "0.000"),
            "pv": (f"={r['fcf']}*{r['df']}", MONEY),
        }
        for key, (val, fmt) in cells.items():
            c = ws.cell(row=ROWS[key], column=col, value=val)
            c.number_format = fmt
            if key in ("growth", "margin"):
                c.font = BLUE
            if key in ("fcf", "pv"):
                c.font, c.border = BOLD, TOP_BORDER

    # ---- valuation summary -----------------------------------------------
    fcf_n, ebitda_n = f"{last}{ROWS['fcf']}", f"{last}{ROWS['ebitda']}"
    v = VAL_ROW
    hdr = ws.cell(row=v - 1, column=1, value="Valuation")
    hdr.font, hdr.fill = HEADER_FONT, HEADER_FILL
    ws.cell(row=v - 1, column=2).fill = HEADER_FILL
    summary = [
        ("Sum of PV of FCF", f"=SUM(C{ROWS['pv']}:{last}{ROWS['pv']})", MONEY),
        ("Terminal value",
         f'=IF({_b("terminal_method")}="gordon",'
         f'{fcf_n}*(1+{_b("terminal_growth")})/({_b("wacc")}-{_b("terminal_growth")}),'
         f'{ebitda_n}*{_b("exit_multiple")})', MONEY),
        ("PV of terminal value", f"=B{v + 1}/(1+{_b('wacc')})^{n}", MONEY),
        ("Enterprise value", f"=B{v}+B{v + 2}", MONEY),
        ("(–) Net debt", f"={_b('total_debt')}-{_b('cash')}", MONEY),
        ("Equity value", f"=B{v + 3}-B{v + 4}", MONEY),
        ("Implied share price", f"=B{v + 5}/{_b('shares_outstanding')}", PRICE),
        ("Terminal value % of EV", f"=B{v + 2}/B{v + 3}", PCT),
        ("Implied exit EV/EBITDA", f"=B{v + 1}/{ebitda_n}", MULT),
    ]
    for i, (label, formula, fmt) in enumerate(summary):
        ws.cell(row=v + i, column=1, value=label)
        c = ws.cell(row=v + i, column=2, value=formula)
        c.number_format = fmt
        if label in ("Enterprise value", "Equity value", "Implied share price"):
            ws.cell(row=v + i, column=1).font = BOLD
            c.font = BOLD
    ws.column_dimensions["B"].width = 16
    ws.freeze_panes = "B4"

    # ---- historicals ------------------------------------------------------
    if historicals is not None and not historicals.empty:
        hs = wb.create_sheet("Historicals")
        hs.column_dimensions["A"].width = 22
        table = historicals.T
        hs.cell(row=1, column=1, value="Fiscal year").font = BOLD
        for j, year in enumerate(table.columns, start=2):
            hs.cell(row=1, column=j, value=int(year)).font = BOLD
            hs.column_dimensions[get_column_letter(j)].width = 14
        for i, (metric, row) in enumerate(table.iterrows(), start=2):
            hs.cell(row=i, column=1, value=metric)
            is_pct = "%" in metric or metric == "Tax Rate"
            for j, val in enumerate(row, start=2):
                c = hs.cell(row=i, column=j, value=None if pd.isna(val) else float(val))
                c.number_format = PCT if is_pct else MONEY
    return wb


def to_excel_bytes(a: DCFAssumptions, company: str, historicals: pd.DataFrame | None = None) -> bytes:
    buf = BytesIO()
    build_workbook(a, company, historicals).save(buf)
    return buf.getvalue()
