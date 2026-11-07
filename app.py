"""DCF Valuation Tool — Streamlit front end.

Run with:  streamlit run app.py
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dcf.data import DEFAULT_ERP, CompanyData, fetch_company, fetch_risk_free_rate, suggested_defaults
from dcf.excel import to_excel_bytes
from dcf.model import (
    DEFAULT_SCENARIOS,
    DCFAssumptions,
    Scenario,
    WACCInputs,
    apply_scenario,
    run_dcf,
    sensitivity_table,
)

st.set_page_config(page_title="DCF Valuation Tool", page_icon="📈", layout="wide")

GREEN, RED, NAVY, GREY = "#2E7D32", "#C62828", "#1F3864", "#9E9E9E"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
@st.cache_data(ttl=3600, show_spinner=False)
def load_company(ticker: str) -> CompanyData:
    return fetch_company(ticker)


@st.cache_data(ttl=3600, show_spinner=False)
def load_risk_free() -> float:
    return fetch_risk_free_rate()


def money(x: float, ccy: str = "USD") -> str:
    sym = "$" if ccy == "USD" else f"{ccy} "
    if pd.isna(x):
        return "–"
    sign = "-" if x < 0 else ""
    x = abs(x)
    for div, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if x >= div:
            return f"{sign}{sym}{x / div:,.1f}{suffix}"
    return f"{sign}{sym}{x:,.0f}"


def pct_input(label: str, key: str, default: float, step: float = 0.5, **kw) -> float:
    """Number input shown in percent, returned as a decimal."""
    return st.number_input(label, value=round(default * 100, 2), step=step, format="%.2f", key=key, **kw) / 100


# --------------------------------------------------------------------------- #
# Sidebar — company & assumptions
# --------------------------------------------------------------------------- #
st.sidebar.title("📈 DCF Valuation")
ticker = st.sidebar.text_input("Ticker", value="AAPL", help="Any Yahoo Finance ticker, e.g. MSFT, KO, NVDA").strip().upper()
if not ticker:
    st.info("Enter a ticker in the sidebar to begin.")
    st.stop()

try:
    with st.spinner(f"Loading financials for {ticker}…"):
        company = load_company(ticker)
except Exception as exc:  # network errors, delisted tickers, missing data
    st.error(f"Couldn't load **{ticker}**: {exc}")
    st.stop()

d = suggested_defaults(company)
rf_default = load_risk_free()
k = lambda name: f"{ticker}:{name}"  # widget keys are per-ticker so defaults reset on change

if st.sidebar.button("↺ Reset assumptions to defaults", width="stretch"):
    for key in [s for s in st.session_state if str(s).startswith(f"{ticker}:")]:
        del st.session_state[key]
    st.rerun()

with st.sidebar.expander("Revenue & margins", expanded=True):
    years = st.slider("Projection years", 3, 10, 5, key=k("years"))
    growth_start = pct_input("Growth yr 1 %", k("g1"), d["growth_start"])
    growth_end = pct_input("Growth final yr %", k("g2"), d["growth_end"])
    margin_start = pct_input("EBIT margin yr 1 %", k("m1"), d["margin_start"])
    margin_end = pct_input("EBIT margin final yr %", k("m2"), d["margin_end"])

with st.sidebar.expander("Cash flow drivers"):
    tax_rate = pct_input("Tax rate %", k("tax"), d["tax_rate"])
    da_pct = pct_input("D&A % of revenue", k("da"), d["da_pct_revenue"], step=0.25)
    capex_pct = pct_input("CapEx % of revenue", k("capex"), d["capex_pct_revenue"], step=0.25)
    nwc_pct = pct_input("ΔNWC % of Δrevenue", k("nwc"), d["nwc_pct_change_revenue"], step=1.0)

with st.sidebar.expander("Discount rate (WACC)", expanded=True):
    rf = pct_input("Risk-free rate % (10Y UST)", k("rf"), rf_default, step=0.1)
    beta = st.number_input("Levered beta", value=round(company.beta, 2), step=0.05, key=k("beta"))
    erp = pct_input("Equity risk premium %", k("erp"), DEFAULT_ERP, step=0.25)
    kd = pct_input("Pre-tax cost of debt %", k("kd"), d["pre_tax_cost_of_debt"], step=0.25)
    wacc_inputs = WACCInputs(rf, beta, erp, kd, tax_rate, company.market_cap, company.total_debt)
    override = st.checkbox("Override WACC", key=k("override"))
    if override:
        wacc = pct_input("WACC %", k("wacc"), wacc_inputs.wacc, step=0.25)
    else:
        wacc = wacc_inputs.wacc
        st.caption(f"Calculated WACC: **{wacc:.2%}**")

with st.sidebar.expander("Terminal value", expanded=True):
    method_label = st.radio("Method", ["Perpetuity growth", "Exit multiple"], horizontal=True, key=k("method"))
    method = "gordon" if method_label == "Perpetuity growth" else "exit_multiple"
    terminal_growth = pct_input("Terminal growth %", k("tg"), 0.025, step=0.25, disabled=method != "gordon")
    exit_multiple = st.number_input("Exit EV/EBITDA (x)", value=12.0, step=0.5, key=k("mult"),
                                    disabled=method != "exit_multiple")
    mid_year = st.checkbox("Mid-year convention", value=True, key=k("mid"))

assumptions = DCFAssumptions(
    base_revenue=float(company.latest["Revenue"]),
    years=years,
    growth_start=growth_start, growth_end=growth_end,
    margin_start=margin_start, margin_end=margin_end,
    tax_rate=tax_rate, da_pct_revenue=da_pct, capex_pct_revenue=capex_pct, nwc_pct_change_revenue=nwc_pct,
    wacc=wacc, terminal_method=method, terminal_growth=terminal_growth, exit_multiple=exit_multiple,
    mid_year=mid_year, total_debt=company.total_debt, cash=company.cash,
    shares_outstanding=company.shares_outstanding,
)

try:
    result = run_dcf(assumptions)
except ValueError as exc:
    st.error(str(exc))
    st.stop()

st.sidebar.download_button(
    "⬇ Download Excel model",
    data=to_excel_bytes(assumptions, company.name, company.historicals),
    file_name=f"{ticker}_DCF.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    width="stretch",
)

# --------------------------------------------------------------------------- #
# Header & headline metrics
# --------------------------------------------------------------------------- #
ccy = company.currency
upside = result.implied_share_price / company.price - 1
st.title(f"{company.name} ({company.ticker})")
st.caption(
    f"Market cap {money(company.market_cap, ccy)} · Net debt {money(result.net_debt, ccy)} · "
    f"Shares {company.shares_outstanding / 1e9:,.2f}B · Last FY {company.historicals.index[-1]}"
)

m = st.columns(5)
m[0].metric("Implied share price", f"{result.implied_share_price:,.2f}", f"{upside:+.1%} vs market")
m[1].metric("Current price", f"{company.price:,.2f}")
m[2].metric("Enterprise value", money(result.enterprise_value, ccy))
m[3].metric("WACC", f"{wacc:.2%}")
if method == "gordon":
    m[4].metric("Implied exit multiple", f"{result.implied_exit_multiple:.1f}x")
else:
    m[4].metric("Implied perpetual growth", f"{result.implied_terminal_growth:.2%}")

verdict = "UNDERVALUED" if upside > 0.15 else "OVERVALUED" if upside < -0.15 else "FAIRLY VALUED"
color = GREEN if upside > 0.15 else RED if upside < -0.15 else GREY
st.markdown(
    f"<div style='padding:.6rem 1rem;border-left:5px solid {color};background:rgba(127,127,127,.08);"
    f"border-radius:4px'>On these assumptions the stock looks <b style='color:{color}'>{verdict}</b> "
    f"({upside:+.1%}). Terminal value is <b>{result.tv_share_of_ev:.0%}</b> of enterprise value.</div>",
    unsafe_allow_html=True,
)
if result.tv_share_of_ev > 0.80:
    st.warning("Terminal value is over 80% of EV, so the valuation depends heavily on long-term assumptions.")
if method == "gordon" and wacc - terminal_growth < 0.03:
    st.warning("WACC is within 3 points of terminal growth, so the result is very sensitive to both.")

tab_model, tab_sens, tab_scen, tab_wacc, tab_method = st.tabs(
    ["📊 DCF model", "🔥 Sensitivity", "🎯 Scenarios & football field", "🏦 WACC", "📘 Methodology"]
)

# --------------------------------------------------------------------------- #
# Tab 1 — model
# --------------------------------------------------------------------------- #
with tab_model:
    hist = company.historicals
    proj = result.projections
    last_fy = int(hist.index[-1])
    hist_cols = {f"{y}A": hist.loc[y] for y in hist.index}
    proj_cols = {f"{last_fy + i + 1}E": proj.iloc[i] for i in range(len(proj))}

    rows = ["Revenue", "Growth %", "EBIT", "EBIT Margin %", "D&A", "CapEx", "Change in NWC",
            "Unlevered FCF", "Discount Factor", "PV of FCF"]
    table = pd.DataFrame({c: s.reindex(rows) for c, s in {**hist_cols, **proj_cols}.items()})

    def fmt_cell(row: str, v: float) -> str:
        if pd.isna(v):
            return ""
        if "%" in row:
            return f"{v:.1%}"
        if row == "Discount Factor":
            return f"{v:.3f}"
        return f"{v / 1e9:,.1f}"

    shown = pd.DataFrame({c: [fmt_cell(r, table.loc[r, c]) for r in rows] for c in table.columns}, index=rows)
    st.subheader("Historical (A) and projected (E) financials")
    st.caption(f"{ccy} billions except percentages. Historical FCF is computed with the same formula as the projections.")
    st.dataframe(shown, width="stretch")

    c1, c2 = st.columns(2)
    with c1:
        fig = go.Figure()
        fig.add_bar(x=list(hist_cols), y=hist["Revenue"] / 1e9, name="Revenue (A)", marker_color=GREY)
        fig.add_bar(x=list(proj_cols), y=proj["Revenue"] / 1e9, name="Revenue (E)", marker_color=NAVY)
        fig.add_scatter(x=list(hist_cols) + list(proj_cols),
                        y=list(hist["Unlevered FCF"] / 1e9) + list(proj["Unlevered FCF"] / 1e9),
                        name="Unlevered FCF", mode="lines+markers", line=dict(color=GREEN, width=3))
        fig.update_layout(title="Revenue and free cash flow", yaxis_title=f"{ccy} bn", height=380,
                          legend=dict(orientation="h", y=-0.2), margin=dict(t=50, b=10))
        st.plotly_chart(fig, width="stretch")
    with c2:
        fig = go.Figure(go.Waterfall(
            orientation="v",
            measure=["relative", "relative", "total", "relative", "total"],
            x=["PV of FCF", "PV of terminal value", "Enterprise value", "(–) Net debt", "Equity value"],
            y=np.array([result.pv_fcf_sum, result.pv_terminal_value, 0, -result.net_debt, 0]) / 1e9,
            text=[money(v, ccy) for v in (result.pv_fcf_sum, result.pv_terminal_value,
                                          result.enterprise_value, -result.net_debt, result.equity_value)],
            textposition="outside",
            increasing=dict(marker_color=GREEN), decreasing=dict(marker_color=RED),
            totals=dict(marker_color=NAVY),
        ))
        fig.update_layout(title="Enterprise value to equity value bridge", yaxis_title=f"{ccy} bn",
                          height=380, margin=dict(t=50, b=10), showlegend=False)
        st.plotly_chart(fig, width="stretch")

# --------------------------------------------------------------------------- #
# Tab 2 — sensitivity
# --------------------------------------------------------------------------- #
with tab_sens:
    st.subheader("Implied share price sensitivity")
    wacc_axis = [round(wacc + x, 4) for x in (-0.02, -0.01, -0.005, 0, 0.005, 0.01, 0.02)]
    if method == "gordon":
        second_axis = [round(terminal_growth + x, 4) for x in (-0.01, -0.005, 0, 0.005, 0.01)]
        col_fmt = lambda v: f"{v:.2%}"
        col_title = "Terminal growth"
    else:
        second_axis = [exit_multiple + x for x in (-4, -2, 0, 2, 4)]
        col_fmt = lambda v: f"{v:.1f}x"
        col_title = "Exit EV/EBITDA"
    sens = sensitivity_table(assumptions, wacc_axis, second_axis)

    fig = go.Figure(go.Heatmap(
        z=sens.values,
        x=[col_fmt(v) for v in sens.columns],
        y=[f"{v:.2%}" for v in sens.index],
        text=[[("n/a" if pd.isna(v) else f"{v:,.0f}") for v in row] for row in sens.values],
        texttemplate="%{text}", textfont=dict(size=14),
        colorscale="RdYlGn", zmid=company.price,
        colorbar=dict(title="Price"),
        hovertemplate=f"WACC %{{y}}<br>{col_title} %{{x}}<br>Price %{{z:,.2f}}<extra></extra>",
    ))
    fig.update_layout(xaxis_title=col_title, yaxis_title="WACC", height=460, margin=dict(t=20),
                      xaxis=dict(type="category", side="top"),
                      yaxis=dict(type="category", autorange="reversed"))
    st.plotly_chart(fig, width="stretch")
    st.caption(f"Colours are centred on the current price ({company.price:,.2f}): green is above market, red is below.")

# --------------------------------------------------------------------------- #
# Tab 3 — scenarios & football field
# --------------------------------------------------------------------------- #
with tab_scen:
    st.subheader("Bear / base / bull scenarios")
    st.caption("Shifts are applied on top of the sidebar (base) assumptions. Edit the table to change them.")
    term_label = "Terminal growth shift" if method == "gordon" else "Exit multiple shift (x)"
    base_df = pd.DataFrame([
        {"Scenario": s.name, "Growth shift": s.growth_shift * 100, "Margin shift": s.margin_shift * 100,
         "WACC shift": s.wacc_shift * 100,
         term_label: s.terminal_shift * 100}  # pp of growth, or turns of multiple
        for s in DEFAULT_SCENARIOS
    ])
    edited = st.data_editor(
        base_df, hide_index=True, width="stretch", disabled=["Scenario"], key=k(f"scen_{method}"),
        column_config={c: st.column_config.NumberColumn(c, format="%.2f", help="Percentage points")
                       for c in base_df.columns if c != "Scenario"},
    )

    scen_rows = []
    for _, r in edited.iterrows():
        s = Scenario(r["Scenario"], r["Growth shift"] / 100, r["Margin shift"] / 100, r["WACC shift"] / 100,
                     r[term_label] / 100)
        try:
            res = run_dcf(apply_scenario(assumptions, s))
            scen_rows.append({"Scenario": s.name, "Implied price": res.implied_share_price,
                              "Upside": res.implied_share_price / company.price - 1,
                              "Enterprise value": money(res.enterprise_value, ccy)})
        except ValueError as exc:
            scen_rows.append({"Scenario": s.name, "Implied price": np.nan, "Upside": np.nan,
                              "Enterprise value": str(exc)})
    scen_df = pd.DataFrame(scen_rows)
    st.dataframe(
        scen_df.style.format({"Implied price": "{:,.2f}", "Upside": "{:+.1%}"}, na_rep="–"),
        hide_index=True, width="stretch",
    )

    # Football field — the standard banking summary of valuation ranges
    st.subheader("Football field")
    ranges: list[tuple[str, float, float]] = []
    info = company.info
    if info.get("fiftyTwoWeekLow") and info.get("fiftyTwoWeekHigh"):
        ranges.append(("52-week trading range", info["fiftyTwoWeekLow"], info["fiftyTwoWeekHigh"]))
    if info.get("targetLowPrice") and info.get("targetHighPrice"):
        ranges.append(("Analyst price targets", info["targetLowPrice"], info["targetHighPrice"]))
    inner = sens.iloc[2:5, 1:4].values.flatten()  # ±0.5% WACC around the base case
    inner = inner[~np.isnan(inner)]
    if inner.size:
        ranges.append(("DCF: sensitivity (±0.5% WACC)", inner.min(), inner.max()))
    prices = scen_df["Implied price"].dropna()
    if len(prices) >= 2:
        ranges.append(("DCF: bear to bull scenarios", prices.min(), prices.max()))

    if ranges:
        fig = go.Figure()
        for label, lo, hi in ranges:
            fig.add_bar(y=[label], x=[hi - lo], base=[lo], orientation="h", marker_color=NAVY,
                        text=f"{lo:,.0f} – {hi:,.0f}", textposition="inside", insidetextanchor="middle",
                        textfont=dict(color="white"), hovertemplate=f"{label}<br>{lo:,.2f} – {hi:,.2f}<extra></extra>")
        fig.add_vline(x=company.price, line_dash="dash", line_color=RED,
                      annotation_text=f"Current {company.price:,.2f}", annotation_position="top")
        fig.add_vline(x=result.implied_share_price, line_dash="dot", line_color=GREEN,
                      annotation_text=f"DCF base {result.implied_share_price:,.2f}", annotation_position="bottom")
        fig.update_layout(showlegend=False, height=120 + 70 * len(ranges), xaxis_title=f"Share price ({ccy})",
                          margin=dict(t=40, b=40), yaxis=dict(autorange="reversed"))
        st.plotly_chart(fig, width="stretch")

# --------------------------------------------------------------------------- #
# Tab 4 — WACC
# --------------------------------------------------------------------------- #
with tab_wacc:
    st.subheader("Weighted average cost of capital")
    w = wacc_inputs
    c1, c2 = st.columns(2)
    c1.markdown(
        f"""
