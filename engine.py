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

def _arc(cx, cy, r, a0, a1):
    x0, y0 = cx + r * math.cos(a0), cy + r * math.sin(a0)
    x1, y1 = cx + r * math.cos(a1), cy + r * math.sin(a1)
    large = 1 if (a1 - a0) > math.pi else 0
    return x0, y0, x1, y1, large


def donut_svg(segments, width=470, height=300, center_title="", center_value=""):
    """Donut chart with a clean side legend (label, value, %).
    segments: list of (label, value, ...). Only value>0 drawn; % recomputed internally."""
    pos = [(l, v) for l, v, *_ in segments if v > 0]
    total = sum(v for _, v in pos) or 1.0

    R = 92        # outer radius
    r_in = 56     # inner radius (donut hole)
    cx, cy = 118, height / 2

    out = [f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
           f'xmlns="http://www.w3.org/2000/svg" font-family="Helvetica,Arial,sans-serif">']

    if total <= 0 or not pos:
        out.append(f'<text x="{cx}" y="{cy}" text-anchor="middle" font-size="11" fill="#999">No data</text></svg>')
        return "".join(out)

    a = -math.pi / 2
    for i, (lbl, v) in enumerate(pos):
        sweep = 2 * math.pi * v / total
        a1 = a + sweep
        x0, y0, x1, y1, large = _arc(cx, cy, R, a, a1)
        xi0, yi0, xi1, yi1, _ = _arc(cx, cy, r_in, a, a1)
        color = CHART_COLORS[i % len(CHART_COLORS)]
        out.append(
            f'<path d="M {x0:.2f} {y0:.2f} A {R} {R} 0 {large} 1 {x1:.2f} {y1:.2f} '
            f'L {xi1:.2f} {yi1:.2f} A {r_in} {r_in} 0 {large} 0 {xi0:.2f} {yi0:.2f} Z" '
            f'fill="{color}" stroke="#fff" stroke-width="2"/>'
        )
        a = a1

    if center_value:
        out.append(f'<text x="{cx}" y="{cy-4}" text-anchor="middle" font-size="9" fill="#7b8a90" '
                   f'letter-spacing="1">{esc(center_title)}</text>')
        out.append(f'<text x="{cx}" y="{cy+13}" text-anchor="middle" font-size="13" font-weight="700" '
                   f'fill="#143b46">{esc(center_value)}</text>')

    lx = 235
    n = len(pos)
    row_h = min(26, (height - 30) / max(n, 1))
    ly = (height - n * row_h) / 2 + row_h / 2
    for i, (lbl, v) in enumerate(pos):
        color = CHART_COLORS[i % len(CHART_COLORS)]
        yc = ly + i * row_h
        pct = v / total * 100
        out.append(f'<rect x="{lx}" y="{yc-6}" width="12" height="12" rx="2" fill="{color}"/>')
        out.append(f'<text x="{lx+19}" y="{yc+3.5}" font-size="9.5" fill="#1c2529">{esc(lbl)}</text>')
        out.append(f'<text x="{width-70}" y="{yc+3.5}" font-size="9.5" fill="#1c2529" '
                   f'text-anchor="end" font-weight="600">{fmt(v)}</text>')
        out.append(f'<text x="{width-2}" y="{yc+3.5}" font-size="9" fill="#7b8a90" '
                   f'text-anchor="end">{pct:.1f}%</text>')
    out.append("</svg>")
    return "".join(out)


