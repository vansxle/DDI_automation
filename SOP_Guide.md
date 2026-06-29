# SOP Guide — Wisdom Consolidated Statement Engine

## 1. Overview

This engine converts a raw position CSV snapshot into a client-ready A4 PDF
statement. The workflow is:

```
CSV  →  engine.py  →  filled HTML  →  WeasyPrint  →  PDF
```

Two artefacts are consumed:
- **`template.html`** — static layout with `{{token}}` placeholders; never
  contains hardcoded financial figures.
- **`engine.py`** — reads the CSV, computes every aggregation, generates SVG
  charts inline, and performs string-substitution to fill the template.

---

## 2. CSV Schema & Column Reference

| Column | Type | Used for |
|---|---|---|
| `Date` / `FileDate` | YYYY/M/D | Statement date on cover & headers |
| `Broker` | string | Custodian name (e.g. `BOS HK`, `LGT SG`) |
| `Portfolio` | A / B / C | Account identifier shown in Position Detail |
| `Currency` | ISO code | Native currency of the position |
| `Total_Amount_In_USD` | float | **Primary value field** — amount in USD including accrued interest |
| `Statement_Product_Type` | string | **Category mapping key** (see §3) |
| `Description` | string | Instrument name shown in Position Detail |
| `ISIN` | string | Fallback identifier if Description is blank |

All other columns (`Nominal/Quantity`, `Last_Price`, `Exchange_Rate`, …) are
present in the feed but not directly consumed by the current engine version.

---

## 3. Statement_Product_Type → Report Category Mapping

The engine maps the raw `Statement_Product_Type` column to one of seven fixed
report buckets. Any type not in the table defaults to **STP**.

| `Statement_Product_Type` | Report Category | Rationale |
|---|---|---|
| `Cash` | **Liquidity** | Current / call accounts |
| `TD` | **Liquidity** | Term deposits (short-term cash equivalents) |
| `Bond` | **Bond** | Standard fixed-income bonds |
| `FRN` | **Bond** | Floating-rate notes (Asset class = Bonds and notes) |
| `Equity` | **Equity** | Direct equity holdings |
| `ETF` | **Equity** | Exchange-traded funds (equity sub-class) |
| `Precious Metal` | **Equity** | Bullion / gold accounts (commodity investment) |
| `FCN` | **STP** | Fixed Coupon Notes — structured product |
| `BEN` | **STP** | Barrier / Enhanced Notes — structured product |
| `CLN` | **STP** | Credit-Linked Notes — structured product |
| `RAN` | **STP** | Range Accumulation Notes — structured product |
| `Equity AQ` | **STP** | Equity accumulators & reverse accumulators |
| `Option` | **STP** | OTC equity / index options |
| `Fund` | **Fund** | Mutual, money-market, and hedge funds |
| `TL` | **Loan** | Term loans (typically negative USD value) |
| *(anything else)* | **STP** | Unknown — treated as structured product |

To add a new product type, append one line to `CATEGORY_MAP` in `engine.py`:

```python
CATEGORY_MAP["NEW_TYPE"] = "Bond"   # or whichever bucket
```

---

## 4. Token Contract

Every `{{token}}` in `template.html` is replaced by `engine.py` before PDF
conversion. No token may remain unfilled (the engine will leave the literal
`{{token}}` string if a key is missing, which makes errors visible).

### 4.1 Global / Header Tokens

| Token | Source | Example |
|---|---|---|
| `{{statement_date_display}}` | `FileDate` column (first data row) | `01-Jun-2026` |
| `{{client_name}}` | `--client` CLI argument | `demo` |
| `{{rm_name}}` | `--rm` CLI argument | `Ethan Wang` |

### 4.2 Cover Page Tokens

| Token | Content |
|---|---|
| `{{custodian_accounts_rows}}` | One `<tr>` per unique Broker, listing Portfolio letters |
| `{{total_value_usd}}` | Grand total formatted as `USD 128,994,190` |

### 4.3 Asset Type Allocation Tokens

| Token | Content |
|---|---|
| `{{asset_alloc_rows}}` | One `<tr>` per category (Liquidity, Bond, Equity, Fund, STP, Loan, Ins.) |
| `{{asset_grand_total}}` | Sum of all `Total_Amount_In_USD` values, formatted |
| `{{asset_pie_svg}}` | Inline SVG pie chart (positive categories only) |

### 4.4 Custodian Allocation Tokens

| Token | Content |
|---|---|
| `{{custodian_alloc_rows}}` | One `<tr>` per Broker with its total USD value |
| `{{custodian_grand_total}}` | Same grand total as above |
| `{{custodian_pie_svg}}` | Inline SVG pie chart by custodian |

### 4.5 Custodian / Asset Type Distribution Tokens

| Token | Content |
|---|---|
| `{{custodian_asset_bar_svg}}` | Inline SVG stacked bar chart (categories on x-axis, custodians stacked) |
| `{{custodian_asset_crosstab_rows}}` | One `<tr>` per Broker × 7 category columns + Total |

### 4.6 Currency Distribution Tokens

| Token | Content |
|---|---|
| `{{currency_summary_rows}}` | Liquidity amounts by currency (for the top summary table + bar chart) |
| `{{currency_bar_svg}}` | Inline SVG bar chart of liquidity by currency |
| `{{currency_crosstab_rows}}` | One `<tr>` per currency × 7 category columns + Total |

