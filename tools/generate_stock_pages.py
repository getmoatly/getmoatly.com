#!/usr/bin/env python3
"""
Create a stock/<slug>/index.html for every company in scores.json.

    python3 tools/generate_stock_pages.py            # create missing pages
    python3 tools/generate_stock_pages.py --dry-run  # report only
    python3 tools/generate_stock_pages.py --overwrite # rebuild existing too

WHY THIS EXISTS, ALONGSIDE refresh.py
  refresh.py updates the numbers on pages that already exist. It cannot create
  one. Twenty-two pages were made by hand; doing another seven hundred that way
  is not a plan. This builds them, and refresh.py keeps them current afterwards.

THE PROSE IS DERIVED, NOT WRITTEN
  The hand-made pages carry MoatlyAI paragraphs that quote figures. refresh.py
  deliberately does not touch them, and its docstring explains the consequence:
  "read those paragraphs before publishing or the tables and the prose will
  contradict each other." That has already happened — AAPL's prose says a
  margin of safety score of 14 while its own panel says 13, because the prose
  was written against an earlier scoring run.

  At twenty-two pages that is a proofreading job. At seven hundred it is a
  guarantee of public, indexed contradictions. So every sentence here is
  computed from the same row that fills the tables. It cannot drift, because
  there is nothing to drift from: regenerate and the prose moves with the
  numbers.

  The cost is that it reads as assembled rather than argued. Variety comes from
  banding the figures and selecting phrasing by a hash of the ticker, so a page
  is stable across runs — the same company always gets the same construction —
  while neighbouring companies do not read identically.

  Existing pages are skipped unless --overwrite, so the hand-written twenty-two
  keep their real prose.

ONLY valuation_state == 'full'
  A page with three of four price cards reading N/A is thin, ranks poorly, and
  sells nothing. Partial rows are listed in the report and left alone.
"""

import json
import os
import re
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DRY = "--dry-run" in sys.argv
OVERWRITE = "--overwrite" in sys.argv

APP_STORE = "https://apps.apple.com/us/app/moatly/id6768135444"
PLAY_STORE = "https://play.google.com/store/apps/details?id=com.getmoatly.app"
SUBSTACK = "https://tirthal.substack.com/subscribe"


# ── formatting ────────────────────────────────────────────────────────────────

def money(v):
    if v is None:
        return "N/A"
    v = float(v)
    return f"${v:,.2f}"


def cap(v):
    """Market cap, short form. Matches the hand-made pages: $4.9T, $110.9B."""
    if not v:
        return "N/A"
    v = float(v)
    for div, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if v >= div:
            return f"${v/div:.1f}{suf}"
    return f"${v:,.0f}"


def pct(v, places=1):
    return "N/A" if v is None else f"{float(v)*100:.{places}f}%"


def mult(v, places=1):
    return "N/A" if v is None else f"{float(v):.{places}f}x"


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#x27;"))


def slug_for(ticker):
    """AAPL -> aapl, BRK-B -> brk-b, BF.B -> bf-b."""
    return ticker.lower().replace(".", "-")


def band(score, hi=80, mid=60, lo=40):
    if score is None:
        return "unknown"
    if score >= hi:
        return "high"
    if score >= mid:
        return "good"
    if score >= lo:
        return "moderate"
    return "low"


def pick(ticker, salt, options):
    """Deterministic choice: the same company always reads the same way."""
    h = 0
    for ch in f"{ticker}:{salt}":
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return options[h % len(options)]


# ── derived prose ─────────────────────────────────────────────────────────────

MOAT_WORD = {"high": "wide", "good": "solid", "moderate": "moderate",
             "low": "narrow", "unknown": "unscored"}
MGMT_WORD = {"high": "exceptional", "good": "strong", "moderate": "mixed",
             "low": "weak", "unknown": "unscored"}


