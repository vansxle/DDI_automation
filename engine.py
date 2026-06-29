#!/usr/bin/env python3
"""
Wisdom Group Holdings — Financial Statement Engine
===================================================
Reads a CSV position snapshot, maps Statement_Product_Type to report categories,
computes all aggregations, fills template.html with {{token}} placeholders,
and renders a PDF via WeasyPrint.

Usage:
    python3 engine.py mock_position_01062026.csv \\
        --client "demo" --rm "Ethan Wang" --output output/

Token contract is documented in SOP_Guide.md.
"""

import csv
import sys
import os
import math
import argparse
import html as html_escape_lib
from datetime import datetime
from collections import defaultdict

try:
    from weasyprint import HTML as WPhtml
except ImportError:
    sys.exit("WeasyPrint not installed. Run: pip3 install weasyprint --break-system-packages")

# ============================================================
# CATEGORY MAPPING
# Maps Statement_Product_Type (raw CSV column) → report bucket
# ============================================================
CATEGORY_MAP = {
    "Cash":          "Liquidity",   # current/call accounts
    "TD":            "Liquidity",   # term deposits (cash equivalent)
    "Bond":          "Bond",        # standard bonds
    "FRN":           "Bond",        # floating-rate notes (asset class = bonds)
    "Equity":        "Equity",      # direct equities
    "ETF":           "Equity",      # exchange-traded funds (equity)
    "Precious Metal":"Equity",      # bullion / gold accounts
    "FCN":           "STP",         # fixed coupon notes (structured)
    "BEN":           "STP",         # barrier / enhanced notes (structured)
    "CLN":           "STP",         # credit-linked notes (structured)
    "RAN":           "STP",         # range accumulation notes (structured)
    "Equity AQ":     "STP",         # equity accumulators & reverse accumulators
    "Option":        "STP",         # OTC equity / index options
    "Fund":          "Fund",        # mutual, money-market, hedge funds
    "TL":            "Loan",        # term loans (typically negative value)
}

CATEGORIES      = ["Liquidity", "Bond", "Equity", "Fund", "STP", "Loan", "Ins."]
POS_SECTION_TITLE = {
    "Liquidity": "Liquidity",
    "Bond":      "Bond",
    "Equity":    "Equity",
    "Fund":      "Fund",
    "STP":       "Structured Product",
    "Loan":      "Loan",
    "Ins.":      "Insurance",
}

# Teal palette used for charts (dark → light)
CHART_COLORS = [
    "#1a4a5a", "#2a6b7c", "#4bacc6",
    "#7fbfcf", "#a5d5df", "#c5e8ef", "#6aafbf",
]


# ============================================================
# HELPERS
# ============================================================

def fmt(n):
    """Format a number with comma thousands separator, 0 decimals."""
    if n is None:
        return "na"
    return f"{n:,.0f}"


def safe_float(s):
    try:
        return float(str(s).strip()) if str(s).strip() else 0.0
    except ValueError:
        return 0.0


def esc(s):
    """HTML-escape a string for safe table cell output."""
    return html_escape_lib.escape(str(s))


# ============================================================
# SVG CHART GENERATORS
# All return a self-contained <svg>…</svg> string.
# ============================================================

def _arc_path(cx, cy, r, start_angle, sweep):
    """Return SVG arc path string for a pie slice (centre-to-edge fan)."""
    end_angle = start_angle + sweep
    large_arc = 1 if sweep > math.pi else 0
    x1 = cx + r * math.cos(start_angle)
    y1 = cy + r * math.sin(start_angle)
    x2 = cx + r * math.cos(end_angle)
    y2 = cy + r * math.sin(end_angle)
    return (
        f"M {cx:.2f} {cy:.2f} "
        f"L {x1:.2f} {y1:.2f} "
        f"A {r:.2f} {r:.2f} 0 {large_arc} 1 {x2:.2f} {y2:.2f} Z"
    )


