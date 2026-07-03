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

Position rows are **truly granular** — one row per individual holding (not a
per-product-type summary), mirroring the legacy Wisdom statement. Rows are
sorted by absolute USD value descending. Each row contains:
`Custodian | Account | Description | Ccy | Value in USD | %`

where `%` is the position's share of its own category gross total (by absolute
value).

**Row-cap behaviour (`MAX_POS_ROWS`, default 18):** to keep long sections
(e.g. 199 options) from overflowing the PDF, each section renders at most
`MAX_POS_ROWS` individual holdings (the largest by absolute value). The
remainder is collapsed into a single aggregated **`Other holdings (N positions)`**
row whose value is the signed sum of the hidden positions. A closing
**`Subtotal — <Category>`** row always shows the section's signed total at 100%.
To show more or fewer individual rows, change the `MAX_POS_ROWS` constant near
the top of the Position Detail block in `engine.py`.

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

## 9. Frontend Redesign — Changelog (v2)

The `template.html` was modernised while keeping the **token contract in §4
identical** (drop-in replacement; `engine.py` data interface unchanged except
for the granular Position Detail rows described in §4.7).

| Area | v1 | v2 |
|---|---|---|
| Running page header | Date label wrapped mid-word (`01-/Jun-2026`) on every page | Fixed via `@page { @top-center: element(...) }` running element — date renders on one line |
| Page numbers | Missing on the disclaimer page | `@bottom-right` `Page X of Y` on every non-cover page |
| Cover | Plain figure | Headline `TOTAL PORTFOLIO VALUE` card with brand-gradient |
| Charts | Small, floating in dead space | Larger, framed in rounded cards; SVGs constrained with `max-width:100%` so frames never overflow the page margin |
| Position Detail | Per-product-type summary | Truly granular per-holding rows with subtotals (see §4.7) |
| Styling | Inline-ish | Centralised `:root` teal palette, zebra striping, tabular-nums |

All figures are still 100% engine-driven `{{token}}` substitutions — no
hardcoded financial values were introduced.

---

## 10. Known Constraints

- **No external JavaScript or CSS frameworks** are used; the template is
  self-contained with one `<style>` block, ensuring compatibility with
  automated enterprise PDF renderers.
- **Pie charts** only plot positive-valued segments; negative positions
  (loans, short options) appear in tables but not in the pie.
- **Position Detail descriptions** are truncated to ~88 characters to prevent
  table overflow on narrow pages.
- **Position Detail row cap**: sections show the top `MAX_POS_ROWS` (default 18)
  holdings; smaller positions are aggregated into one `Other holdings` row.
  This keeps page count bounded regardless of how many raw positions a feed
  contains.
- **WeasyPrint** requires `libpango` to be installed on the host OS. On macOS
  this is `brew install pango`; on Debian/Ubuntu it is `apt-get install libpango-1.0-0`.

---

## 11. Frontend Redesign — Changelog (v3)

v3 builds on v2 with readable charts, the **real company logo**, and
**period-over-period (PoP) comparison**. The token contract in §4 remains
backward-compatible: every existing token is preserved and the engine still
runs on a single CSV with no extra arguments.

| Area | v2 | v3 |
|---|---|---|
| Brand logo | Recreated/styled wordmark | **Real logo** extracted from the legacy statement PDF, embedded as a base64 PNG data URI (`logo_datauri.txt`). Black extraction background made transparent so it sits cleanly on the white cover and in the running header. Never recreated. |
| Allocation charts | Flat pie (positive segments only) | **Donut** with a side legend listing label / value / % per segment and a compact total (`_short_usd`) in the center hole. |
| Currency chart | Vertical bar (USD bar dwarfed the rest) | **Horizontal bars** with signed value labels; negative currencies render in red left of the zero line, the long tail is grouped into `Other (N)`, and the dominant USD bar is capped so smaller bars stay readable. |
| Custodian/Asset chart | Stacked bar, no labels | **Stacked bar** with per-stack value labels and a cleaner legend. |
| Period-over-period | — | New **Section 05 "Portfolio Change"** (when a prior snapshot is supplied): Δ USD and Δ % per asset class and per custodian, color-coded (green gain / red loss), with a highlighted grand-total row and a `NEW` tag for custodians absent from the prior. A cover **delta badge** shows the total change (`▲ +1,389,801 (+1.1%)`). |
| Conditional sections | All sections always rendered | When **no** prior is supplied, the Portfolio Change page is **omitted entirely** (no orphan em-dash table) and the remaining sections renumber automatically (Position Detail → 05, Disclaimer → 06). The TOC and section eyebrows are driven by `{{toc_change_row}}`, `{{sec_position_no}}`, `{{sec_disclaimer_no}}`, `{{toc_position_page}}`. |

### 11.1 New CLI argument

```bash
# Standalone (backward-compatible — no Portfolio Change section):
python3 engine.py mock_position_01062026.csv \
    --client "Demo Client" --rm "Ethan Wang" --output out/

# With period-over-period comparison vs a prior snapshot:
python3 engine.py mock_position_16062026.csv \
    --client "Demo Client" --rm "Ethan Wang" --output out/ \
    --prior mock_position_01062026.csv
```

`--prior <csv>` is optional. When given, the engine loads the prior snapshot via
the same `load_and_compute()` path and computes deltas; when omitted, all
delta-bearing tokens collapse to em-dashes / empty and the change section is
dropped.

### 11.2 New / changed chart functions (`engine.py`)

| Function | Purpose |
|---|---|
| `donut_svg(segments, width, height, center_title, center_value)` | Donut chart + side legend (replaces the old `pie_svg`). |
| `hbar_svg(rows, width, height, max_rows)` | Signed horizontal bars with value labels + top-N grouping. |
| `stacked_bar_svg(categories, custodians, data, ...)` | Stacked bar with value labels and legend. |
| `_short_usd(n)` | Compact money formatting (e.g. `128.99M`, `1.30B`, `540K`) for the donut center. |
| `_delta_pct(diff, prior)` | Δ % using `diff / abs(prior) * 100` so the sign follows the USD direction (correctly handles negative-prior lines such as Loan). |
| `_delta_badge(cur, prior)` | Cover badge HTML + text. |
| `_delta_cell(cur, prior)` | Two `<td>` cells (Δ USD, Δ %) for the change table. |
| `_load_logo_data_uri(template_path)` | Reads `logo_datauri.txt` (next to the template or engine) for the embedded logo. |

### 11.3 New tokens (added; none removed)

`cover_delta_badge`, `portfolio_change_section`, `toc_change_row`,
`sec_position_no`, `sec_disclaimer_no`, `toc_position_page`,
`prior_statement_date`, `logo_data_uri`. The previous `asset_pie_svg`,
`custodian_pie_svg`, and `currency_bar_svg` tokens are retained but now carry
donut / horizontal-bar SVGs.

### 11.4 Deployment note

`logo_datauri.txt` **must be committed alongside `engine.py` and
`template.html`** — the engine reads it at render time. Without it the logo
tokens render empty.
