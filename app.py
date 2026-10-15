"""Deal Radar - track product prices with TinyFish web agents.

Run it:  streamlit run app.py
"""
from __future__ import annotations

import hmac
import re

import altair as alt
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Deal Radar", page_icon="📡", layout="wide")

from tinyfish import AuthenticationError  # noqa: E402

from dealradar import config, theme  # noqa: E402
from dealradar.agent import (  # noqa: E402
    CheckState, check_connection, get_balance, make_client, run_checks, search_products,
)
from dealradar.analysis import STATUS_RANK, is_valid_url, money, store_name, summarize  # noqa: E402
from dealradar.db import Store  # noqa: E402

ss = st.session_state
for key, default in {
    "runs_used": 0, "balance_start": None, "balance_now": None, "currency": None, "conn": None,
    "conn_key": None, "last_batch": None, "pending": None, "search_results": [], "authed": False,
}.items():
    ss.setdefault(key, default)

theme.inject_css()


# --------------------------------------------------------------------------- helpers
@st.cache_resource
def get_store() -> Store:
    return Store(config.DB_PATH)


@st.cache_resource
def get_client(api_key: str):
    return make_client(api_key)


def plain(text: str | None) -> str:
    """Strip characters that Streamlit markdown would treat as formatting or links."""
    return re.sub(r"[\[\]()`*_<>#|]", "", text or "").strip()


def md(text: str) -> str:
    """Escape dollar signs so Streamlit markdown does not turn prices into math."""
    return text.replace("$", "\\$")


def label_of(p: dict) -> str:
    return plain(p.get("name")) or store_name(p["url"])


def password_gate() -> None:
    password = config.app_password()
    if not password or ss.authed:
        return
    theme.header()
    entered = st.text_input("Password", type="password")
    if entered:
        if hmac.compare_digest(entered, password):
            ss.authed = True
            st.rerun()
        st.error("That password is not right.")
    st.stop()


store = get_store()
password_gate()


# --------------------------------------------------------------------------- sidebar
def sidebar():
    """Draws the sidebar and returns (client or None, settings)."""
    with st.sidebar:
        st.markdown("### TinyFish connection")
        key, source = config.resolve_api_key(ss.get("typed_key"))

        if source in ("env", "secrets"):
            st.caption("API key loaded from " + (".env" if source == "env" else "Streamlit secrets") + ".")
            with st.expander("Use a different key"):
                st.text_input("API key", type="password", key="typed_key", placeholder="sk-tinyfish-...")
        else:
            st.text_input("API key", type="password", key="typed_key", placeholder="sk-tinyfish-...")
            if source == "none":
                st.info(f"Put your key in a .env file as TINYFISH_API_KEY, or paste it above. "
                        f"You can create one at [agent.tinyfish.ai]({config.KEYS_URL}).")

        client = None
        if key:
            client = get_client(key)
            key_id = f"{source}:{key[-6:]}"
            retest = st.button("Test connection", use_container_width=True)
            if ss.conn_key != key_id or retest:
                with st.spinner("Checking your key"):
                    ss.conn = check_connection(client)
                ss.conn_key = key_id
                if ss.conn["ok"] and ss.conn["balance"] is not None:
                    ss.balance_now, ss.currency = ss.conn["balance"], ss.conn["currency"]
                    if ss.balance_start is None:
                        ss.balance_start = ss.conn["balance"]
            conn = ss.conn or {}
            if conn.get("ok"):
                st.success("Connected to TinyFish")
            elif conn:
                st.error(conn.get("message", "Could not connect."))
                client = None

        if ss.balance_now is not None:
            spent = (ss.balance_start or ss.balance_now) - ss.balance_now
            st.metric("Wallet balance", f"{ss.balance_now:,.2f} {ss.currency or ''}".strip(),
                      delta=f"-{spent:,.2f} used this session" if spent > 0 else None, delta_color="off")
        st.caption(f"Agent runs this session: {ss.runs_used} of {config.MAX_RUNS_PER_SESSION}")

        st.divider()
        st.markdown("### Agent settings")
        stealth = st.toggle("Stealth browser", value=True,
                            help="Use for shops with bot protection such as Amazon or Flipkart. Turn it off for simple sites.")
        proxy = st.selectbox("Proxy country", ["None", "US", "GB", "CA", "DE", "FR", "JP", "AU"],
                             help="Route the browser through a country. Leave on None unless a shop needs it.")
        parallel = st.slider("Products checked at once", 1, 5, 3)

        st.divider()
        st.markdown("### Your data")
        if store.has_demo():
            if st.button("Clear demo data", use_container_width=True):
                store.clear_demo()
                st.rerun()
        elif st.button("Load demo data", use_container_width=True, help="Sample products with price history. Uses no credits."):
            store.seed_demo()
            st.rerun()
        st.download_button("Export watchlist (JSON)", store.export_json(), "deal-radar-backup.json",
                           "application/json", use_container_width=True)
        upload = st.file_uploader("Import a backup", type="json", label_visibility="collapsed")
        if upload is not None and st.button("Import backup", use_container_width=True):
            try:
                added, skipped = store.import_json(upload.getvalue().decode("utf-8"))
                st.toast(f"Imported {added} products, skipped {skipped} already tracked.")
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(f"That file could not be imported: {exc}")

    settings = {"stealth": stealth, "proxy": None if proxy == "None" else proxy, "parallel": parallel}
    return client, settings