| Cost of equity (CAPM) | |
|---|---:|
| Risk-free rate | {w.risk_free:.2%} |
| × Levered beta | {w.beta:.2f} |
| × Equity risk premium | {w.equity_risk_premium:.2%} |
| **Cost of equity** | **{w.cost_of_equity:.2%}** |
"""
    )
    c2.markdown(
        f"""
| Cost of debt and weights | |
|---|---:|
| Pre-tax cost of debt | {w.pre_tax_cost_of_debt:.2%} |
| After-tax cost of debt | {w.after_tax_cost_of_debt:.2%} |
| Equity weight (E / (D+E)) | {w.weight_equity:.1%} |
| Debt weight (D / (D+E)) | {w.weight_debt:.1%} |
"""
    )
    st.markdown(f"### WACC = {w.wacc:.2%}" + ("  *(overridden in the sidebar)*" if override else ""))
    st.caption("Weights use market value of equity and book value of debt. Beta comes from Yahoo Finance; "
               "the risk-free rate is the latest 10-year US Treasury yield (^TNX).")

# --------------------------------------------------------------------------- #
# Tab 5 — methodology
# --------------------------------------------------------------------------- #
with tab_method:
    st.markdown(
        r"""
### How the model works

**1. Unlevered free cash flow**, projected for each year:

