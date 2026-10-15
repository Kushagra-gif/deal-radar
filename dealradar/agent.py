"""Everything that talks to TinyFish.

Flow for one price check:
    client.agent.queue(goal, url, output_schema)  -> run_id        (starts a real browser agent)
    client.runs.get(run_id)                       -> status/result (polled until COMPLETED / FAILED)

Several products run in parallel; this module is UI-free so it can be tested with a fake client.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Callable

from tinyfish import (
    AuthenticationError,
    BrowserProfile,
    NotFoundError,
    ProxyConfig,
    ProxyCountryCode,
    RateLimitError,
    RunStatus,
    SDKError,
    TinyFish,
)

from .analysis import parse_price

GOAL = (
    "You are on a single product page. Read the product's name and the CURRENT selling price "
    "that a buyer would pay today, shown in the main buy box. "
    "Return current_price as a plain number with no currency symbol and no thousands separators "
    "(for example 1299.5). Return currency as the 3-letter ISO code (for example INR, USD, EUR). "
    "If a higher struck-through list price (MRP) is shown, return it as original_price, otherwise omit it. "
    "Also return whether the item is in stock and the seller or store name if visible. "
    "Close cookie banners or pop-ups if they block the page. "
    "Ignore sponsored, related, or 'frequently bought together' items. "
    "Never log in, add to cart, or buy anything. "
    "If you cannot find a price, return current_price as 0."
)

PRICE_SCHEMA = {
    "type": "object",
    "properties": {
        "product_name": {"type": "string"},
        "current_price": {"type": "number"},
        "currency": {"type": "string"},
        "original_price": {"type": "number"},
        "in_stock": {"type": "boolean"},
        "seller": {"type": "string"},
    },
    "required": ["product_name", "current_price", "currency"],
}


# --------------------------------------------------------------------------- client
def make_client(api_key: str) -> TinyFish:
    return TinyFish(api_key=api_key)


def _to_decimal(value) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        return None


def get_balance(client: TinyFish) -> tuple[Decimal | None, str | None]:
    """Wallet balance, or (None, None) when the account is on a legacy credits plan."""
    try:
        wallet = client.wallet.get()
        return _to_decimal(wallet.available_balance), wallet.currency
    except NotFoundError:
        return None, None
    except SDKError:
        return None, None


def check_connection(client: TinyFish) -> dict:
    """Cheap, read-only call that proves the key works (does not start an agent run)."""
    try:
        try:
            wallet = client.wallet.get()
            return {"ok": True, "message": "Connected", "balance": _to_decimal(wallet.available_balance),
                    "currency": wallet.currency}
        except NotFoundError:
            client.runs.list(limit=1)  # legacy plan: no wallet, but the key can still be verified
            return {"ok": True, "message": "Connected", "balance": None, "currency": None}
    except AuthenticationError:
        return {"ok": False, "message": "TinyFish rejected this API key.", "balance": None, "currency": None}
    except SDKError as exc:
        return {"ok": False, "message": f"Could not reach TinyFish: {exc}", "balance": None, "currency": None}


def search_products(client: TinyFish, query: str, limit: int = 8) -> list[dict]:
    """Search API (free) - helps the user find product pages to track."""
    response = client.search.query(query)
    results = []
    for r in (response.results or [])[:limit]:
        if r.url:
            results.append({"title": r.title or r.url, "url": r.url, "site": r.site_name or "", "snippet": r.snippet or ""})
    return results


# --------------------------------------------------------------------------- result parsing
@dataclass
class PriceReading:
    ok: bool
    name: str | None = None
    price: float | None = None
    currency: str | None = None
    original_price: float | None = None
    in_stock: bool | None = None
    seller: str | None = None
    error: str | None = None


def parse_result(result) -> PriceReading:
    """Validate what the agent returned. Never trust it blindly - a bad read must not pollute history."""
    if not isinstance(result, dict):
        return PriceReading(False, error="The agent returned no data.")
    if "current_price" not in result and len(result) == 1 and isinstance(next(iter(result.values())), dict):
        result = next(iter(result.values()))  # unwrap {"result": {...}}
    price = parse_price(result.get("current_price"))
    if price is None:
        return PriceReading(False, name=result.get("product_name"),
                            error="No price found on the page (it may be blocked, sold out, or need a login).")
    original = parse_price(result.get("original_price"))
    currency = (result.get("currency") or "").strip().upper()[:3] or None
    in_stock = result.get("in_stock")
    return PriceReading(
        True, name=(result.get("product_name") or "").strip() or None, price=price, currency=currency,
        original_price=original if original and original > price else None,
        in_stock=in_stock if isinstance(in_stock, bool) else None,
        seller=(result.get("seller") or "").strip() or None,
    )


# --------------------------------------------------------------------------- batch runner
@dataclass
class CheckState:
    product: dict
    status: str = "waiting"          # waiting | running | done | failed
    run_id: str | None = None
    streaming_url: str | None = None
    started: float | None = None
    duration_s: float | None = None
    reading: PriceReading | None = None
    error: str | None = None
    poll_errors: int = field(default=0, repr=False)


def _proxy(country: str | None):
    if not country:
        return None
    return ProxyConfig(enabled=True, country_code=ProxyCountryCode(country))


def _profile(stealth: bool):
    return BrowserProfile.STEALTH if stealth else BrowserProfile.LITE


def run_checks(
    client: TinyFish,
    products: list[dict],
    *,
    stealth: bool = True,
    proxy_country: str | None = None,
    max_parallel: int = 3,
    timeout_s: float = 240,
    poll_s: float = 3,
    on_update: Callable[[list[CheckState]], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> list[CheckState]:
    """Start up to `max_parallel` agents at once and poll them until every product is resolved."""
    states = [CheckState(p) for p in products]
    pending = deque(states)
    active: list[CheckState] = []

    def notify():
        if on_update:
            on_update(states)

    def fail(state: CheckState, message: str):
        state.status, state.error = "failed", message
        if state.started is not None:
            state.duration_s = clock() - state.started

    while pending or active:
        # 1) start new runs while there is room
        while pending and len(active) < max_parallel:
            state = pending.popleft()
            try:
                queued = client.agent.queue(
                    goal=GOAL, url=state.product["url"], browser_profile=_profile(stealth),
                    proxy_config=_proxy(proxy_country), output_schema=PRICE_SCHEMA,
                )
            except AuthenticationError:
                raise  # a bad key fails every product - stop right away
            except RateLimitError:
                fail(state, "TinyFish rate limit reached. Try again in a minute or lower the parallel runs.")
                continue
            except SDKError as exc:
                fail(state, f"Could not start the agent: {exc}")
                continue
            if queued.error or not queued.run_id:
                fail(state, f"Could not start the agent: {getattr(queued.error, 'message', 'no run id returned')}")
                continue
            state.status, state.run_id, state.started = "running", queued.run_id, clock()
            active.append(state)
        notify()

        # 2) poll everything that is running
        for state in list(active):
            try:
                run = client.runs.get(state.run_id)
                state.poll_errors = 0
            except AuthenticationError:
                raise
            except SDKError as exc:
                state.poll_errors += 1
                if state.poll_errors >= 3:
                    fail(state, f"Lost contact with the run: {exc}")
                    active.remove(state)
                continue

            state.streaming_url = getattr(run, "streaming_url", None) or state.streaming_url
            if run.status == RunStatus.COMPLETED:
                reading = parse_result(run.result)
                state.reading = reading
                state.duration_s = clock() - (state.started if state.started is not None else clock())
                if reading.ok:
                    state.status = "done"
                else:
                    fail(state, reading.error or "No price found.")
                active.remove(state)
            elif run.status in (RunStatus.FAILED, RunStatus.CANCELLED):
                message = getattr(run.error, "message", None) or f"Run {run.status.value.lower()}."
                fail(state, message)
                active.remove(state)
            elif state.started is not None and clock() - state.started > timeout_s:
                fail(state, f"Timed out after {int(timeout_s)} seconds. The run may still finish in your TinyFish dashboard.")
                active.remove(state)
        notify()

        if active or pending:
            sleep(poll_s)
    return states