def para_business(r):
    t, name = r["ticker"], r["company_name"]
    mg, mo = r.get("mgmt_score"), r.get("moat_score")
    roic, gm, de = r.get("roic"), r.get("gross_margin"), r.get("debt_to_equity")

    lead = pick(t, "biz", [
        f"{name} scores {mg} for management and {mo} for moat.",
        f"On the four Ms, {name} earns {mg} for management and {mo} for moat.",
        f"{name} is scored {mo} for moat and {mg} for management.",
    ])

    bits = []
    if roic is not None:
        bits.append(f"a return on invested capital of {pct(roic)}")
    if gm is not None:
        bits.append(f"a gross margin of {pct(gm)}")
    if bits:
        verdict = {
            "high": "figures that describe a business turning capital into profit efficiently",
            "good": "figures that describe a business earning a respectable return on what it employs",
            "moderate": "figures that describe returns closer to the cost of the capital behind them",
            "low": "figures that describe capital earning less than it costs to employ",
            "unknown": "figures reported from the statements",
        }[band(mg)]
        lead += f" The management score rests on {' and '.join(bits)} — {verdict}."

    if de is not None:
        d = float(de)
        if d > 2:
            lead += (f" The balance sheet carries debt at {mult(d, 2)} equity, "
                     "which magnifies both the returns above and the risk beneath them.")
        elif d < 0:
            lead += (" Shareholder equity is negative, so the debt-to-equity "
                     "figure is not meaningful here — usually the mark of heavy "
                     "buybacks rather than distress, but worth checking.")
        else:
            lead += f" Debt sits at {mult(d, 2)} equity."

    mo_w, mg_w = MOAT_WORD[band(mo)], MGMT_WORD[band(mg)]
    if band(mo) != band(mg):
        lead += pick(t, "tension", [
            f" The {mo_w} moat and {mg_w} management are not the same judgement, "
            "and the gap between them is where the argument about this business sits.",
            f" A {mo_w} moat alongside {mg_w} management is a combination worth "
            "understanding before the price matters at all.",
        ])
    return lead


def para_price(r):
    t = r["ticker"]
    price = r.get("price_at_scoring")
    gu, gc = r.get("growth_used"), r.get("growth_computed")
    pe = r.get("future_pe_used")

    s = pick(t, "price", [
        "The price anchors are built on deliberately conservative assumptions.",
        "Three independent anchors set the price worth paying.",
        "The valuation runs on assumptions chosen to understate rather than flatter.",
    ])

    if gu is not None and gc is not None and float(gc) > float(gu) + 0.001:
        s += (f" Growth is modelled at {pct(gu)} a year, below the {pct(gc)} "
              "actually measured over the last decade — the model caps growth "
              "rather than extrapolating a good run forwards.")
    elif gu is not None:
        s += f" Growth is modelled at {pct(gu)} a year, the rate measured from the filings."

    if pe is not None:
        s += f" The exit multiple assumed is {mult(pe)}."

    anchors = []
    for key, label in (("sticker_price", "an intrinsic value of"),
                       ("ten_cap_price", "a 10-CAP price of"),
                       ("pbt_price", "a payback time price of")):
        if r.get(key) is not None:
            anchors.append(f"{label} {money(r[key])}")
    if anchors:
        s += (" That produces " + ", ".join(anchors[:-1]) +
              (" and " if len(anchors) > 1 else "") + anchors[-1] +
              f", with the value zone set at the highest of the three, {money(r.get('buy_price'))}.")

    if price is not None and r.get("buy_price"):
        p, b = float(price), float(r["buy_price"])
        gap = (p - b) / b
        if p <= b:
            s += f" Today's price of {money(p)} sits inside that zone."
        elif gap < 0.25:
            s += f" Today's price of {money(p)} sits {gap*100:.0f}% above it."
        else:
            s += f" Today's price of {money(p)} is {gap*100:.0f}% above it."
    return s


def para_verdict(r):
    t, name = r["ticker"], r["company_name"]
    price, buy = r.get("price_at_scoring"), r.get("buy_price")
    mos, mo, mg = r.get("mos_score"), r.get("moat_score"), r.get("mgmt_score")
    if price is None or not buy:
        return ("What the business is worth and what it costs are separate "
                "questions. The figures above answer the first; the price "
                "answers the second.")

    p, b = float(price), float(buy)
    quality = (mo or 0) + (mg or 0)

    if p <= b and quality >= 140:
        return pick(t, "v", [
            f"A business scoring this well, trading inside its value zone, is the "
            f"combination the framework exists to find — which is also the reason "
            f"to check why the market disagrees. A margin of safety score of {mos} "
            f"describes the cushion, not the certainty.",
            f"{name} currently reads as a strong business at a price the model "
            f"supports. That is rare enough to warrant asking what the market "
            f"sees that these figures do not.",
        ])
    if p <= b:
        return (f"The price sits inside the value zone, but the business scores "
                f"{mo} for moat and {mg} for management. Cheap and good are "
                f"different tests, and only one of them is passed here.")
    if quality >= 140:
        return pick(t, "v", [
            f"The operating figures describe a business performing at a high level; "
            f"the price asks for that performance to continue and then some. A "
            f"margin of safety score of {mos} is the measure of how little room "
            f"that leaves for being wrong.",
            f"A wonderful business at the wrong price is still the wrong price. "
            f"{name} scores well on the business and poorly on the entry point, "
            f"which is the most common shape in a long bull market.",
        ])
    return (f"The price is above the value zone and the business scores {mo} for "
            f"moat and {mg} for management. Neither test argues for paying up here.")