def pie_svg(segments, width=440, height=300):
    """
    segments : list of (label_str, value_float, pct_str)
               Only segments with value > 0 are drawn.
    Returns an inline SVG string with ample padding for labels.
    """
    # Pie sits in the centre with 90px padding on each side for labels
    pad = 95
    r   = (min(width, height) - 2 * pad) / 2
    cx  = width / 2
    cy  = height / 2
    lbl_r  = r * 1.52   # label text anchor radius
    line_r0 = r + 5     # leader line start (just outside slice)
    line_r1 = r * 1.38  # leader line end

    positive = [(lbl, v, pct) for lbl, v, pct in segments if v > 0]
    total = sum(v for _, v, _ in positive)

    out = [
        f'<svg width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">'
    ]
    if total <= 0:
        out.append(
            f'<text x="{cx:.0f}" y="{cy:.0f}" text-anchor="middle" '
            f'font-size="10" fill="#999">No data</text>'
        )
        out.append("</svg>")
        return "".join(out)

    cur = -math.pi / 2      # start from 12 o'clock
    for i, (lbl, v, pct) in enumerate(positive):
        sweep = 2 * math.pi * v / total
        color = CHART_COLORS[i % len(CHART_COLORS)]

        # Pie slice
        out.append(
            f'<path d="{_arc_path(cx, cy, r, cur, sweep)}" '
            f'fill="{color}" stroke="white" stroke-width="1.5"/>'
        )

        # Label + leader line at midpoint of arc
        mid = cur + sweep / 2
        lx  = cx + lbl_r  * math.cos(mid)
        ly  = cy + lbl_r  * math.sin(mid)
        lx0 = cx + line_r0 * math.cos(mid)
        ly0 = cy + line_r0 * math.sin(mid)
        lx1 = cx + line_r1 * math.cos(mid)
        ly1 = cy + line_r1 * math.sin(mid)

        # Use strict left/right anchoring so text grows away from the pie
        if math.cos(mid) < -0.15:
            anchor = "end"
        elif math.cos(mid) > 0.15:
            anchor = "start"
        else:
            anchor = "middle"

        out.append(
            f'<line x1="{lx0:.1f}" y1="{ly0:.1f}" '
            f'x2="{lx1:.1f}" y2="{ly1:.1f}" '
            f'stroke="#999" stroke-width="0.8"/>'
        )
        out.append(
            f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="{anchor}" '
            f'dominant-baseline="middle" font-size="8.5" fill="#333" '
            f'font-family="Helvetica,Arial,sans-serif">{esc(lbl)}: {esc(pct)}</text>'
        )
        cur += sweep

    out.append("</svg>")
    return "".join(out)


def stacked_bar_svg(categories, custodians, data, width=520, height=320):
    """
    Stacked bar chart: x-axis = asset categories, stacked colours = custodians.

    categories : list of category name strings (x-axis)
    custodians : list of custodian name strings (stack colours)
    data       : {custodian: {category: float_value}}
    """
    pad_l, pad_r, pad_t, pad_b = 58, 175, 20, 56

    cw = width  - pad_l - pad_r
    ch = height - pad_t - pad_b

    # Max stacked height per category (positive values only)
    cat_max = max(
        (sum(max(0.0, data.get(cust, {}).get(cat, 0.0)) for cust in custodians)
         for cat in categories),
        default=1.0
    )
    if cat_max <= 0:
        cat_max = 1.0

    # Round y-axis ceiling to a nice number
    mag = 10 ** math.floor(math.log10(cat_max))
    nice_max = math.ceil(cat_max / mag) * mag
    if nice_max < cat_max * 1.1:
        nice_max += mag

    n_ticks = 4
    tick_step = nice_max / n_ticks

    out = [
        f'<svg width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">',
        f'<rect width="{width}" height="{height}" fill="white"/>',
    ]

    # Grid lines + y-axis labels
    for i in range(n_ticks + 1):
        y_val = i * tick_step
        yp = pad_t + ch - (y_val / nice_max) * ch
        out.append(
            f'<line x1="{pad_l}" y1="{yp:.1f}" x2="{pad_l + cw}" y2="{yp:.1f}" '
            f'stroke="#e8e8e8" stroke-width="0.8"/>'
        )
        lbl = f"{y_val / 1e6:.0f}" if y_val > 0 else "0"
        out.append(
            f'<text x="{pad_l - 5}" y="{yp + 3:.1f}" text-anchor="end" '
            f'font-size="8" fill="#666" font-family="Helvetica,Arial,sans-serif">{lbl}</text>'
        )

    # Y-axis label (rotated)
    mid_y = pad_t + ch / 2
    out.append(
        f'<text x="{pad_l - 45}" y="{mid_y:.1f}" text-anchor="middle" '
        f'font-size="8" fill="#555" font-family="Helvetica,Arial,sans-serif" '
        f'transform="rotate(-90,{pad_l - 45:.1f},{mid_y:.1f})">Value in USD (M)</text>'
    )

    # Bars
    n_cats = len(categories)
    bar_w = (cw / n_cats) * 0.48

    for j, cat in enumerate(categories):
        xc = pad_l + (j + 0.5) * (cw / n_cats)
        xb = xc - bar_w / 2
        cum_h = 0.0
        for i, cust in enumerate(custodians):
            val = max(0.0, data.get(cust, {}).get(cat, 0.0))
            if val <= 0:
                continue
            bh = (val / nice_max) * ch
            yb = pad_t + ch - cum_h - bh
            color = CHART_COLORS[i % len(CHART_COLORS)]
            out.append(
                f'<rect x="{xb:.1f}" y="{yb:.1f}" width="{bar_w:.1f}" '
                f'height="{bh:.1f}" fill="{color}"/>'
            )
            cum_h += bh

        out.append(
            f'<text x="{xc:.1f}" y="{pad_t + ch + 16:.1f}" text-anchor="middle" '
            f'font-size="8" fill="#444" font-family="Helvetica,Arial,sans-serif">{esc(cat)}</text>'
        )

    # Axes
    out.append(
        f'<line x1="{pad_l}" y1="{pad_t}" x2="{pad_l}" y2="{pad_t + ch}" '
        f'stroke="#aaa" stroke-width="0.8"/>'
    )
    out.append(
        f'<line x1="{pad_l}" y1="{pad_t + ch}" x2="{pad_l + cw}" y2="{pad_t + ch}" '
        f'stroke="#aaa" stroke-width="0.8"/>'
    )

    # Legend (right side)
    lx = pad_l + cw + 12
    for i, cust in enumerate(custodians):
        ly = pad_t + 16 + i * 18
        color = CHART_COLORS[i % len(CHART_COLORS)]
        out.append(f'<rect x="{lx}" y="{ly}" width="11" height="10" fill="{color}"/>')
        out.append(
            f'<text x="{lx + 15}" y="{ly + 9}" font-size="8" fill="#444" '
            f'font-family="Helvetica,Arial,sans-serif">{esc(cust)}</text>'
        )

    out.append("</svg>")
    return "".join(out)