$$\text{FCF} = \text{EBIT}\,(1-t) + \text{D\&A} - \text{CapEx} - \Delta\text{NWC}$$

Revenue growth and EBIT margin move in a straight line from year 1 to the final year. D&A, CapEx and
ΔNWC are tied to revenue.

**2. Discount rate:** WACC, with the cost of equity from CAPM, $R_e = R_f + \beta \cdot ERP$.

$$\text{WACC} = \tfrac{E}{D+E} R_e + \tfrac{D}{D+E} R_d (1-t)$$

**3. Terminal value**, calculated one of two ways:
- *Perpetuity growth (Gordon):* $TV = \dfrac{FCF_N (1+g)}{WACC - g}$
- *Exit multiple:* $TV = EBITDA_N \times \text{multiple}$

The model always shows the figure implied by the other method, as a cross-check.

**4. Discounting:** each year's FCF is discounted at $(1+WACC)^{t}$. With the mid-year convention
$t$ is moved back half a year, because cash comes in during the year rather than all at year-end.
Terminal value is discounted from the end of year $N$.

**5. Equity bridge:** Equity value = Enterprise value − (Debt − Cash). Dividing by shares
outstanding gives the implied share price.

### Data and limitations
- Financial statements and market data come from Yahoo Finance via `yfinance` and may have gaps or errors.
- The defaults are based on history (revenue CAGR, 3-year average margin and reinvestment rates). They are
  starting points, not forecasts.
- Banks, insurers and REITs are usually valued on other bases, such as dividend discount or P/B, so an
  unlevered DCF fits them poorly.
- This is an educational tool, not investment advice.
"""
    )