# ── page ──────────────────────────────────────────────────────────────────────

def score_class(v):
    if v is None:
        return ""
    return " hi" if v >= 70 else (" mid" if v >= 40 else " lo")


def peers_for(row, rows, n=4):
    """Same industry, nearest market cap. Internal links are the point: seven
    hundred orphan pages crawl badly, a connected graph does not."""
    same = [r for r in rows
            if r.get("industry") == row.get("industry")
            and r["ticker"] != row["ticker"]
            and r.get("valuation_state") == "full"]
    if len(same) < n:
        same += [r for r in rows
                 if r.get("sector") and r.get("sector") == row.get("sector")
                 and r["ticker"] != row["ticker"]
                 and r not in same and r.get("valuation_state") == "full"]
    mc = float(row.get("market_cap") or 0)
    same.sort(key=lambda r: abs(float(r.get("market_cap") or 0) - mc))
    return same[:n]


def build_page(r, rows):
    t, name = r["ticker"], r["company_name"]
    slug = slug_for(t)
    mo, mg, mos = r.get("moat_score"), r.get("mgmt_score"), r.get("mos_score")
    asof = str(r.get("scored_at") or "")[:10]

    title = f"{name} ({t}) intrinsic value, moat score and buy price | Moatly"
    desc = (f"What is {name} ({t}) worth? Moat {mo}, management {mg}, "
            f"margin of safety {mos}. Buy price and intrinsic value from ten "
            f"years of financial statements.")

    peer_rows = "".join(
        f'<tr><td><a href="/stock/{slug_for(p["ticker"])}/">{esc(p["company_name"])} '
        f'({esc(p["ticker"])})</a></td><td class="n">{p.get("moat_score")}</td>'
        f'<td class="n">{money(p.get("buy_price"))}</td></tr>'
        for p in peers_for(r, rows))

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{esc(title)}</title>
<meta name="description" content="{esc(desc)}">
<link rel="canonical" href="https://getmoatly.com/stock/{slug}/">
<meta property="og:type" content="article">
<meta property="og:url" content="https://getmoatly.com/stock/{slug}/">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(desc)}">
<meta property="og:image" content="https://getmoatly.com/og-image.png">
<meta property="og:site_name" content="Moatly">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{esc(title)}">
<meta name="twitter:description" content="{esc(desc)}">
<meta name="twitter:image" content="https://getmoatly.com/og-image.png">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,300;0,400;0,500;1,400&family=Poppins:wght@600&family=Syne:wght@400;500;600;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/stock/stock.css">
</head>
<body>
<nav>
  <a class="nav-logo" href="/index.html"><span class="logo-text">moatly&trade;</span></a>
  <div class="nav-links">
    <a class="nav-link" href="/index.html#framework">The Framework</a>
    <a class="nav-link" href="/index.html#moatlyai">MoatlyAI</a>
    <a class="nav-link" href="/stocks.html">Stocks</a>
    <a class="nav-link" href="/index.html#pricing">Pricing</a>
    <a class="nav-link" href="/about.html">About</a>
    <a class="nav-link" href="/faq.html">FAQ</a>
    <a class="nav-cta" href="{APP_STORE}">Download</a>
  </div>
</nav>