def hbar_svg(rows, width=470, height=330, max_rows=12):
    """Horizontal bar chart with value labels - readable even with one dominant value.
    rows: list of (label, value). Negative values shown in red, label kept right of zero-line."""
    rows = [(l, v) for l, v in rows if abs(v) > 0.01]
    if len(rows) > max_rows:
        head = rows[:max_rows - 1]
        rest_val = sum(v for _, v in rows[max_rows - 1:])
        rows = head + [(f"Other ({len(rows)-(max_rows-1)})", rest_val)]

    out = [f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
           f'xmlns="http://www.w3.org/2000/svg" font-family="Helvetica,Arial,sans-serif">']
    if not rows:
        out.append(f'<text x="{width/2}" y="{height/2}" text-anchor="middle" font-size="11" fill="#999">No data</text></svg>')
        return "".join(out)

    pad_l, pad_r, pad_t, pad_b = 62, 92, 12, 12
    plot_w = width - pad_l - pad_r
    n = len(rows)
    row_h = (height - pad_t - pad_b) / n
    bar_h = min(15, row_h * 0.58)

    vmax = max((v for _, v in rows), default=1.0)
    vmin = min((v for _, v in rows), default=0.0)
    span_pos = max(vmax, 0.0)
    span_neg = max(-vmin, 0.0)
    # Cap the negative gutter so a small negative never eats >18% of the plot.
    neg_frac = 0.0 if span_pos <= 0 else min(0.18, span_neg / (span_pos + span_neg))
    pos_w = plot_w * (1 - neg_frac)
    neg_w = plot_w * neg_frac
    zero_x = pad_l + neg_w
    pos_scale = pos_w / (span_pos or 1.0)
    neg_scale = (neg_w / (span_neg or 1.0)) if span_neg > 0 else 0.0

    for i, (lbl, v) in enumerate(rows):
        yc = pad_t + i * row_h + row_h / 2
        out.append(f'<text x="{pad_l-8}" y="{yc+3.2}" text-anchor="end" font-size="9" '
                   f'fill="#1c2529">{esc(lbl)}</text>')
        if v >= 0:
            w = v * pos_scale
            x = zero_x; color = "#2a6b7c"
            tx = x + w + 5; anchor = "start"
        else:
            w = (-v) * neg_scale
            x = zero_x - w; color = "#b4453b"
            tx = zero_x + 5; anchor = "start"
        out.append(f'<rect x="{x:.1f}" y="{yc-bar_h/2:.1f}" width="{max(w,0.6):.1f}" '
                   f'height="{bar_h:.1f}" rx="1.5" fill="{color}"/>')
        lblcolor = "#b4453b" if v < 0 else "#4a585d"
        out.append(f'<text x="{tx:.1f}" y="{yc+3.2:.1f}" text-anchor="{anchor}" font-size="8.5" '
                   f'fill="{lblcolor}">{fmt(v)}</text>')
    out.append(f'<line x1="{zero_x:.1f}" y1="{pad_t}" x2="{zero_x:.1f}" y2="{height-pad_b}" '
               f'stroke="#cfd8db" stroke-width="0.8"/>')
    out.append("</svg>")
    return "".join(out)