client, settings = sidebar()


# --------------------------------------------------------------------------- running checks
def queue_batch(ids: list[int], title: str) -> None:
    ss.pending = {"ids": ids, "title": title}
    st.rerun()


def render_progress(states: list[CheckState]) -> str:
    lines = []
    for s in states:
        name = label_of(s.product)
        if s.status == "waiting":
            lines.append(f":gray[Waiting] {name}")
        elif s.status == "running":
            live = f" [Watch live]({s.streaming_url})" if s.streaming_url else ""
            lines.append(f":orange[Running] {name}{live}")
        elif s.status == "done":
            r = s.reading
            lines.append(f":green[Done] {name}: **{money(r.price, r.currency)}**")
        else:
            lines.append(f":red[Failed] {name}: {plain(s.error)}")
    return md("\n\n".join(lines))


def run_batch(batch: dict) -> None:
    if client is None:
        st.error("Add a working TinyFish API key in the sidebar first.")
        return
    products = [p for p in (store.get_product(i) for i in batch["ids"]) if p]
    real = [p for p in products if not p["is_demo"]]
    if not real:
        st.warning("Demo products cannot be checked. Add a real product link first.")
        return
    remaining = config.MAX_RUNS_PER_SESSION - ss.runs_used
    if remaining <= 0:
        st.error(f"You have used all {config.MAX_RUNS_PER_SESSION} agent runs allowed for this session. "
                 "Refresh the page to start a new session.")
        return
    if len(real) > remaining:
        st.warning(f"Only {remaining} runs are left in this session, so the first {remaining} products will be checked.")
        real = real[:remaining]

    seen_started: set[str] = set()
    saved: set[int] = set()

    def persist(states: list[CheckState]) -> None:
        # Save each result the moment it lands, so nothing is lost if the page reruns mid-batch.
        for s in states:
            if s.run_id and s.run_id not in seen_started:
                seen_started.add(s.run_id)
                ss.runs_used += 1
            pid = s.product["id"]
            if s.status in ("done", "failed") and pid not in saved:
                saved.add(pid)
                if s.status == "done":
                    r = s.reading
                    store.add_price(pid, r.price, r.currency, r.original_price, r.in_stock, r.seller, s.run_id, name=r.name)
                store.log_run(pid, label_of(s.product), s.run_id, s.status, s.error, s.duration_s)

    status = st.status(batch["title"], expanded=True)
    with status:
        st.caption("Keep this tab open until the agents finish. Each check opens a real browser, so it takes 30 to 90 seconds.")
        slot = st.empty()

        def on_update(states):
            persist(states)
            slot.markdown(render_progress(states))

        try:
            states = run_checks(
                client, real, stealth=settings["stealth"], proxy_country=settings["proxy"],
                max_parallel=settings["parallel"], timeout_s=config.RUN_TIMEOUT_SECONDS,
                poll_s=config.POLL_SECONDS, on_update=on_update,
            )
        except AuthenticationError:
            status.update(label="TinyFish rejected your API key", state="error")
            return
        except Exception as exc:  # noqa: BLE001
            status.update(label="The batch stopped early", state="error")
            st.error(f"Something went wrong: {exc}")
            return

    ok = sum(s.status == "done" for s in states)
    failed = len(states) - ok
    bal, cur = get_balance(client)
    if bal is not None:
        ss.balance_now, ss.currency = bal, cur
        if ss.balance_start is None:
            ss.balance_start = bal
    ss.last_batch = {"ok": ok, "failed": failed, "text": render_progress(states)}
    status.update(label=f"Finished: {ok} price{'s' if ok != 1 else ''} read, {failed} failed", state="complete")
    st.rerun()


# --------------------------------------------------------------------------- page
theme.header()

