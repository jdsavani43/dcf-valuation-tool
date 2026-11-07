"""Core discounted cash flow (DCF) engine.

Pure functions and dataclasses with no Streamlit or network dependencies, so the
valuation logic can be unit-tested and reused on its own.

Free cash flow to the firm (unlevered FCF):
    FCF = EBIT * (1 - tax) + D&A - CapEx - change in NWC
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

import numpy as np
import pandas as pd

TerminalMethod = Literal["gordon", "exit_multiple"]


# --------------------------------------------------------------------------- #
# Cost of capital
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class WACCInputs:
    risk_free: float          # e.g. 0.042
    beta: float               # levered equity beta
    equity_risk_premium: float  # e.g. 0.055
    pre_tax_cost_of_debt: float
    tax_rate: float
    market_cap: float         # market value of equity
    total_debt: float

    @property
    def cost_of_equity(self) -> float:
        """CAPM: Re = Rf + beta * ERP."""
        return self.risk_free + self.beta * self.equity_risk_premium

    @property
    def after_tax_cost_of_debt(self) -> float:
        return self.pre_tax_cost_of_debt * (1 - self.tax_rate)

    @property
    def weight_equity(self) -> float:
        total = self.market_cap + self.total_debt
        return 1.0 if total <= 0 else self.market_cap / total

    @property
    def weight_debt(self) -> float:
        return 1.0 - self.weight_equity

    @property
    def wacc(self) -> float:
        return (self.weight_equity * self.cost_of_equity
                + self.weight_debt * self.after_tax_cost_of_debt)


# --------------------------------------------------------------------------- #
# Operating assumptions
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class DCFAssumptions:
    base_revenue: float           # last fiscal year revenue
    years: int = 5
    growth_start: float = 0.08    # revenue growth in year 1
    growth_end: float = 0.04      # revenue growth in final projected year
    margin_start: float = 0.20    # EBIT margin in year 1
    margin_end: float = 0.22      # EBIT margin in final projected year
    tax_rate: float = 0.21
    da_pct_revenue: float = 0.03
    capex_pct_revenue: float = 0.04
    nwc_pct_change_revenue: float = 0.05  # ΔNWC as % of Δrevenue
    wacc: float = 0.09
    terminal_method: TerminalMethod = "gordon"
    terminal_growth: float = 0.025
    exit_multiple: float = 12.0   # EV / EBITDA
    mid_year: bool = True
    # bridge from enterprise value to equity value
    total_debt: float = 0.0
    cash: float = 0.0
    shares_outstanding: float = 1.0

    def with_(self, **changes) -> "DCFAssumptions":
        return replace(self, **changes)


@dataclass(frozen=True)
class DCFResult:
    projections: pd.DataFrame
    pv_fcf_sum: float
    terminal_value: float
    pv_terminal_value: float
    enterprise_value: float
    net_debt: float
    equity_value: float
    implied_share_price: float
    implied_exit_multiple: float
    implied_terminal_growth: float

    @property
    def tv_share_of_ev(self) -> float:
        return self.pv_terminal_value / self.enterprise_value if self.enterprise_value else float("nan")


def _ramp(start: float, end: float, n: int) -> np.ndarray:
    """Linear path from start (year 1) to end (year n)."""
    if n == 1:
        return np.array([start])
    return np.linspace(start, end, n)


def project(a: DCFAssumptions) -> pd.DataFrame:
    """Build the year-by-year projection table."""
    if a.years < 1:
        raise ValueError("years must be >= 1")

    growth = _ramp(a.growth_start, a.growth_end, a.years)
    margin = _ramp(a.margin_start, a.margin_end, a.years)

    revenue = a.base_revenue * np.cumprod(1 + growth)
    prev_revenue = np.concatenate([[a.base_revenue], revenue[:-1]])

    ebit = revenue * margin
    taxes = ebit * a.tax_rate
    nopat = ebit - taxes
    da = revenue * a.da_pct_revenue
    capex = revenue * a.capex_pct_revenue
    delta_nwc = (revenue - prev_revenue) * a.nwc_pct_change_revenue
    fcf = nopat + da - capex - delta_nwc

    periods = np.arange(1, a.years + 1, dtype=float)
    discount_periods = periods - 0.5 if a.mid_year else periods
    discount_factor = 1 / (1 + a.wacc) ** discount_periods

    return pd.DataFrame(
        {
            "Revenue": revenue,
            "Growth %": growth,
            "EBIT": ebit,
            "EBIT Margin %": margin,
            "Taxes": taxes,
            "NOPAT": nopat,
            "D&A": da,
            "EBITDA": ebit + da,
            "CapEx": capex,
            "Change in NWC": delta_nwc,
            "Unlevered FCF": fcf,
            "Discount Period": discount_periods,
            "Discount Factor": discount_factor,
            "PV of FCF": fcf * discount_factor,
        },
        index=pd.Index([f"Year {int(p)}" for p in periods], name="Period"),
    )


def run_dcf(a: DCFAssumptions) -> DCFResult:
    """Run the full DCF and bridge to an implied share price."""
    df = project(a)
    final_fcf = float(df["Unlevered FCF"].iloc[-1])
    final_ebitda = float(df["EBITDA"].iloc[-1])

    if a.terminal_method == "gordon":
        if a.wacc <= a.terminal_growth:
            raise ValueError("WACC must be greater than terminal growth for the Gordon growth model.")
        tv = final_fcf * (1 + a.terminal_growth) / (a.wacc - a.terminal_growth)
    elif a.terminal_method == "exit_multiple":
        tv = final_ebitda * a.exit_multiple
    else:
        raise ValueError(f"Unknown terminal method: {a.terminal_method}")

    # Terminal value is the value at the end of the final year.
    pv_tv = tv / (1 + a.wacc) ** a.years
    pv_fcf_sum = float(df["PV of FCF"].sum())
    ev = pv_fcf_sum + pv_tv
    net_debt = a.total_debt - a.cash
    equity = ev - net_debt
    price = equity / a.shares_outstanding if a.shares_outstanding else float("nan")

    implied_multiple = tv / final_ebitda if final_ebitda else float("nan")
    # Solve TV = FCF * (1+g) / (WACC - g) for g
    implied_g = (tv * a.wacc - final_fcf) / (tv + final_fcf) if (tv + final_fcf) else float("nan")

    return DCFResult(
        projections=df,
        pv_fcf_sum=pv_fcf_sum,
        terminal_value=tv,
        pv_terminal_value=pv_tv,
        enterprise_value=ev,
        net_debt=net_debt,
        equity_value=equity,
        implied_share_price=price,
        implied_exit_multiple=implied_multiple,
        implied_terminal_growth=implied_g,
    )


# --------------------------------------------------------------------------- #
# Sensitivity & scenarios
# --------------------------------------------------------------------------- #
def sensitivity_table(
    a: DCFAssumptions,
    wacc_values: list[float],
    second_values: list[float],
) -> pd.DataFrame:
    """Implied share price across WACC (rows) and terminal growth or exit multiple (columns).

    The second axis is terminal growth for the Gordon method and the exit multiple
    for the exit-multiple method.
    """
    field = "terminal_growth" if a.terminal_method == "gordon" else "exit_multiple"
    out = pd.DataFrame(index=wacc_values, columns=second_values, dtype=float)
    for w in wacc_values:
        for v in second_values:
            try:
                out.loc[w, v] = run_dcf(a.with_(wacc=w, **{field: v})).implied_share_price
            except ValueError:
                out.loc[w, v] = np.nan
    out.index.name = "WACC"
    out.columns.name = "Terminal growth" if field == "terminal_growth" else "Exit multiple"
    return out


@dataclass(frozen=True)
class Scenario:
    name: str
    growth_shift: float = 0.0   # added to start & end growth
    margin_shift: float = 0.0   # added to start & end margin
    wacc_shift: float = 0.0
    terminal_shift: float = 0.0  # added to terminal growth (gordon) or multiple (exit)


DEFAULT_SCENARIOS = (
    Scenario("Bear", growth_shift=-0.03, margin_shift=-0.03, wacc_shift=0.01, terminal_shift=-0.005),
    Scenario("Base"),
    Scenario("Bull", growth_shift=0.03, margin_shift=0.02, wacc_shift=-0.005, terminal_shift=0.005),
)


def apply_scenario(a: DCFAssumptions, s: Scenario) -> DCFAssumptions:
    terminal_kw = (
        {"terminal_growth": a.terminal_growth + s.terminal_shift}
        if a.terminal_method == "gordon"
        else {"exit_multiple": a.exit_multiple + s.terminal_shift * 100}  # 0.5% -> 0.5x
    )
    return a.with_(
        growth_start=a.growth_start + s.growth_shift,
        growth_end=a.growth_end + s.growth_shift,
        margin_start=a.margin_start + s.margin_shift,
        margin_end=a.margin_end + s.margin_shift,
        wacc=a.wacc + s.wacc_shift,
        **terminal_kw,
    )