### 4.7 Position Detail Tokens

Each token generates `<tr>` rows for its section. If the category has no
positions, a single `<tr class="na-row">` with "na" in every cell is emitted
(matching legacy PDF behaviour).

| Token | Category |
|---|---|
| `{{pos_liquidity_rows}}` | Liquidity |
| `{{pos_bond_rows}}` | Bond |
| `{{pos_equity_rows}}` | Equity |
| `{{pos_fund_rows}}` | Fund |
| `{{pos_stp_rows}}` | Structured Product (STP) |
| `{{pos_loan_rows}}` | Loan |
| `{{pos_insurance_rows}}` | Insurance (Ins.) |

Position rows are sorted by absolute USD value descending. Each row contains:
`Custodian | Account | Description | Ccy | Value in USD | %`

where `%` is the position's share of its own category total (by absolute value).

---

## 5. Calculation Logic

| Metric | Formula |
|---|---|
| Category total | `SUM(Total_Amount_In_USD)` for all rows in that category |
| Grand total | `SUM(Total_Amount_In_USD)` across all rows |
| Asset allocation % | `category_total / grand_total × 100` |
| Custodian total | `SUM(Total_Amount_In_USD)` for all rows with that Broker |
| Custodian % | `custodian_total / grand_total × 100` |
| Custodian × Category | `SUM(Total_Amount_In_USD)` grouped by (Broker, category) |
| Currency × Category | `SUM(Total_Amount_In_USD)` grouped by (Currency, category) |
| Liquidity by currency | Subset of the above for category = Liquidity only |
| Position % | `row_value / SUM(ABS(all_values_in_category)) × 100` |

Negative values (loans, short options, accumulators) are included in category
totals and cross-tabs. The pie charts only plot positive-valued segments.

---

## 6. Running the Engine

### Prerequisites

```bash
# System library (macOS)
brew install pango poppler

# Python packages
pip3 install weasyprint pdf2image --break-system-packages
```

### Single run

```bash
python3 engine.py mock_position_01062026.csv \
  --client "demo" \
  --rm "Ethan Wang" \
  --output output/
```

This writes:
- `output/mock_position_01062026_statement.html`
- `output/mock_position_01062026_statement.pdf`

### Arguments

| Argument | Default | Description |
|---|---|---|
| `csv_path` | *(required)* | Path to the position CSV file |
| `--client` | `Client` | Client name on cover page and headers |
| `--rm` | `Relationship Manager` | RM name on cover page and headers |
| `--output` | `output` | Directory for generated HTML and PDF |
| `--template` | `template.html` | Path to the HTML template file |

---

## 7. Automated Pipeline Integration

The engine is deliberately stateless — it reads one CSV, writes one HTML + PDF,
and exits. Plug it into any orchestration tool:

### 7.1 Cron / Scheduler

```bash
# Run on the 1st of every month at 06:00
0 6 1 * * cd /srv/statements && python3 engine.py \
    /data/positions/$(date +\%Y\%m01).csv \
    --client "demo" --rm "Ethan Wang" \
    --output /srv/reports/$(date +\%Y\%m)/
```

### 7.2 Python Orchestration

```python
from engine import load_and_compute, generate_html
from weasyprint import HTML

data = load_and_compute("positions.csv")
html = generate_html(data, "template.html",
                     client_name="ACME Family Office",
                     rm_name="Jane Smith")
HTML(string=html).write_pdf("statement.pdf")
```

### 7.3 Adding a New Data Source

1. Ensure the new feed has a `Statement_Product_Type` column (or a column that
   can be mapped to it before the CSV is passed to the engine).
2. Add any new product types to `CATEGORY_MAP` in `engine.py`.
3. Verify that `Total_Amount_In_USD` reflects the correct USD equivalent value
   including accrued interest.
4. Run the engine and inspect the HTML output before generating the PDF.

### 7.4 Customising the Layout

The visual design lives entirely in the `<style>` block of `template.html`.
To change branding (colours, fonts, logo), edit only `template.html` — no
changes to `engine.py` are needed. The token list in §4 is the contract
between the two files.

---

## 8. Output Files

| File | Description |
|---|---|
| `template.html` | Reusable layout template with `{{token}}` placeholders |
| `engine.py` | Data processing and PDF generation script |
| `output/mock_position_01062026_statement.html` | Filled HTML — 01 Jun 2026 snapshot |
| `output/mock_position_01062026_statement.pdf` | Client-ready PDF — 01 Jun 2026 |
| `output/mock_position_16062026_statement.html` | Filled HTML — 16 Jun 2026 snapshot |
| `output/mock_position_16062026_statement.pdf` | Client-ready PDF — 16 Jun 2026 |
| `SOP_Guide.md` | This document |

---

## 9. Known Constraints

- **No external JavaScript or CSS frameworks** are used; the template is
  self-contained with one `<style>` block, ensuring compatibility with
  automated enterprise PDF renderers.
- **Pie charts** only plot positive-valued segments; negative positions
  (loans, short options) appear in tables but not in the pie.
- **Position Detail descriptions** are truncated to 90 characters to prevent
  table overflow on narrow pages.
- **WeasyPrint** requires `libpango` to be installed on the host OS. On macOS
  this is `brew install pango`; on Debian/Ubuntu it is `apt-get install libpango-1.0-0`.