def simple_bar_svg(categories, values, width=360, height=220):
    """
    Simple single-colour bar chart (used for currency distribution).
    categories : list of strings
    values     : list of floats (USD amounts)
    """
    pad_l, pad_r, pad_t, pad_b = 58, 20, 15, 48

    cw = width  - pad_l - pad_r
    ch = height - pad_t - pad_b

    max_val = max((v for v in values if v > 0), default=1.0)
    mag = 10 ** math.floor(math.log10(max_val))
    nice_max = math.ceil(max_val / mag) * mag
    if nice_max < max_val * 1.1:
        nice_max += mag

    n_ticks = 4
    tick_step = nice_max / n_ticks

    out = [
        f'<svg width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">',
        f'<rect width="{width}" height="{height}" fill="white"/>',
    ]

    for i in range(n_ticks + 1):
        y_val = i * tick_step
        yp = pad_t + ch - (y_val / nice_max) * ch
        out.append(
            f'<line x1="{pad_l}" y1="{yp:.1f}" x2="{pad_l + cw}" y2="{yp:.1f}" '
            f'stroke="#e8e8e8" stroke-width="0.8"/>'
        )
        lbl = f"{y_val / 1e6:.2f}" if y_val > 0 else "0"
        out.append(
            f'<text x="{pad_l - 4}" y="{yp + 3:.1f}" text-anchor="end" '
            f'font-size="8" fill="#666" font-family="Helvetica,Arial,sans-serif">{lbl}</text>'
        )

    mid_y = pad_t + ch / 2
    out.append(
        f'<text x="{pad_l - 48}" y="{mid_y:.1f}" text-anchor="middle" '
        f'font-size="8" fill="#555" font-family="Helvetica,Arial,sans-serif" '
        f'transform="rotate(-90,{pad_l - 48:.1f},{mid_y:.1f})">Value in USD (M)</text>'
    )

    n_cats = len(categories)
    bar_w = (cw / n_cats) * 0.5

    for j, (cat, val) in enumerate(zip(categories, values)):
        xc = pad_l + (j + 0.5) * (cw / n_cats)
        if val > 0:
            bh = (val / nice_max) * ch
            yb = pad_t + ch - bh
            out.append(
                f'<rect x="{xc - bar_w / 2:.1f}" y="{yb:.1f}" '
                f'width="{bar_w:.1f}" height="{bh:.1f}" fill="#2a6b7c"/>'
            )
        out.append(
            f'<text x="{xc:.1f}" y="{pad_t + ch + 16:.1f}" text-anchor="middle" '
            f'font-size="9" fill="#444" font-family="Helvetica,Arial,sans-serif">{esc(cat)}</text>'
        )

    out.append(
        f'<line x1="{pad_l}" y1="{pad_t}" x2="{pad_l}" y2="{pad_t + ch}" '
        f'stroke="#aaa" stroke-width="0.8"/>'
    )
    out.append(
        f'<line x1="{pad_l}" y1="{pad_t + ch}" x2="{pad_l + cw}" y2="{pad_t + ch}" '
        f'stroke="#aaa" stroke-width="0.8"/>'
    )
    out.append("</svg>")
    return "".join(out)