def stacked_bar_svg(categories, custodians, data, width=560, height=300):
    """Stacked vertical bar: x=categories, stacks=custodians, value labels on totals.
    data: {custodian: {category: value}}"""
    pad_l, pad_r, pad_t, pad_b = 50, 150, 22, 40
    cw = width - pad_l - pad_r
    ch = height - pad_t - pad_b

    cat_tot = {c: sum(max(0.0, data.get(s, {}).get(c, 0.0)) for s in custodians) for c in categories}
    cmax = max(cat_tot.values(), default=1.0) or 1.0
    mag = 10 ** math.floor(math.log10(cmax))
    nice = math.ceil(cmax / mag) * mag
    if nice < cmax * 1.08:
        nice += mag
    ticks = 4
    step = nice / ticks

    out = [f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
           f'xmlns="http://www.w3.org/2000/svg" font-family="Helvetica,Arial,sans-serif">']
    for i in range(ticks + 1):
        yv = i * step
        yp = pad_t + ch - (yv / nice) * ch
        out.append(f'<line x1="{pad_l}" y1="{yp:.1f}" x2="{pad_l+cw}" y2="{yp:.1f}" stroke="#eef2f3" stroke-width="1"/>')
        out.append(f'<text x="{pad_l-6}" y="{yp+3:.1f}" text-anchor="end" font-size="8" fill="#7b8a90">{yv/1e6:.0f}</text>')
    midy = pad_t + ch / 2
    out.append(f'<text x="{pad_l-38}" y="{midy:.1f}" text-anchor="middle" font-size="8" fill="#7b8a90" '
               f'transform="rotate(-90,{pad_l-38:.1f},{midy:.1f})">Value in USD (M)</text>')

    n = len(categories)
    bw = (cw / n) * 0.5
    for j, c in enumerate(categories):
        xc = pad_l + (j + 0.5) * (cw / n)
        xb = xc - bw / 2
        cum = 0.0
        for i, s in enumerate(custodians):
            v = max(0.0, data.get(s, {}).get(c, 0.0))
            if v <= 0:
                continue
            bh = (v / nice) * ch
            yb = pad_t + ch - cum - bh
            out.append(f'<rect x="{xb:.1f}" y="{yb:.1f}" width="{bw:.1f}" height="{bh:.1f}" fill="{CHART_COLORS[i%len(CHART_COLORS)]}"/>')
            cum += bh
        if cat_tot[c] > 0:
            out.append(f'<text x="{xc:.1f}" y="{pad_t+ch-cum-4:.1f}" text-anchor="middle" font-size="7.5" '
                       f'fill="#4a585d">{cat_tot[c]/1e6:.1f}</text>')
        out.append(f'<text x="{xc:.1f}" y="{pad_t+ch+15:.1f}" text-anchor="middle" font-size="8.5" fill="#1c2529">{esc(c)}</text>')

    out.append(f'<line x1="{pad_l}" y1="{pad_t+ch}" x2="{pad_l+cw}" y2="{pad_t+ch}" stroke="#cfd8db" stroke-width="0.8"/>')
    lx = pad_l + cw + 16
    ly0 = pad_t + (ch - len(custodians) * 17) / 2
    for i, s in enumerate(custodians):
        ly = ly0 + i * 17
        out.append(f'<rect x="{lx}" y="{ly}" width="11" height="11" rx="2" fill="{CHART_COLORS[i%len(CHART_COLORS)]}"/>')
        out.append(f'<text x="{lx+16}" y="{ly+9.5}" font-size="8.5" fill="#1c2529">{esc(s)}</text>')
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

# Max individual holdings rendered per Position Detail section before the
# remainder is collapsed into a single aggregated "Other holdings" row.
# Keeps long sections (e.g. 199 options) from overflowing the PDF while
# preserving a genuinely granular, per-instrument list like the legacy report.
MAX_POS_ROWS = 18


def _desc_for(p):
    """Best human label for a position: Description, else ISIN, else raw type."""
    d = p.get("description", "").strip()
    if d and d.lower() != "na":
        return d
    if p.get("isin", "").strip():
        return p["isin"].strip()
    return p.get("raw_type", "") or "—"