<div class="wrap">
<div class="crumb"><a href="/index.html">Moatly</a> / <a href="/stocks.html">Stocks</a> / {esc(t)}</div>
<h1>{esc(name)} <em>({esc(t)})</em> — what is it worth?</h1>
<div class="meta">{money(r.get('price_at_scoring'))} &middot; {cap(r.get('market_cap'))} market cap &middot; {esc(r.get('industry') or '')}</div>
<div class="asof">Scored from reported financial statements. Data as of {asof}.</div>
<div class="scores">
<div class="sc"><div class="sc-l">Moat</div><div class="sc-v{score_class(mo)}">{mo}</div><div class="sc-n">Durability of advantage</div></div>
<div class="sc"><div class="sc-l">Management</div><div class="sc-v{score_class(mg)}">{mg}</div><div class="sc-n">Returns and capital use</div></div>
<div class="sc"><div class="sc-l">Margin of safety</div><div class="sc-v{score_class(mos)}">{mos}</div><div class="sc-n">Price against value</div></div>
<div class="sc"><div class="sc-l">Buy price</div><div class="sc-v">{money(r.get('buy_price'))}</div><div class="sc-n">Consensus of three methods</div></div>
</div>
<div class="prices">
<div class="pr"><div class="pr-l">Intrinsic value</div><div class="pr-v">{money(r.get('sticker_price'))}</div><div class="pr-n">What the business is worth</div></div>
<div class="pr"><div class="pr-l">Margin of safety price</div><div class="pr-v">{money(r.get('mos_price'))}</div><div class="pr-n">Intrinsic value less 50%</div></div>
<div class="pr"><div class="pr-l">10-CAP price</div><div class="pr-v">{money(r.get('ten_cap_price'))}</div><div class="pr-n">Ten times owner earnings</div></div>
<div class="pr"><div class="pr-l">Payback time price</div><div class="pr-v">{money(r.get('pbt_price'))}</div><div class="pr-n">Eight years of free cash flow</div></div>
</div>
<div class="ai">
<div class="ai-label">What the numbers say</div>
<p>{esc(para_business(r))}</p>
<p>{esc(para_price(r))}</p>
<p>{esc(para_verdict(r))}</p>
</div>
<h2>Key figures</h2>
<table><tbody>
<tr><td>Price</td><td class="n">{money(r.get('price_at_scoring'))}</td></tr>
<tr><td>Market cap</td><td class="n">{cap(r.get('market_cap'))}</td></tr>
<tr><td>P/E ratio</td><td class="n">{mult(r.get('pe_ratio'))}</td></tr>
<tr><td>Return on invested capital</td><td class="n">{pct(r.get('roic'))}</td></tr>
<tr><td>Gross margin</td><td class="n">{pct(r.get('gross_margin'))}</td></tr>
<tr><td>Debt to equity</td><td class="n">{mult(r.get('debt_to_equity'), 2)}</td></tr>
<tr><td>Free cash flow yield</td><td class="n">{pct(r.get('fcf_yield'))}</td></tr>
<tr><td>Growth rate used</td><td class="n">{pct(r.get('growth_used'))}</td></tr>
<tr><td>Growth rate measured</td><td class="n">{pct(r.get('growth_computed'))}</td></tr>
<tr><td>Exit multiple assumed</td><td class="n">{mult(r.get('future_pe_used'))}</td></tr>
</tbody></table>

<h2>How it compares</h2>
<table><tbody>
<tr><td><strong>Company</strong></td><td class="n"><strong>Moat</strong></td><td class="n"><strong>Buy price</strong></td></tr>
{peer_rows}
</tbody></table>

<div class="capture">
<h3>Be in the know</h3>
<p>Subscribe to our Substack for deeper insights on value plays.</p>
<a class="btn" href="{SUBSTACK}">Subscribe free</a>
</div>
<h2>Run your own assumptions</h2>
<p>Every number above is built on assumptions that can be changed. In Moatly you can move the growth rate, the exit multiple and the margin of safety and watch every figure recalculate, so you are testing your own view of {esc(name)} rather than accepting ours.</p>
<p><a class="btn" href="{APP_STORE}">Get Moatly on iOS</a> &nbsp; <a class="btn" href="{PLAY_STORE}">Get Moatly on Android</a></p>
<div class="disc">Not investment advice. Estimates only, built on stated assumptions that could be wrong. Figures are drawn from reported financial statements and were current as of the date shown. Do your own research.</div>
</div>
<footer>
  <div class="footer-logo">moatly&trade;</div>
  <div class="footer-links">
    <a href="/index.html#framework">The Framework</a>
    <a href="/stocks.html">Stocks</a>
    <a href="/index.html#pricing">Pricing</a>
    <a href="/about.html">About</a>
  </div>
  <div class="footer-copy">&copy; 2026 Moatly LLC</div>
