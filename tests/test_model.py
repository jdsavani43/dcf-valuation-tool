import math

import numpy as np
import pandas as pd
import pytest

from dcf.data import build_historicals
from dcf.excel import build_workbook
from dcf.model import DCFAssumptions, WACCInputs, apply_scenario, run_dcf, sensitivity_table, Scenario

BASE = DCFAssumptions(
    base_revenue=1000.0, years=5, growth_start=0.10, growth_end=0.10, margin_start=0.20, margin_end=0.20,
    tax_rate=0.25, da_pct_revenue=0.05, capex_pct_revenue=0.05, nwc_pct_change_revenue=0.0,
    wacc=0.10, terminal_growth=0.02, mid_year=False, total_debt=200.0, cash=50.0, shares_outstanding=10.0,
)


def test_fcf_hand_calculation():
    df = run_dcf(BASE).projections
    # Year 1: revenue 1100, EBIT 220, NOPAT 165, D&A = CapEx, ΔNWC = 0 -> FCF 165
    assert df["Revenue"].iloc[0] == pytest.approx(1100)
    assert df["Unlevered FCF"].iloc[0] == pytest.approx(165)
    assert df["PV of FCF"].iloc[0] == pytest.approx(165 / 1.1)


def test_gordon_terminal_value_and_bridge():
    r = run_dcf(BASE)
    fcf5 = 1000 * 1.1**5 * 0.2 * 0.75
    tv = fcf5 * 1.02 / (0.10 - 0.02)
    assert r.terminal_value == pytest.approx(tv)
    assert r.pv_terminal_value == pytest.approx(tv / 1.1**5)
    pv_fcfs = sum(1000 * 1.1**t * 0.15 / 1.1**t for t in range(1, 6))
    assert r.enterprise_value == pytest.approx(pv_fcfs + tv / 1.1**5)
    assert r.equity_value == pytest.approx(r.enterprise_value - 150)
    assert r.implied_share_price == pytest.approx(r.equity_value / 10)


def test_implied_metrics_round_trip():
    """The implied exit multiple from Gordon should give back the same value."""
    g = run_dcf(BASE)
    m = run_dcf(BASE.with_(terminal_method="exit_multiple", exit_multiple=g.implied_exit_multiple))
    assert m.implied_share_price == pytest.approx(g.implied_share_price)
    assert m.implied_terminal_growth == pytest.approx(0.02)


def test_mid_year_increases_value():
    assert run_dcf(BASE.with_(mid_year=True)).enterprise_value > run_dcf(BASE).enterprise_value


def test_wacc_below_growth_raises():
    with pytest.raises(ValueError):
        run_dcf(BASE.with_(wacc=0.02, terminal_growth=0.03))


def test_wacc_formula():
    w = WACCInputs(risk_free=0.04, beta=1.2, equity_risk_premium=0.05, pre_tax_cost_of_debt=0.06,
                   tax_rate=0.25, market_cap=800, total_debt=200)
    assert w.cost_of_equity == pytest.approx(0.10)
    assert w.wacc == pytest.approx(0.8 * 0.10 + 0.2 * 0.045)


def test_sensitivity_is_monotonic():
    t = sensitivity_table(BASE, [0.08, 0.10, 0.12], [0.01, 0.02, 0.03])
    assert (np.diff(t.values, axis=0) < 0).all()   # higher WACC -> lower price
    assert (np.diff(t.values, axis=1) > 0).all()   # higher growth -> higher price
    assert t.loc[0.10, 0.02] == pytest.approx(run_dcf(BASE).implied_share_price)


def test_scenarios_order():
    bear = run_dcf(apply_scenario(BASE, Scenario("Bear", -0.03, -0.03, 0.01, -0.005))).implied_share_price
    bull = run_dcf(apply_scenario(BASE, Scenario("Bull", 0.03, 0.02, -0.005, 0.005))).implied_share_price
    assert bear < run_dcf(BASE).implied_share_price < bull


@pytest.mark.parametrize("method", ["gordon", "exit_multiple"])
@pytest.mark.parametrize("mid_year", [True, False])
def test_excel_model_matches_python(tmp_path, method, mid_year):
    """Evaluate the exported workbook's formulas and compare with the Python engine."""
    pycel = pytest.importorskip("pycel")
    a = BASE.with_(terminal_method=method, mid_year=mid_year, growth_start=0.12, growth_end=0.04,
                   margin_start=0.18, margin_end=0.24, nwc_pct_change_revenue=0.1, exit_multiple=11.0)
    path = tmp_path / "model.xlsx"
    build_workbook(a, "TestCo").save(path)
    xl = pycel.ExcelCompiler(filename=str(path))
    excel_price = xl.evaluate("DCF!B42")
    assert xl.evaluate("DCF!A42") == "Implied share price"
    assert excel_price == pytest.approx(run_dcf(a).implied_share_price, rel=1e-9)


def test_build_historicals_signs():
    cols = [pd.Timestamp("2024-12-31"), pd.Timestamp("2023-12-31")]
    inc = pd.DataFrame({cols[0]: [1100, 220, 100, 21, 0.21], cols[1]: [1000, 200, 90, 19, 0.21]},
                       index=["Total Revenue", "EBIT", "Pretax Income", "Tax Provision", "Tax Rate For Calcs"])
    cf = pd.DataFrame({cols[0]: [50, -60, -10], cols[1]: [45, -55, -5]},
                      index=["Depreciation And Amortization", "Capital Expenditure", "Change In Working Capital"])
    h = build_historicals(inc, cf)
    assert list(h.index) == [2023, 2024]
    assert h.loc[2024, "CapEx"] == 60 and h.loc[2024, "Change in NWC"] == 10
    assert h.loc[2024, "Growth %"] == pytest.approx(0.10)
    assert h.loc[2024, "Unlevered FCF"] == pytest.approx(220 * 0.79 + 50 - 60 - 10)
