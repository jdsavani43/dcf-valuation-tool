# DCF Valuation Tool

An interactive discounted cash flow (DCF) model for any public company. Enter a ticker and the app
pulls financial statements and market data, suggests starting assumptions from the company's history,
and calculates an intrinsic value per share. You can change every assumption and export the model to
a working Excel file.

Built with Python, Streamlit, pandas, Plotly and openpyxl.

**▶ Live app: https://jdsavani-dcf-valuation.streamlit.app**

## Features

- **Live data**: income statement, cash flow, price, beta, debt and cash from Yahoo Finance, plus the
  10-year Treasury yield as the risk-free rate.
- **Historical defaults**: revenue CAGR, 3-year average EBIT margin, tax rate, D&A, CapEx and working
  capital intensity.
- **WACC build-up**: CAPM cost of equity, after-tax cost of debt and market-value weights.
- **Two terminal value methods**: perpetuity growth (Gordon) and exit EV/EBITDA, each cross-checked
  against the other (implied multiple and implied growth).
- **Mid-year discounting** and an enterprise value to equity value bridge, shown as a waterfall chart.
- **Sensitivity heatmap**: implied price across WACC against terminal growth or exit multiple.
- **Bear, base and bull scenarios** in an editable table.
- **Football field** comparing the 52-week range, analyst targets, DCF sensitivity range and scenario range.
- **Excel export**: a formula-driven model with blue inputs and black formulas, so it can be audited and
  changed in Excel. A unit test evaluates the exported formulas and checks that they give the same share
  price as the Python engine.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
streamlit run app.py
```

Run the tests:

```bash
pytest
```

## Project structure

```
app.py            Streamlit UI (sidebar inputs, tabs, charts)
dcf/model.py      Pure DCF engine: projections, WACC, terminal value, sensitivity, scenarios
dcf/data.py       yfinance loader and historical-assumption logic
dcf/excel.py      Live Excel model export (openpyxl)
tests/            Unit tests, including Excel ↔ Python agreement
```

## Deploy for free (to get a link for your resume)

1. Push this folder to a public GitHub repository.
2. Go to [share.streamlit.io](https://share.streamlit.io), sign in with GitHub and click **New app**.
3. Choose the repository, set the main file to `app.py`, and deploy.
4. Put the resulting `https://<name>.streamlit.app` link on your resume and LinkedIn.

## Methodology

```
Unlevered FCF  = EBIT × (1 − t) + D&A − CapEx − ΔNWC
Cost of equity = Rf + β × ERP
WACC           = E/(D+E) × Re + D/(D+E) × Rd × (1 − t)
Terminal value = FCF_N × (1 + g) / (WACC − g)   or   EBITDA_N × exit multiple
Equity value   = Enterprise value − (Debt − Cash)
```

## Limitations

- Yahoo Finance data can contain gaps or errors. Check key inputs against the 10-K.
- An unlevered DCF fits banks, insurers and REITs poorly.
- This is an educational tool, not investment advice.