</footer>
</body>
</html>
"""


# ── sitemap ───────────────────────────────────────────────────────────────────

def write_index(rows):
    """Rewrite stocks.html, the hub page.

    Without this the 690 generated pages are reachable only from the sitemap
    and from each other's peer tables — there is no path to them from the site
    itself, which is how a corpus this size ends up crawled slowly and ranked
    badly. The hub had been hand-maintained and still listed the original 20.

    Sorted by market cap within each sector rather than alphabetically, so the
    names a reader recognises are the ones they see first.
    """
    path = os.path.join(ROOT, "stocks.html")
    if not os.path.exists(path):
        print("  stocks.html: not found, skipping")
        return 0
    with open(path) as f:
        s = f.read()

    by_sector = {}
    for r in rows:
        by_sector.setdefault(r.get("sector") or "Other", []).append(r)

    out = []
    for sector in sorted(by_sector):
        items = sorted(by_sector[sector],
                       key=lambda r: -float(r.get("market_cap") or 0))
        out.append(f"<h2>{esc(sector)}</h2>")
        out.append('<div class="idx">')
        for r in items:
            out.append(f'<a href="/stock/{slug_for(r["ticker"])}/">'
                       f'<b>{esc(r["ticker"])}</b>'
                       f'<span>Moat {r.get("moat_score")}</span></a>')
        out.append("</div>")
    block = "\n".join(out)

    # Replace everything between the intro paragraph and the footer.
    start = s.find("<h2>")
    end = s.find("</div>\n<footer")
    if end < 0:
        end = s.rfind("</div>", 0, s.find("<footer"))
    if start < 0 or end < 0:
        print("  stocks.html: anchors not found, left untouched")
        return 0
    s = s[:start] + block + "\n" + s[end + len("</div>"):]

    # The count in the intro and the meta description.
    n = len(rows)
    s = re.sub(r"\b\d+ companies, each scored",
               f"{n} companies, each scored", s)
    s = re.sub(r"margin of safety scores for \d+ companies",
               f"margin of safety scores for {n} companies", s)

    if not DRY:
        with open(path, "w") as f:
            f.write(s)
    return n


def write_sitemap(slugs):
    today = date.today().isoformat()
    statics = ["", "about.html", "faq.html", "stocks.html",
               "privacy.html", "terms.html", "delete-account.html"]
    urls = [f"  <url><loc>https://getmoatly.com/{p}</loc><lastmod>{today}</lastmod></url>"
            for p in statics]
    urls += [f"  <url><loc>https://getmoatly.com/stock/{s}/</loc><lastmod>{today}</lastmod></url>"
             for s in sorted(slugs)]
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
           + "\n".join(urls) + "\n</urlset>\n")
    if not DRY:
        with open(os.path.join(ROOT, "sitemap.xml"), "w") as f:
            f.write(xml)
    return len(urls)


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    with open(os.path.join(HERE, "scores.json")) as f:
        rows = json.load(f)

    full = [r for r in rows if r.get("valuation_state") == "full"]
    partial = [r for r in rows if r.get("valuation_state") != "full"]

    print(f"scores.json: {len(rows)} rows, {len(full)} full, {len(partial)} partial")
    if partial:
        print(f"  skipping {len(partial)} partial rows "
              f"(a page with three N/A price cards is a thin page)")

    created = skipped = rebuilt = 0
    all_slugs = []
    for r in full:
        slug = slug_for(r["ticker"])
        all_slugs.append(slug)
        d = os.path.join(ROOT, "stock", slug)
        page = os.path.join(d, "index.html")
        exists = os.path.exists(page)
        if exists and not OVERWRITE:
            skipped += 1
            continue
        if not DRY:
            os.makedirs(d, exist_ok=True)
            with open(page, "w") as f:
                f.write(build_page(r, full))
        if exists:
            rebuilt += 1
        else:
            created += 1

    n = write_sitemap(all_slugs)
    listed = write_index(full)
    verb = "would write" if DRY else "wrote"
    print(f"\ncreated {created}, rebuilt {rebuilt}, skipped {skipped} existing")
    print(f"{verb} sitemap.xml with {n} urls")
    print(f"{verb} stocks.html listing {listed} companies")
    if skipped and not OVERWRITE:
        print("  (existing pages keep their hand-written prose; "
              "--overwrite replaces it with derived prose)")


if __name__ == "__main__":
    main()
