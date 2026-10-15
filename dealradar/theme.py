"""Look and feel. The one memorable element is the price-tag card: a chamfered corner, a punched
hole, a hard ink shadow and a rotated sticker. Everything around it stays quiet.

Palette  paper #EDF0EE | ink #12202B | slate #5C6B73 | sticker #FFD43B | drop #0B8F5E | rise #C8372D
Type     Bricolage Grotesque for names and prices, Public Sans for everything else
"""
from __future__ import annotations

from html import escape

import streamlit as st

from .analysis import money, store_name, time_ago

INK, PAPER, SLATE, GREEN, RED = "#12202B", "#EDF0EE", "#5C6B73", "#0B8F5E", "#C8372D"

CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,500..800&family=Public+Sans:wght@400;500;600&display=swap');
:root {{ --paper:{PAPER}; --ink:{INK}; --slate:{SLATE}; --green:{GREEN}; --red:{RED}; --sticker:#FFD43B; --line:#C9D1CD; }}
.stApp, .stApp p, .stApp li, .stApp label, .stApp input, .stApp textarea, .stApp button, .stApp summary {{
    font-family: 'Public Sans', system-ui, -apple-system, 'Segoe UI', sans-serif;
}}
.stApp h1, .stApp h2, .stApp h3 {{ font-family: 'Bricolage Grotesque', 'Public Sans', sans-serif; letter-spacing: -0.02em; color: var(--ink); }}
.block-container {{ padding-top: 2.2rem; max-width: 1180px; }}

.dr-title {{ font: 800 46px/1 'Bricolage Grotesque','Public Sans',sans-serif; letter-spacing:-0.03em; color:var(--ink); margin:0; }}
.dr-sub {{ color: var(--slate); font-size: 16px; margin: 10px 0 26px; max-width: 62ch; line-height:1.5; }}

.dr-stats {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:14px; margin-bottom: 22px; }}
.dr-stat {{ border-left: 3px solid var(--ink); padding: 2px 0 2px 12px; }}
.dr-stat b {{ display:block; font: 700 30px/1.1 'Bricolage Grotesque',sans-serif; color:var(--ink); }}
.dr-stat span {{ color: var(--slate); font-size: 13.5px; }}

.dr-tagwrap {{ filter: drop-shadow(4px 4px 0 var(--ink)); margin: 4px 6px 8px 0; }}
.dr-tag {{ background:#fff; position:relative; padding:20px 20px 16px 24px; min-height:262px;
    clip-path: polygon(30px 0, 100% 0, 100% 100%, 0 100%, 0 30px); color: var(--ink); }}
.dr-hole {{ position:absolute; top:10px; left:10px; width:9px; height:9px; border-radius:50%;
    background: var(--paper); box-shadow: inset 0 0 0 1.5px var(--ink); }}
.dr-sticker {{ position:absolute; top:12px; right:12px; transform: rotate(4deg); background: var(--sticker);
    border:1.5px solid var(--ink); padding:2px 9px; font: 700 13px/1.4 'Bricolage Grotesque',sans-serif; color:var(--ink); }}
.dr-store {{ color: var(--slate); font-size: 13px; margin: 0 76px 6px 6px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
.dr-name {{ font: 600 17px/1.28 'Bricolage Grotesque',sans-serif; display:-webkit-box; -webkit-line-clamp:2;
    -webkit-box-orient:vertical; overflow:hidden; min-height:44px; margin-bottom:8px; }}
.dr-price {{ font: 800 38px/1.05 'Bricolage Grotesque',sans-serif; letter-spacing:-0.02em; }}
.dr-was {{ font-size: 14px; color: var(--slate); text-decoration: line-through; margin-left: 8px; font-weight:400; letter-spacing:0; }}
.dr-delta {{ font-size: 14px; font-weight: 600; margin: 4px 0 8px; }}
.dr-delta.down {{ color: var(--green); }} .dr-delta.up {{ color: var(--red); }} .dr-delta.flat {{ color: var(--slate); font-weight:500; }}
.dr-spark {{ display:block; width:100%; height:34px; margin: 2px 0 8px; }}
.dr-meta {{ font-size: 13px; color: var(--slate); line-height: 1.55; border-top: 1px solid var(--line); padding-top: 8px; }}
.dr-meta b {{ color: var(--ink); font-weight:600; }}
.dr-oos {{ color: var(--red); font-weight:600; }}
.dr-demo {{ background: var(--paper); border:1px solid var(--line); padding:0 6px; font-size:11.5px; margin-left:6px; }}
.dr-empty {{ border: 1.5px dashed var(--slate); padding: 28px; color: var(--ink); max-width: 640px; line-height:1.55; }}
@media (prefers-reduced-motion: reduce) {{ * {{ transition: none !important; animation: none !important; }} }}
</style>
"""


def inject_css() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def header() -> None:
    st.markdown(
        '<h1 class="dr-title">Deal Radar</h1>'
        '<p class="dr-sub">Paste product links. A TinyFish web agent opens each page, reads the live price, '
        "and tells you when it drops below your target.</p>",
        unsafe_allow_html=True,
    )


def stat_strip(tracked: int, target_hits: int, best_drop: float, runs_used: int, runs_cap: int) -> None:
    drop = f"{abs(best_drop):.1f}%" if best_drop < 0 else "none yet"
    items = [
        (str(tracked), "products tracked"),
        (str(target_hits), "at or below target"),
        (drop, "biggest drop since last check"),
        (f"{runs_used} of {runs_cap}", "agent runs this session"),
    ]
    cells = "".join(f'<div class="dr-stat"><b>{escape(v)}</b><span>{escape(label)}</span></div>' for v, label in items)
    st.markdown(f'<div class="dr-stats">{cells}</div>', unsafe_allow_html=True)


def sparkline(prices: list[float], color: str) -> str:
    w, h, pad = 220, 34, 4
    if len(prices) < 2:
        return (f'<svg class="dr-spark" viewBox="0 0 {w} {h}" preserveAspectRatio="none" aria-hidden="true">'
                f'<line x1="{pad}" y1="{h/2}" x2="{w-pad}" y2="{h/2}" stroke="#C9D1CD" stroke-width="2" stroke-dasharray="4 5"/></svg>')
    pts = prices[-24:]
    lo, hi = min(pts), max(pts)
    span = (hi - lo) or 1.0
    xs = [pad + i * (w - 2 * pad) / (len(pts) - 1) for i in range(len(pts))]
    ys = [h - pad - (p - lo) / span * (h - 2 * pad) for p in pts]
    path = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys))
    return (f'<svg class="dr-spark" viewBox="0 0 {w} {h}" preserveAspectRatio="none" role="img" '
            f'aria-label="Price trend over the last {len(pts)} checks">'
            f'<polyline points="{path}" fill="none" stroke="{INK}" stroke-width="2" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>'
            f'<circle cx="{xs[-1]:.1f}" cy="{ys[-1]:.1f}" r="3.5" fill="{color}" stroke="{INK}" stroke-width="1.5" vector-effect="non-scaling-stroke"/></svg>')


def _delta(s: dict) -> tuple[str, str]:
    cur = s["currency"]
    if s["status"] == "unchecked":
        return "flat", "Not checked yet"
    if s["checks"] == 1:
        return "flat", "First reading saved"
    if s["change"] < 0 and abs(s["change_pct"]) >= 0.05:
        return "down", f"Down {abs(s['change_pct']):.1f}% ({money(abs(s['change']), cur)}) since last check"
    if s["change"] > 0 and abs(s["change_pct"]) >= 0.05:
        return "up", f"Up {s['change_pct']:.1f}% ({money(s['change'], cur)}) since last check"
    return "flat", "No change since last check"


def tag_card(product: dict, s: dict) -> str:
    name = escape(product.get("name") or store_name(product["url"]))
    store = escape(product.get("store") or store_name(product["url"]))
    demo = '<span class="dr-demo">demo</span>' if product.get("is_demo") else ""
    sticker = ""
    if s["target_hit"]:
        sticker = '<div class="dr-sticker">Target hit</div>'
    elif s["is_new_low"]:
        sticker = '<div class="dr-sticker">New low</div>'

    kind, delta_text = _delta(s)
    price = money(s["current"], s["currency"]) if s["current"] else "-"
    was = ""
    if s["current"] and s["original"]:
        was = f'<span class="dr-was">{money(s["original"], s["currency"])}</span>'

    rows = []
    if s["lowest"] is not None and s["checks"] > 1:
        rows.append(f'Lowest tracked <b>{money(s["lowest"], s["currency"])}</b>')
    if s["target"]:
        gap = s["gap_to_target"]
        tail = "reached" if s["target_hit"] else f"{money(gap, s['currency'])} to go"
        rows.append(f'Target <b>{money(s["target"], s["currency"])}</b>, {tail}')
    if s["in_stock"] is False:
        rows.append('<span class="dr-oos">Out of stock</span>')
    rows.append(f'Checked {escape(time_ago(s["last_checked"]))}, {s["checks"]} reading{"s" if s["checks"] != 1 else ""}')
    spark_color = {"down": GREEN, "up": RED}.get(kind, "#FFFFFF")

    html = (
        f'<div class="dr-tagwrap"><div class="dr-tag"><div class="dr-hole"></div>{sticker}'
        f'<div class="dr-store">{store}{demo}</div><div class="dr-name">{name}</div>'
        f'<div class="dr-price">{escape(price)}{was}</div>'
        f'<div class="dr-delta {kind}">{escape(delta_text)}</div>'
        f'{sparkline(s["prices"], spark_color)}'
        f'<div class="dr-meta">{"<br>".join(rows)}</div></div></div>'
    )
    return html.replace("$", "&#36;")  # stop Streamlit markdown from reading $...$ as math


def empty_state() -> None:
    st.markdown(
        '<div class="dr-empty"><b>Nothing on the radar yet.</b><br>Open the Add products tab and paste a product link. '
        "Want to see the dashboard first? Choose Load demo data in the sidebar. It costs no credits.</div>",
        unsafe_allow_html=True,
    )