def _pos_rows_html(positions_list, cat):
    """
    Generate granular per-holding <tr> rows for one Position Detail section.

    Columns: Custodian | Account | Description | Ccy | Value in USD | %
    matching the legacy Wisdom statement layout. Rows are sorted by absolute
    USD value (largest first). If a section has more than MAX_POS_ROWS holdings,
    the smaller ones are collapsed into a single aggregated remainder row so the
    page never overflows. Percentages are share of the category's gross
    (absolute-value) total, so signed positions read sensibly.
    """
    rows = [p for p in (positions_list or []) if abs(p["value_usd"]) > 0.01]
    if not rows:
        return '<tr class="na-row"><td colspan="6">na</td></tr>'

    # Denominator: gross (absolute) category total so signed shares are sane.
    gross_total = sum(abs(p["value_usd"]) for p in rows) or 1.0

    ordered = sorted(rows, key=lambda p: -abs(p["value_usd"]))

    shown = ordered[:MAX_POS_ROWS]
    rest  = ordered[MAX_POS_ROWS:]

    html_rows = []
    for p in shown:
        val = p["value_usd"]
        pct = abs(val) / gross_total * 100
        desc = _desc_for(p)
        if len(desc) > 88:
            desc = desc[:87].rstrip() + "…"
        html_rows.append(
            f'<tr>'
            f'<td>{esc(p["broker"])}</td>'
            f'<td>{esc(p["portfolio"])}</td>'
            f'<td class="desc">{esc(desc)}</td>'
            f'<td>{esc(p["currency"])}</td>'
            f'<td class="r">{fmt(val)}</td>'
            f'<td class="r">{pct:.2f}%</td>'
            f'</tr>'
        )

    if rest:
        rest_val = sum(p["value_usd"] for p in rest)
        rest_pct = abs(rest_val) / gross_total * 100
        html_rows.append(
            f'<tr class="more-row">'
            f'<td>—</td><td>—</td>'
            f'<td class="desc"><em>Other holdings ({len(rest)} positions)</em></td>'
            f'<td>—</td>'
            f'<td class="r">{fmt(rest_val)}</td>'
            f'<td class="r">{rest_pct:.2f}%</td>'
            f'</tr>'
        )

    # Section subtotal row (signed sum of all positions in the category)
    section_total = sum(p["value_usd"] for p in rows)
    html_rows.append(
        f'<tr class="subtotal-row">'
        f'<td colspan="4">Subtotal &mdash; {esc(POS_SECTION_TITLE.get(cat, cat))}</td>'
        f'<td class="r">{fmt(section_total)}</td>'
        f'<td class="r">100.00%</td>'
        f'</tr>'
    )
    return "\n".join(html_rows)


def _short_usd(n):
    """Compact USD label for donut centre, e.g. 128.99M / 1.30B / 540K."""
    a = abs(n)
    if a >= 1e9:
        return f"{n/1e9:.2f}B"
    if a >= 1e6:
        return f"{n/1e6:.2f}M"
    if a >= 1e3:
        return f"{n/1e3:.0f}K"
    return f"{n:.0f}"


def _delta_pct(diff, prior):
    """Percentage change anchored to the *magnitude* of the prior value, signed by the
    direction of the USD change. This keeps signed liabilities (e.g. loans that grow
    more negative) reading as a decrease, not a spurious positive."""
    if not prior:
        return 0.0
    return diff / abs(prior) * 100


def _delta_badge(cur, prior):
    """Return (html_span, plain_text) describing change vs prior. prior None -> em-dash."""
    if prior is None:
        return ('<span class="delta-flat">&mdash;</span>', "\u2014")
    diff = cur - prior
    pct = _delta_pct(diff, prior)
    arrow = "\u25B2" if diff > 0 else ("\u25BC" if diff < 0 else "")
    cls = "delta-up" if diff > 0 else ("delta-down" if diff < 0 else "delta-flat")
    sign = "+" if diff > 0 else ("" if diff < 0 else "")
    txt = f"{arrow} {sign}{fmt(diff)} ({sign}{pct:.1f}%)".strip()
    return (f'<span class="{cls}">{txt}</span>', txt)


def _delta_cell(cur, prior):
    """Two table cells (Δ USD | Δ%) for a row. prior None -> em-dashes."""
    if prior is None:
        return "<td class='num delta-flat'>&mdash;</td><td class='num delta-flat'>&mdash;</td>"
    diff = cur - prior
    pct = _delta_pct(diff, prior)
    cls = "delta-up" if diff > 0 else ("delta-down" if diff < 0 else "delta-flat")
    sign = "+" if diff > 0 else ("" if diff < 0 else "")
    return (f"<td class='num {cls}'>{sign}{fmt(diff)}</td>"
            f"<td class='num {cls}'>{sign}{pct:.1f}%</td>")


