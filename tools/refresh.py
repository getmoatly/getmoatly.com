#!/usr/bin/env python3
"""
Refresh every number on the Moatly marketing site from stock_scores.

    python3 tools/refresh.py            # rewrite pages, print a change report
    python3 tools/refresh.py --dry-run  # report only, touch nothing

INPUT
  tools/scores.json — the output of this query, pasted straight in:

    select ticker, company_name, industry, price_at_scoring, market_cap,
           moat_score, mgmt_score, mos_score,
           sticker_price, mos_price, ten_cap_price, pbt_price, buy_price,
           growth_used, growth_computed, future_pe_used,
           pe_ratio, roic, gross_margin, debt_to_equity, fcf_yield,
           valuation_state, scored_at
    from stock_scores where ticker in (...) order by ticker;

WHAT IT REWRITES, per stock/<slug>/index.html
  · <div class="meta">      price, market cap, industry
  · <div class="asof">      the scored date
  · the four score cards    moat / management / margin of safety / buy price
  · the four price cards    intrinsic / MOS / 10-CAP / payback
  · Key figures table       ten rows
  · title + og + twitter    the "Moat X, management Y, margin of safety Z" string
  · How it compares         peer rows, read from the peer's own row in scores.json
  · stocks.html             the moat score beside each ticker

WHAT IT DOES NOT TOUCH
  The MoatlyAI prose. Those paragraphs quote specific figures, and no script can
  rewrite an argument. The report at the end lists, per page, which quoted
  numbers moved — read those paragraphs before publishing or the tables and the
  prose will contradict each other.

VALUATION STATE drives which anchors are printed, exactly as the app does:
  full           all four
  earnings_only  intrinsic + MOS; FCF anchors N/A
  fcf_only       10-CAP + payback; earnings anchors N/A
  none           nothing; all four N/A
  not_computable nothing (measured decline; growth is an artefact, not an estimate)

Stale-anchor note: a row wiped by isPermanentError keeps its old ten_cap_price
and pbt_price, because the wipe only clears sticker/mos/buy. GOOG is in that
state today. Printing by state rather than by value is what stops those stale
numbers reaching the page.
"""

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DRY = "--dry-run" in sys.argv

with open(os.path.join(HERE, "scores.json")) as f:
    ROWS = json.load(f)

BY_TICKER = {r["ticker"]: r for r in ROWS}

# stock/<slug>/ -> ticker. Only brk-b needs help; the rest are the upper-case slug.
def slug_to_ticker(slug):
    return "BRK-B" if slug == "brk-b" else slug.upper()


# ---------- formatting ----------

NA = "N/A"


def money(v):
    return NA if v is None else f"${float(v):,.2f}"


def mcap(v):
    if v is None:
        return NA
    v = float(v)
    if v >= 1e12:
        return f"${v / 1e12:.1f}T"
    if v >= 1e9:
        return f"${v / 1e9:.1f}B"
    return f"${v / 1e6:.1f}M"


def pct(v, dp=1):
    return NA if v is None else f"{float(v) * 100:.{dp}f}%"


def gross_margin_display(v):
    """
    FMP returns grossProfitMargin = 1 for companies that report no cost of
    revenue line — Mastercard is one. Printing "100.0%" states something the
    filing does not, so it reads as N/A instead.
    """
    if v is None or float(v) >= 0.999:
        return NA
    return pct(v)


def mult(v, dp=1):
    return NA if v is None else f"{float(v):.{dp}f}x"


def score_class(v):
    # Matches the existing pages: hi >= 80, mid >= 40, lo below.
    return "hi" if v >= 80 else ("mid" if v >= 40 else "lo")


def as_of(scored_at):
    return str(scored_at)[:10]


def anchors(row):
    """Which of the four price anchors this row is allowed to print."""
    st = row["valuation_state"]
    earnings = st in ("full", "earnings_only")
    fcf = st in ("full", "fcf_only")
    return {
        "Intrinsic value": money(row["sticker_price"]) if earnings else NA,
        "Margin of safety price": money(row["mos_price"]) if earnings else NA,
        "10-CAP price": money(row["ten_cap_price"]) if fcf else NA,
        "Payback time price": money(row["pbt_price"]) if fcf else NA,
    }