# ============================================================
# DATA LOADING & AGGREGATION
# ============================================================

def load_and_compute(csv_path):
    """Read CSV, categorise rows, and return aggregated data dict."""
    positions = []
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            raw_type = row["Statement_Product_Type"].strip()
            category = CATEGORY_MAP.get(raw_type, "STP")   # unknown → STP
            val      = safe_float(row["Total_Amount_In_USD"])
            positions.append({
                "category":    category,
                "raw_type":    raw_type,
                "broker":      row["Broker"].strip(),
                "portfolio":   row["Portfolio"].strip(),
                "currency":    row["Currency"].strip(),
                "description": row["Description"].strip(),
                "value_usd":   val,
                "isin":        row["ISIN"].strip(),
            })

    # Statement date from FileDate column of first non-blank row
    stmt_date_display = "—"
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            raw = row.get("FileDate", "").strip()
            if raw:
                try:
                    dt = datetime.strptime(raw, "%Y/%m/%d")
                    stmt_date_display = dt.strftime("%d-%b-%Y")
                except ValueError:
                    stmt_date_display = raw
                break

    # Asset Type Allocation
    asset_totals = defaultdict(float)
    for p in positions:
        asset_totals[p["category"]] += p["value_usd"]

    grand_total = sum(asset_totals.values())

    # Custodian Allocation
    custodian_totals = defaultdict(float)
    for p in positions:
        custodian_totals[p["broker"]] += p["value_usd"]

    custodians = sorted(custodian_totals)

    # Custodian × Category cross-tab
    custodian_asset = defaultdict(lambda: defaultdict(float))
    for p in positions:
        custodian_asset[p["broker"]][p["category"]] += p["value_usd"]

    # Currency × Category cross-tab
    currency_asset = defaultdict(lambda: defaultdict(float))
    for p in positions:
        currency_asset[p["currency"]][p["category"]] += p["value_usd"]

    # Positions grouped by category (for Position Detail section)
    positions_by_cat = defaultdict(list)
    for p in positions:
        positions_by_cat[p["category"]].append(p)

    # Cover page: unique portfolios per custodian
    cover_custodians = defaultdict(set)
    for p in positions:
        cover_custodians[p["broker"]].add(p["portfolio"])

    return {
        "stmt_date_display": stmt_date_display,
        "grand_total":       grand_total,
        "asset_totals":      dict(asset_totals),
        "custodian_totals":  dict(custodian_totals),
        "custodians":        custodians,
        "custodian_asset":   {k: dict(v) for k, v in custodian_asset.items()},
        "currency_asset":    {k: dict(v) for k, v in currency_asset.items()},
        "positions_by_cat":  {k: list(v) for k, v in positions_by_cat.items()},
        "cover_custodians":  {k: sorted(v) for k, v in cover_custodians.items()},
    }


# ============================================================
# HTML GENERATION (template filling)
# ============================================================

def _pos_rows_html(positions_list, cat):
    """
    Generate condensed <tr> rows for one Position Detail section.
    Groups by raw product type, showing count + subtotal per type.
    """
    rows = positions_list or []
    if not rows:
        return '<tr class="na-row"><td colspan="4">—</td></tr>'

    by_type = defaultdict(list)
    for p in rows:
        by_type[p["raw_type"]].append(p)

    type_totals = {t: sum(p["value_usd"] for p in ps) for t, ps in by_type.items()}
    cat_total = sum(type_totals.values()) or 1.0

    html_rows = []
    for t in sorted(type_totals, key=lambda t: -type_totals[t]):
        val = type_totals[t]
        count = len(by_type[t])
        pct = val / cat_total * 100
        html_rows.append(
            f'<tr>'
            f'<td>{esc(t)}</td>'
            f'<td class="r">{count}</td>'
            f'<td class="r">{fmt(val)}</td>'
            f'<td class="r">{pct:.2f}%</td>'
            f'</tr>'
        )
    return "\n".join(html_rows)