products = store.products()
history = store.all_history()
summaries = {p["id"]: summarize(p, history.get(p["id"], [])) for p in products}
drops = [s["change_pct"] for s in summaries.values() if s["checks"] > 1 and s["change_pct"] < 0]
theme.stat_strip(
    tracked=len(products), target_hits=sum(s["target_hit"] for s in summaries.values()),
    best_drop=min(drops) if drops else 0.0, runs_used=ss.runs_used, runs_cap=config.MAX_RUNS_PER_SESSION,
)

real_products = [p for p in products if not p["is_demo"]]
bar_a, bar_b = st.columns([1, 3])
if bar_a.button("Check all prices", type="primary", use_container_width=True, disabled=not real_products,
                help="Starts one TinyFish agent per product." if real_products else "Add a product link first."):
    queue_batch([p["id"] for p in real_products], f"Checking {len(real_products)} product(s)")
bar_b.caption(f"{len(real_products)} real products on the radar. Each check is one agent run on your TinyFish account.")

if ss.pending:
    batch, ss.pending = ss.pending, None
    run_batch(batch)

if ss.last_batch:
    summary = ss.last_batch
    ss.last_batch = None
    (st.success if summary["failed"] == 0 else st.warning)(
        f"Last check: {summary['ok']} read, {summary['failed']} failed. Your balance in the sidebar shows what it cost."
    )
    with st.expander("Details from the last check"):
        st.markdown(summary["text"])

tab_watch, tab_add, tab_hist, tab_log = st.tabs(["Watchlist", "Add products", "Price history", "Run log"])

# ---- watchlist
with tab_watch:
    if not products:
        theme.empty_state()
    else:
        sort = st.selectbox("Sort by", ["Best deals first", "Recently added", "Lowest price first"], label_visibility="collapsed")
        rows = [(p, summaries[p["id"]]) for p in products]
        if sort == "Best deals first":
            rows.sort(key=lambda r: (STATUS_RANK[r[1]["status"]], r[1]["change_pct"]))
        elif sort == "Recently added":
            rows.sort(key=lambda r: -r[0]["id"])
        else:
            rows.sort(key=lambda r: (r[1]["current"] is None, r[1]["current"] or 0))

        for start in range(0, len(rows), 3):
            cols = st.columns(3)
            for col, (p, s) in zip(cols, rows[start:start + 3]):
                with col:
                    st.markdown(theme.tag_card(p, s), unsafe_allow_html=True)
                    b1, b2, b3 = st.columns([1.15, 0.85, 1.1])
                    if b1.button("Check now", key=f"chk_{p['id']}", disabled=bool(p["is_demo"]), use_container_width=True,
                                 help="Demo products cannot be checked." if p["is_demo"] else None):
                        queue_batch([p["id"]], f"Checking {label_of(p)}")
                    with b2.popover("Edit", use_container_width=True):
                        new_target = st.number_input("Target price", min_value=0.0, step=1.0, format="%.2f",
                                                     value=float(p["target_price"] or 0), key=f"tgt_{p['id']}",
                                                     help="Set 0 to remove the target.")
                        if st.button("Save target", key=f"save_{p['id']}", use_container_width=True):
                            store.set_target(p["id"], new_target or None)
                            st.rerun()
                        if st.button("Remove product", key=f"rm_{p['id']}", use_container_width=True):
                            store.delete_product(p["id"])
                            st.rerun()
                    b3.link_button("Open page", p["url"], use_container_width=True, disabled=bool(p["is_demo"]))
                    st.write("")