def buy_display(row):
    bp = row["buy_price"]
    return money(bp) if (bp and float(bp) > 0) else NA


# ---------- surgical replacement ----------

changes = []          # (page, field, old, new)
prose_warnings = []   # (page, [figures that moved])


def sub_once(html, pattern, repl, page, field, flags=0):
    """Replace one anchored field and record the change."""
    global changes
    m = re.search(pattern, html, flags)
    if not m:
        changes.append((page, field, "NOT FOUND", "-"))
        return html
    old = m.group("val")
    if old != repl:
        changes.append((page, field, old, repl))
    return html[: m.start("val")] + repl + html[m.end("val"):]


def refresh_page(slug):
    ticker = slug_to_ticker(slug)
    row = BY_TICKER.get(ticker)
    path = os.path.join(ROOT, "stock", slug, "index.html")
    if row is None:
        print(f"  {slug}: no row in scores.json, skipped")
        return
    if not os.path.exists(path):
        print(f"  {slug}: no index.html, skipped")
        return

    with open(path) as f:
        html = f.read()

    # Redirect stubs and any other non-scored page have no score block. Skip
    # them rather than reporting every field as missing. stock/goog is one:
    # GOOG is is_active = false in curated_stocks (Alphabet is scored under
    # GOOGL), so its page canonicalises to GOOGL instead of carrying numbers.
    if '<div class="scores">' not in html:
        print(f"  {slug}: not a scored page, skipped")
        return

    before = html
    page = f"stock/{slug}"

    moat, mgmt, mos = row["moat_score"], row["mgmt_score"], row["mos_score"]

    # 1. meta line: price · market cap · industry
    industry = row["industry"].replace("&", "&amp;")
    meta_line = f"{money(row['price_at_scoring'])} &middot; {mcap(row['market_cap'])} market cap &middot; {industry}"
    html = sub_once(html, r'<div class="meta">(?P<val>.*?)</div>', meta_line, page, "meta line")

    # 2. as-of date
    html = sub_once(
        html,
        r'(?<=Data as of )(?P<val>\d{4}-\d{2}-\d{2})(?=\.)',
        as_of(row["scored_at"]),
        page,
        "as-of date",
    )

    # 3. score cards
    for label, val in (("Moat", moat), ("Management", mgmt), ("Margin of safety", mos)):
        pat = (
            r'<div class="sc-l">' + re.escape(label)
            + r'</div><div class="sc-v (?P<cls>[a-z]+)">(?P<val>[^<]*)</div>'
        )
        m = re.search(pat, html)
        if m:
            if m.group("val") != str(val):
                changes.append((page, label, m.group("val"), str(val)))
            html = (
                html[: m.start("cls")] + score_class(val)
                + html[m.end("cls"): m.start("val")] + str(val)
                + html[m.end("val"):]
            )
        else:
            changes.append((page, label, "NOT FOUND", "-"))

    html = sub_once(
        html,
        r'<div class="sc-l">Buy price</div><div class="sc-v">(?P<val>[^<]*)</div>',
        buy_display(row),
        page,
        "Buy price",
    )

    # 4. price cards
    for label, val in anchors(row).items():
        html = sub_once(
            html,
            r'<div class="pr-l">' + re.escape(label) + r'</div><div class="pr-v">(?P<val>[^<]*)</div>',
            val,
            page,
            label,
        )

    # 5. key figures table
    figures = [
        ("Price", money(row["price_at_scoring"])),
        ("Market cap", mcap(row["market_cap"])),
        ("P/E ratio", mult(row["pe_ratio"])),
        ("Return on invested capital", pct(row["roic"])),
        ("Gross margin", gross_margin_display(row["gross_margin"])),
        ("Debt to equity", mult(row["debt_to_equity"], 2)),
        ("Free cash flow yield", pct(row["fcf_yield"])),
        ("Growth rate used", pct(row["growth_used"])),
        ("Growth rate measured", pct(row["growth_computed"])),
        ("Exit multiple assumed", mult(row["future_pe_used"])),
    ]
    for label, val in figures:
        html = sub_once(
            html,
            r'<tr><td>' + re.escape(label) + r'</td><td class="n">(?P<val>[^<]*)</td></tr>',
            val,
            page,
            label,
        )

    # 6. the score string inside <title>, og: and twitter: descriptions
    new_str = f"Moat {moat}, management {mgmt}, margin of safety {mos}"
    html, n = re.subn(r"Moat \d+, management \d+, margin of safety \d+", new_str, html)
    if n == 0:
        changes.append((page, "meta score string", "NOT FOUND", "-"))

    # 7. How it compares — each peer row carries that peer's own three scores
    def peer_row(m):
        peer = BY_TICKER.get(slug_to_ticker(m.group("slug")))
        if peer is None:
            return m.group(0)
        return (
            f'{m.group("head")}<td class="n">{peer["moat_score"]}</td>'
            f'<td class="n">{peer["mgmt_score"]}</td>'
            f'<td class="n">{peer["mos_score"]}</td></tr>'
        )

    # Trailing slash is optional: stock/goog links its peer as /stock/googl.
    html = re.sub(
        r'(?P<head><tr><td><a href="/stock/(?P<slug>[a-z\-]+)/?">[^<]*</a></td>)'
        r'<td class="n">\d+</td><td class="n">\d+</td><td class="n">\d+</td></tr>',
        peer_row,
        html,
    )

    # 8. flag prose that quotes a figure which moved
    quoted = []
    ai = re.search(r'<div class="ai">(.*?)</div>\s*<h2', html, re.S)
    if ai:
        body = ai.group(1)
        for _, field, old, new in [c for c in changes if c[0] == page]:
            if old not in ("NOT FOUND", new) and old and old in body:
                quoted.append(f"{field}: {old} -> {new}")
    if quoted:
        prose_warnings.append((page, quoted))

    if html != before and not DRY:
        with open(path, "w") as f:
            f.write(html)


