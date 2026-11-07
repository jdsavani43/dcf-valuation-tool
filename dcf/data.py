"""Load company financials from Yahoo Finance (via yfinance) and derive DCF defaults."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

DEFAULT_RISK_FREE = 0.042
DEFAULT_ERP = 0.055


@dataclass
class CompanyData:
    ticker: str
    name: str
    currency: str
    price: float
    shares_outstanding: float
    market_cap: float
    total_debt: float
    cash: float
    beta: float
    historicals: pd.DataFrame          # rows = fiscal years (oldest -> newest)
    info: dict = field(default_factory=dict)

    @property
    def latest(self) -> pd.Series:
        return self.historicals.iloc[-1]


def _row(df: pd.DataFrame, *names: str) -> pd.Series:
    """Return the first matching row from a yfinance statement, or NaNs."""
    for n in names:
        if n in df.index:
            return pd.to_numeric(df.loc[n], errors="coerce")
    return pd.Series(np.nan, index=df.columns)


def build_historicals(income: pd.DataFrame, cashflow: pd.DataFrame) -> pd.DataFrame:
    """Normalise yfinance income statement + cash flow into one tidy table."""
    cols = [c for c in income.columns if c in cashflow.columns] or list(income.columns)
    inc, cf = income[cols], cashflow.reindex(columns=cols)

    revenue = _row(inc, "Total Revenue", "Operating Revenue")
    ebit = _row(inc, "EBIT", "Operating Income")
    da = _row(cf, "Depreciation And Amortization", "Depreciation Amortization Depletion")
    if da.isna().all():
        da = _row(inc, "Reconciled Depreciation")
    capex = -_row(cf, "Capital Expenditure")          # yfinance reports capex as negative
    delta_nwc = -_row(cf, "Change In Working Capital")  # cash-flow sign -> investment sign
    pretax = _row(inc, "Pretax Income")
    tax = _row(inc, "Tax Provision")
    tax_rate = _row(inc, "Tax Rate For Calcs")
    tax_rate = tax_rate.fillna(tax / pretax)

    h = pd.DataFrame(
        {
            "Revenue": revenue,
            "EBIT": ebit,
            "D&A": da,
            "CapEx": capex,
            "Change in NWC": delta_nwc,
            "Tax Rate": tax_rate,
        }
    )
    h.index = [pd.Timestamp(c).year for c in h.index]
    h.index.name = "Fiscal Year"
    h = h.sort_index().dropna(subset=["Revenue"])
    h["Growth %"] = h["Revenue"].pct_change()
    h["EBIT Margin %"] = h["EBIT"] / h["Revenue"]
    h["D&A % Rev"] = h["D&A"] / h["Revenue"]
    h["CapEx % Rev"] = h["CapEx"] / h["Revenue"]
    h["ΔNWC % ΔRev"] = h["Change in NWC"] / h["Revenue"].diff()
    h["Unlevered FCF"] = h["EBIT"] * (1 - h["Tax Rate"]) + h["D&A"] - h["CapEx"] - h["Change in NWC"]
    return h


def fetch_company(ticker: str) -> CompanyData:
    """Download statements and market data for a ticker. Raises ValueError on failure."""
    import yfinance as yf

    t = yf.Ticker(ticker)
    info = t.info or {}
    income, cashflow = t.income_stmt, t.cashflow
    if income is None or income.empty:
        raise ValueError(f"No financial statements found for '{ticker}'.")

    hist = build_historicals(income, cashflow)
    if hist.empty:
        raise ValueError(f"No revenue history found for '{ticker}'.")

    price = info.get("currentPrice") or info.get("regularMarketPrice") or info.get("previousClose")
    shares = info.get("sharesOutstanding") or info.get("impliedSharesOutstanding")
    if not price or not shares:
        raise ValueError(f"Missing price or share count for '{ticker}'.")

    return CompanyData(
        ticker=ticker.upper(),
        name=info.get("longName") or info.get("shortName") or ticker.upper(),
        currency=info.get("financialCurrency") or info.get("currency") or "USD",
        price=float(price),
        shares_outstanding=float(shares),
        market_cap=float(info.get("marketCap") or price * shares),
        total_debt=float(info.get("totalDebt") or 0.0),
        cash=float(info.get("totalCash") or 0.0),
        beta=float(info.get("beta") or 1.0),
        historicals=hist,
        info=info,
    )


def fetch_risk_free_rate() -> float:
    """10-year US Treasury yield from ^TNX, falling back to a static default."""
    try:
        import yfinance as yf

        closes = yf.Ticker("^TNX").history(period="5d")["Close"].dropna()
        if not closes.empty:
            return float(closes.iloc[-1]) / 100
    except Exception:
        pass
    return DEFAULT_RISK_FREE


def _clean_mean(s: pd.Series, default: float, lo: float, hi: float) -> float:
    s = s.replace([np.inf, -np.inf], np.nan).dropna()
    val = float(s.mean()) if not s.empty else default
    return float(np.clip(val, lo, hi))


def _clean_median(s: pd.Series, default: float, lo: float, hi: float) -> float:
    s = s.replace([np.inf, -np.inf], np.nan).dropna()
    val = float(s.median()) if not s.empty else default
    return float(np.clip(val, lo, hi))


def suggested_defaults(c: CompanyData) -> dict:
    """Derive sensible starting assumptions from a company's history."""
    h = c.historicals
    rev = h["Revenue"].dropna()
    if len(rev) >= 2 and rev.iloc[0] > 0:
        cagr = (rev.iloc[-1] / rev.iloc[0]) ** (1 / (len(rev) - 1)) - 1
    else:
        cagr = 0.05
    growth_start = float(np.clip(cagr, -0.05, 0.30))
    margin = _clean_mean(h["EBIT Margin %"].tail(3), 0.15, -0.2, 0.6)
    interest = c.info.get("interestExpense")
    cost_of_debt = 0.055
    if interest and c.total_debt:
        cost_of_debt = float(np.clip(abs(interest) / c.total_debt, 0.03, 0.12))

    return {
        "growth_start": growth_start,
        "growth_end": float(min(growth_start, 0.04)),
        "margin_start": margin,
        "margin_end": margin,
        "tax_rate": _clean_mean(h["Tax Rate"].tail(3), 0.21, 0.0, 0.40),
        "da_pct_revenue": _clean_mean(h["D&A % Rev"].tail(3), 0.03, 0.0, 0.30),
        "capex_pct_revenue": _clean_mean(h["CapEx % Rev"].tail(3), 0.04, 0.0, 0.40),
        # ΔNWC / ΔRevenue is very noisy year to year, so use the median and a tight clamp.
        "nwc_pct_change_revenue": _clean_median(h["ΔNWC % ΔRev"].tail(3), 0.05, 0.0, 0.25),
        "pre_tax_cost_of_debt": cost_of_debt,
    }