# ---- add products
with tab_add:
    st.markdown("#### Add product links")
    links = st.text_area("Product links", height=150, label_visibility="collapsed",
                         placeholder="https://www.example.com/product-one\nhttps://www.example.com/product-two",
                         help="One link per line. Use the page of a single product, not a search or category page.")
    c1, c2 = st.columns(2)
    target = c1.number_input("Target price for these products (optional)", min_value=0.0, step=1.0, format="%.2f", value=0.0,
                             help="You get a Target hit sticker when the price reaches this. Leave 0 for none.")
    check_now = c2.checkbox("Read the price right after adding", value=True)
    c2.caption("Uses one agent run per link.")

    if st.button("Add to watchlist", type="primary"):
        new_ids, skipped, invalid = [], 0, 0
        for line in dict.fromkeys(l.strip() for l in links.splitlines() if l.strip()):
            if not is_valid_url(line):
                invalid += 1
            elif (pid := store.add_product(line, target=target or None)) is None:
                skipped += 1
            else:
                new_ids.append(pid)
        if invalid:
            st.warning(f"{invalid} line(s) were not valid links and were ignored.")
        if skipped:
            st.info(f"{skipped} link(s) were already on your watchlist.")
        if new_ids:
            if check_now:
                queue_batch(new_ids, f"Reading prices for {len(new_ids)} new product(s)")
            st.rerun()
        elif not invalid and not skipped:
            st.info("Paste at least one product link above.")

    st.divider()
    st.markdown("#### Find product pages")
    st.caption("Uses the TinyFish Search API, which does not use agent credits. Pick results that are single product pages.")
    q = st.text_input("Search the web", placeholder="Sony WH-1000XM5 price", label_visibility="collapsed")
    if st.button("Search", disabled=client is None, help=None if client else "Add your API key first."):
        if q.strip():
            with st.spinner("Searching"):
                try:
                    ss.search_results = search_products(client, q.strip())
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Search failed: {exc}")
    for i, r in enumerate(ss.search_results):
        left, right = st.columns([6, 1])
        left.markdown(f"**{md(plain(r['title']))}**")
        left.caption(md(f"{plain(r['site']) or store_name(r['url'])}  {plain(r['url'])[:90]}"))
        if right.button("Add", key=f"addres_{i}"):
            if store.add_product(r["url"], target=target or None) is None:
                st.toast("Already on your watchlist.")
            else:
                st.toast("Added to your watchlist.")
                st.rerun()

# ---- price history
with tab_hist:
    if not products:
        st.info("Add a product to see its price history.")
    else:
        choice = st.selectbox("Product", products, format_func=label_of)
        records = history.get(choice["id"], [])
        if not records:
            st.info("No readings yet for this product. Run a check from the Watchlist tab.")
        else:
            s = summaries[choice["id"]]
            m = st.columns(4)
            m[0].metric("Current", money(s["current"], s["currency"]))
            m[1].metric("Lowest", money(s["lowest"], s["currency"]))
            m[2].metric("Highest", money(s["highest"], s["currency"]))
            m[3].metric("Average", money(s["average"], s["currency"]))

            df = pd.DataFrame(records)
            df["checked_at"] = pd.to_datetime(df["checked_at"])
            base = alt.Chart(df).encode(
                x=alt.X("checked_at:T", title=None),
                y=alt.Y("price:Q", title=f"Price ({s['currency'] or ''})".strip(), scale=alt.Scale(zero=False)),
                tooltip=[alt.Tooltip("checked_at:T", title="Checked"), alt.Tooltip("price:Q", title="Price", format=",.2f")],
            )
            chart = base.mark_line(color=theme.INK, strokeWidth=2.5) + base.mark_point(color=theme.INK, fill="white", size=70, strokeWidth=2)
            if s["target"]:
                rule = alt.Chart(pd.DataFrame({"t": [s["target"]]})).mark_rule(color=theme.GREEN, strokeDash=[6, 4], strokeWidth=2).encode(y="t:Q")
                chart = chart + rule
            st.altair_chart(chart.properties(height=340), use_container_width=True)
            if s["target"]:
                st.caption("The dashed green line is your target price.")

            table = df.rename(columns={"checked_at": "Checked", "price": "Price", "original_price": "List price", "seller": "Seller"})
            st.dataframe(table[["Checked", "Price", "List price", "Seller"]].iloc[::-1], hide_index=True, use_container_width=True)

        if history:
            names = {p["id"]: label_of(p) for p in products}
            export = pd.DataFrame([{"product": names.get(pid, pid), **{k: h[k] for k in ("checked_at", "price", "currency", "original_price", "in_stock", "seller")}}
                                   for pid, rows_ in history.items() for h in rows_])
            st.download_button("Download all price history (CSV)", export.to_csv(index=False), "deal-radar-history.csv", "text/csv")

# ---- run log
with tab_log:
    st.markdown("#### Every agent run")
    st.caption("Each row is a real TinyFish agent run. Open one to watch what the agent did, and compare it with your wallet balance in the sidebar.")
    runs = store.runs()
    if not runs:
        st.info("Runs will show up here after your first price check.")
    else:
        log = pd.DataFrame(runs)
        log["When"] = pd.to_datetime(log["created_at"]).dt.strftime("%d %b, %H:%M")
        log["Run"] = log["run_id"].map(lambda r: config.RUN_URL.format(run_id=r) if r else None)
        log["Seconds"] = log["duration_s"].round(0)
        log = log.rename(columns={"product_label": "Product", "status": "Result", "error": "Note"})
        st.dataframe(
            log[["When", "Product", "Result", "Seconds", "Note", "Run"]], hide_index=True, use_container_width=True,
            column_config={"Run": st.column_config.LinkColumn("TinyFish run", display_text="Open run")},
        )