def generate_html(data, template_path, client_name, rm_name):
    with open(template_path, encoding="utf-8") as f:
        tmpl = f.read()

    d             = data
    stmt_date     = d["stmt_date_display"]
    grand_total   = d["grand_total"]
    custodians    = d["custodians"]
    asset_totals  = d["asset_totals"]

    # ── Cover page: custodian/portfolio rows ──────────────────
    cov_rows = []
    for broker in sorted(d["cover_custodians"]):
        portfolios = ", ".join(d["cover_custodians"][broker])
        cov_rows.append(
            f"<tr><td>{esc(broker)}</td><td>{esc(portfolios)}</td></tr>"
        )
    custodian_accounts_rows = "\n".join(cov_rows)

    # ── Asset Type Allocation table rows ─────────────────────
    asset_rows = []
    for cat in CATEGORIES:
        val = asset_totals.get(cat, 0.0)
        asset_rows.append(
            f"<tr><td>{esc(cat)}</td><td class='num'>{fmt(val)}</td></tr>"
        )
    asset_alloc_rows = "\n".join(asset_rows)

    # ── Asset pie chart ───────────────────────────────────────
    asset_pie_segs = []
    for cat in CATEGORIES:
        val = asset_totals.get(cat, 0.0)
        if val > 0 and grand_total > 0:
            pct_str = f"{val / grand_total * 100:.2f}%"
            asset_pie_segs.append((cat, val, pct_str))
    asset_pie = pie_svg(asset_pie_segs)

    # ── Custodian Allocation table rows ───────────────────────
    cust_alloc_rows = []
    for cust in sorted(d["custodian_totals"]):
        val = d["custodian_totals"][cust]
        cust_alloc_rows.append(
            f"<tr><td>{esc(cust)}</td><td class='num'>{fmt(val)}</td></tr>"
        )
    custodian_alloc_rows = "\n".join(cust_alloc_rows)

    # ── Custodian pie chart ───────────────────────────────────
    cust_pie_segs = []
    for cust in sorted(d["custodian_totals"]):
        val = d["custodian_totals"][cust]
        if val > 0 and grand_total > 0:
            cust_pie_segs.append((cust, val, f"{val / grand_total * 100:.2f}%"))
    custodian_pie = pie_svg(cust_pie_segs)

    # ── Custodian/Asset stacked bar ───────────────────────────
    chart_cats   = [c for c in CATEGORIES if c != "Ins."]
    cust_bar_svg = stacked_bar_svg(chart_cats, custodians, d["custodian_asset"])

    # ── Custodian × Asset cross-tab rows ──────────────────────
    ca_rows = []
    for cust in sorted(d["custodian_totals"]):
        total = d["custodian_totals"][cust]
        cells = "".join(
            f"<td>{fmt(d['custodian_asset'].get(cust, {}).get(cat, 0.0))}</td>"
            for cat in CATEGORIES
        )
        ca_rows.append(
            f"<tr><td>{esc(cust)}</td>{cells}<td class='total'>{fmt(total)}</td></tr>"
        )
    custodian_asset_crosstab_rows = "\n".join(ca_rows)

    # ── Currency totals across all categories (shared by summary + cross-tab) ──
    ccy_totals = {
        ccy: sum(d["currency_asset"][ccy].values())
        for ccy in d["currency_asset"]
    }
    sorted_ccys = sorted(ccy_totals, key=lambda c: -abs(ccy_totals[c]))

    # ── Currency summary table (all categories) ───────────────
    cur_sum_rows = []
    for ccy in sorted_ccys:
        val = ccy_totals[ccy]
        if abs(val) > 0.01:
            cur_sum_rows.append(
                f"<tr><td>{esc(ccy)}</td><td class='num'>{fmt(val)}</td></tr>"
            )
    currency_summary_rows = "\n".join(cur_sum_rows)

    # ── Currency bar chart (all categories) ───────────────────
    bar_ccys = [c for c in sorted_ccys if ccy_totals[c] > 0]
    bar_vals = [ccy_totals[c] for c in bar_ccys]
    currency_bar = simple_bar_svg(bar_ccys, bar_vals)

    # ── Currency × Asset cross-tab rows ──────────────────────
    cc_rows = []
    for ccy in sorted_ccys:
        total = ccy_totals[ccy]
        if abs(total) < 0.01:
            continue
        cells = "".join(
            f"<td>{fmt(d['currency_asset'][ccy].get(cat, 0.0))}</td>"
            for cat in CATEGORIES
        )
        cc_rows.append(
            f"<tr><td>{esc(ccy)}</td>{cells}<td class='total'>{fmt(total)}</td></tr>"
        )
    currency_crosstab_rows = "\n".join(cc_rows)

    # ── Position Detail rows (one section per category) ───────
    pos_tokens = {
        "pos_liquidity_rows": _pos_rows_html(d["positions_by_cat"].get("Liquidity", []), "Liquidity"),
        "pos_bond_rows":      _pos_rows_html(d["positions_by_cat"].get("Bond", []),      "Bond"),
        "pos_equity_rows":    _pos_rows_html(d["positions_by_cat"].get("Equity", []),    "Equity"),
        "pos_fund_rows":      _pos_rows_html(d["positions_by_cat"].get("Fund", []),      "Fund"),
        "pos_stp_rows":       _pos_rows_html(d["positions_by_cat"].get("STP", []),       "STP"),
        "pos_loan_rows":      _pos_rows_html(d["positions_by_cat"].get("Loan", []),      "Loan"),
        "pos_insurance_rows": _pos_rows_html(d["positions_by_cat"].get("Ins.", []),      "Ins."),
    }

    # ── Total USD display (cover page) ───────────────────────
    total_value_usd = f"USD {fmt(grand_total)}"

    # ── Build full replacement dict ───────────────────────────
    replacements = {
        "statement_date_display":       stmt_date,
        "client_name":                  esc(client_name),
        "rm_name":                      esc(rm_name),
        "custodian_accounts_rows":      custodian_accounts_rows,
        "total_value_usd":              total_value_usd,
        "asset_alloc_rows":             asset_alloc_rows,
        "asset_grand_total":            fmt(grand_total),
        "asset_pie_svg":                asset_pie,
        "custodian_alloc_rows":         custodian_alloc_rows,
        "custodian_grand_total":        fmt(grand_total),
        "custodian_pie_svg":            custodian_pie,
        "custodian_asset_bar_svg":      cust_bar_svg,
        "custodian_asset_crosstab_rows":custodian_asset_crosstab_rows,
        "currency_summary_rows":        currency_summary_rows,
        "currency_bar_svg":             currency_bar,
        "currency_crosstab_rows":       currency_crosstab_rows,
        **pos_tokens,
    }

    for key, val in replacements.items():
        tmpl = tmpl.replace("{{" + key + "}}", str(val))

    return tmpl