def refresh_index():
    path = os.path.join(ROOT, "stocks.html")
    with open(path) as f:
        html = f.read()
    before = html

    def one(m):
        row = BY_TICKER.get(slug_to_ticker(m.group("slug")))
        if row is None:
            return m.group(0)
        if str(row["moat_score"]) != m.group("val"):
            changes.append(("stocks.html", m.group("slug").upper(), m.group("val"), str(row["moat_score"])))
        return f'{m.group("head")}{row["moat_score"]}</span></a>'

    html = re.sub(
        r'(?P<head><a href="/stock/(?P<slug>[a-z\-]+)/"><b>[A-Z\-\.]+</b><span>Moat )(?P<val>\d+)</span></a>',
        one,
        html,
    )
    if html != before and not DRY:
        with open(path, "w") as f:
            f.write(html)


# ---------- run ----------

print("Refreshing ticker pages...")
slugs = sorted(
    d for d in os.listdir(os.path.join(ROOT, "stock"))
    if os.path.isdir(os.path.join(ROOT, "stock", d))
)
for slug in slugs:
    refresh_page(slug)

print("Refreshing stocks.html...")
refresh_index()

print(f"\n{'DRY RUN — nothing written' if DRY else 'Written'}. {len(changes)} field(s) changed.\n")

missing = [c for c in changes if c[2] == "NOT FOUND"]
if missing:
    print("FIELDS NOT FOUND (markup differs from the template):")
    for page, field, _, _ in missing:
        print(f"  {page}: {field}")
    print()

current = None
for page, field, old, new in changes:
    if old == "NOT FOUND":
        continue
    if page != current:
        print(page)
        current = page
    print(f"    {field:<28} {old:>12}  ->  {new}")

if prose_warnings:
    print("\nPROSE NEEDS REVIEW — these pages quote figures that moved:")
    for page, items in prose_warnings:
        print(f"  {page}")
        for it in items:
            print(f"      {it}")

print("\nStates that suppressed anchors:")
for r in ROWS:
    if r["valuation_state"] != "full":
        print(f"  {r['ticker']:<6} {r['valuation_state']}")