def _load_logo_data_uri(template_path):
    """Return the embedded Wisdom logo as a PNG data-URI string.
    Looks for logo_datauri.txt next to the template (or the engine). The file is the
    real brand logo extracted from the legacy statement — NEVER a recreation.
    Falls back to an empty string if missing so rendering still succeeds."""
    here = os.path.dirname(os.path.abspath(template_path)) or "."
    candidates = [
        os.path.join(here, "logo_datauri.txt"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo_datauri.txt"),
    ]
    for path in candidates:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return f.read().strip()
    return ""


def generate_html(data, template_path, client_name, rm_name, prior=None):
    """Fill template with the current snapshot. If `prior` (a load_and_compute dict
    from an earlier statement) is supplied, period-over-period deltas are rendered;
    otherwise all delta tokens fall back to em-dashes (fully backward-compatible)."""
    with open(template_path, encoding="utf-8") as f:
        tmpl = f.read()

    logo_data_uri = _load_logo_data_uri(template_path)

    d             = data
    stmt_date     = d["stmt_date_display"]
    grand_total   = d["grand_total"]
    custodians    = d["custodians"]
    asset_totals  = d["asset_totals"]

    # Prior snapshot lookups (None-safe)
    p_grand   = prior["grand_total"]      if prior else None
    p_asset   = prior["asset_totals"]     if prior else {}
    p_cust    = prior["custodian_totals"] if prior else {}
    p_date    = prior["stmt_date_display"] if prior else None

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
    asset_pie = donut_svg(asset_pie_segs, center_title="TOTAL", center_value=_short_usd(grand_total))

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
    custodian_pie = donut_svg(cust_pie_segs, center_title="TOTAL", center_value=_short_usd(grand_total))

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
    # Horizontal bars keep the dominant USD bar readable and show signed values.
    ccy_rows = [(c, ccy_totals[c]) for c in sorted_ccys if abs(ccy_totals[c]) > 0.01]
    currency_bar = hbar_svg(ccy_rows)

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

    # ── Period-over-period deltas (optional) ─────────────────
    # Cover badge: grand-total change vs prior snapshot.
    cover_delta_html, _cover_delta_txt = _delta_badge(grand_total, p_grand)
    if prior:
        change_caption = (f"Change vs {esc(p_date)} \u2014 "
                          f"prior total USD {fmt(p_grand)}")
    else:
        change_caption = "No prior statement supplied \u2014 period-over-period comparison unavailable."

    # Portfolio Change table: grand total + each asset class + each custodian.
    change_rows = []
    # Grand total row (emphasised)
    change_rows.append(
        f"<tr class='pchange-total'><td>Total Portfolio Value</td>"
        f"<td class='num'>{fmt(grand_total)}</td>"
        f"<td class='num'>{fmt(p_grand) if prior else '&mdash;'}</td>"
        f"{_delta_cell(grand_total, p_grand)}</tr>"
    )
    # Asset-class rows
    for cat in CATEGORIES:
        cur_v = asset_totals.get(cat, 0.0)
        pri_v = p_asset.get(cat, 0.0) if prior else None
        if cur_v == 0 and (pri_v in (0.0, None)):
            continue
        change_rows.append(
            f"<tr><td>Asset \u2014 {esc(cat)}</td>"
            f"<td class='num'>{fmt(cur_v)}</td>"
            f"<td class='num'>{fmt(pri_v) if prior else '&mdash;'}</td>"
            f"{_delta_cell(cur_v, pri_v)}</tr>"
        )
    # Custodian rows (union of current + prior custodians)
    all_custs = sorted(set(d["custodian_totals"]) | set(p_cust.keys()))
    for cust in all_custs:
        cur_v = d["custodian_totals"].get(cust, 0.0)
        pri_v = p_cust.get(cust, 0.0) if prior else None
        new_tag = ""
        if prior and cust not in p_cust:
            new_tag = " <span class='pchange-new'>NEW</span>"
        change_rows.append(
            f"<tr><td>Custodian \u2014 {esc(cust)}{new_tag}</td>"
            f"<td class='num'>{fmt(cur_v)}</td>"
            f"<td class='num'>{fmt(pri_v) if prior else '&mdash;'}</td>"
            f"{_delta_cell(cur_v, pri_v)}</tr>"
        )
    portfolio_change_rows = "\n".join(change_rows)

    # Conditional Portfolio Change section + section numbering.
    # When a prior statement IS supplied, Portfolio Change is Section 05,
    # Position Detail 06, Disclaimer 07. When NO prior is supplied, the
    # Portfolio Change page is omitted entirely (no orphan em-dash table)
    # and the remaining sections shift up to 05 / 06.
    if prior:
        portfolio_change_section = (
            "<div class=\"page-break\">\n"
            "  <div class=\"section-eyebrow\">Section 05</div>\n"
            "  <h2>Portfolio Change</h2>\n"
            f"  <div class=\"pchange-caption\">{change_caption}</div>\n"
            "  <table class=\"pchange\">\n"
            "    <thead>\n"
            "      <tr>\n"
            "        <td>Line item</td>\n"
            "        <td class=\"r\">Current (USD)</td>\n"
            "        <td class=\"r\">Prior (USD)</td>\n"
            "        <td class=\"r\">&Delta; USD</td>\n"
            "        <td class=\"r\">&Delta; %</td>\n"
            "      </tr>\n"
            "    </thead>\n"
            "    <tbody>\n"
            f"      {portfolio_change_rows}\n"
            "    </tbody>\n"
            "  </table>\n"
            "</div>"
        )
        toc_change_row = (
            "<tr style=\"border-bottom:1px solid var(--hair);\">\n"
            "        <td style=\"font-size:10pt; padding:12px 0; color:var(--teal-700); font-weight:600;\">05 &nbsp; Portfolio Change</td>\n"
            "        <td style=\"text-align:right; font-size:10pt; padding:12px 0; color:var(--muted);\">6</td>\n"
            "      </tr>\n      "
        )
        sec_position_no   = "06"
        sec_disclaimer_no = "07"
        toc_position_page = "7"
    else:
        portfolio_change_section = ""
        toc_change_row    = ""
        sec_position_no   = "05"
        sec_disclaimer_no = "06"
        toc_position_page = "6"

    # Build full replacement dict ───────────────────────────
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
        "cover_delta_badge":            cover_delta_html,
        "portfolio_change_section":     portfolio_change_section,
        "toc_change_row":               toc_change_row,
        "sec_position_no":              sec_position_no,
        "sec_disclaimer_no":            sec_disclaimer_no,
        "toc_position_page":            toc_position_page,
        "prior_statement_date":         esc(p_date) if prior else "\u2014",
        "logo_data_uri":                logo_data_uri,
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
    parser.add_argument("--prior",   default=None,
                        help="Optional path to a PRIOR period CSV for period-over-period deltas")
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

    prior_data = None
    if args.prior:
        print(f"      Prior snapshot : loading {args.prior} …")
        prior_data = load_and_compute(args.prior)
        print(f"      Prior total    : USD {fmt(prior_data['grand_total'])} "
              f"({prior_data['stmt_date_display']})")
        print(f"      Net change     : USD {fmt(data['grand_total'] - prior_data['grand_total'])}")

    print(f"[2/3] Filling template …")
    filled_html = generate_html(data, args.template, args.client, args.rm, prior=prior_data)

    with open(out_html, "w", encoding="utf-8") as f:
        f.write(filled_html)
    print(f"      HTML → {out_html}")

    print(f"[3/3] Rendering PDF …")
    WPhtml(filename=out_html).write_pdf(out_pdf)
    print(f"      PDF  → {out_pdf}")

    return out_pdf


if __name__ == "__main__":
    main()