# ============================================================
# ENTRY POINT
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Generate a Wisdom Consolidated Statement HTML+PDF from a CSV position file."
    )
    parser.add_argument("csv_path",            help="Path to input CSV position file")
    parser.add_argument("--client",  default="Client",                help="Client display name")
    parser.add_argument("--rm",      default="Relationship Manager",  help="Relationship Manager name")
    parser.add_argument("--output",  default="output",                help="Output directory")
    parser.add_argument("--template",default="template.html",         help="Path to template.html")
    args = parser.parse_args()

    base     = os.path.splitext(os.path.basename(args.csv_path))[0]
    out_dir  = args.output
    os.makedirs(out_dir, exist_ok=True)

    out_html = os.path.join(out_dir, f"{base}_statement.html")
    out_pdf  = os.path.join(out_dir, f"{base}_statement.pdf")

    print(f"[1/3] Loading {args.csv_path} …")
    data = load_and_compute(args.csv_path)

    print(f"      Statement date : {data['stmt_date_display']}")
    print(f"      Grand total    : USD {fmt(data['grand_total'])}")
    print(f"      Custodians     : {', '.join(data['custodians'])}")
    for cat in CATEGORIES:
        val = data["asset_totals"].get(cat, 0.0)
        if val:
            print(f"      {cat:12s}: {fmt(val)}")

    print(f"[2/3] Filling template …")
    filled_html = generate_html(data, args.template, args.client, args.rm)

    with open(out_html, "w", encoding="utf-8") as f:
        f.write(filled_html)
    print(f"      HTML → {out_html}")

    print(f"[3/3] Rendering PDF …")
    WPhtml(filename=out_html).write_pdf(out_pdf)
    print(f"      PDF  → {out_pdf}")

    return out_pdf


if __name__ == "__main__":
    main()
