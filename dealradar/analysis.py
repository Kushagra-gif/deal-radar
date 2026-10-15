"""Pure helpers: price parsing, formatting and deal logic. No network, no UI."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urlparse

CURRENCY_SYMBOLS = {
    "INR": "₹", "USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "CNY": "¥",
    "AUD": "A$", "CAD": "C$", "AED": "AED ", "SGD": "S$", "KRW": "₩",
}


def parse_price(value) -> float | None:
    """Turn 1299, '1,299.50', '₹ 1,29,999' or 'Rs. 499' into a float. None if not a price."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None
    text = re.sub(r"[^\d.,]", "", str(value)).strip(".,")  # "Rs. 499" leaves a stray leading dot
    if not text:
        return None
    if "," in text and "." in text:
        # whichever separator comes last is the decimal mark
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        tail = text.rsplit(",", 1)[1]
        text = text.replace(",", ".") if len(tail) in (1, 2) and text.count(",") == 1 else text.replace(",", "")
    try:
        number = float(text)
    except ValueError:
        return None
    return number if number > 0 else None


def _group(integer: str, currency: str) -> str:
    if currency == "INR" and len(integer) > 3:  # lakh / crore grouping
        head, tail = integer[:-3], integer[-3:]
        head = re.sub(r"(\d)(?=(\d\d)+$)", r"\1,", head)
        return f"{head},{tail}"
    return f"{int(integer):,}"


def money(value: float | None, currency: str | None = None) -> str:
    if value is None:
        return "-"
    cur = (currency or "").upper()
    symbol = CURRENCY_SYMBOLS.get(cur, f"{cur} " if cur else "")
    whole = value == int(value)
    integer, _, frac = f"{value:.2f}".partition(".")
    body = _group(integer, cur)
    return f"{symbol}{body}" if whole else f"{symbol}{body}.{frac}"


def store_name(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def is_valid_url(url: str) -> bool:
    parsed = urlparse(url.strip())
    return parsed.scheme in ("http", "https") and bool(parsed.netloc) and "." in parsed.netloc


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def time_ago(value: str | None, now: datetime | None = None) -> str:
    dt = parse_iso(value)
    if not dt:
        return "never"
    seconds = int(((now or datetime.now(timezone.utc)) - dt).total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    if seconds < 86400:
        return f"{seconds // 3600} h ago"
    days = seconds // 86400
    return "yesterday" if days == 1 else f"{days} days ago"


STATUS_RANK = {"target": 0, "drop": 1, "new": 2, "flat": 3, "rise": 4, "unchecked": 5}


def summarize(product: dict, history: list[dict]) -> dict:
    """Everything the UI needs to know about one product."""
    target = product.get("target_price") or None
    out = {
        "status": "unchecked", "current": None, "currency": product.get("currency"),
        "previous": None, "change": 0.0, "change_pct": 0.0, "lowest": None, "highest": None,
        "average": None, "checks": len(history), "is_new_low": False, "target": target,
        "target_hit": False, "gap_to_target": None, "discount_pct": None, "original": None,
        "last_checked": None, "in_stock": None, "prices": [h["price"] for h in history],
    }
    if not history:
        return out

    last = history[-1]
    prices = out["prices"]
    out.update(
        current=last["price"], currency=last.get("currency") or product.get("currency"),
        lowest=min(prices), highest=max(prices), average=sum(prices) / len(prices),
        last_checked=last["checked_at"],
        in_stock=None if last.get("in_stock") is None else bool(last["in_stock"]),
    )
    original = last.get("original_price")
    if original and original > last["price"]:
        out["discount_pct"] = (original - last["price"]) / original * 100
        out["original"] = original

    if len(history) > 1:
        prev = history[-2]["price"]
        out["previous"] = prev
        out["change"] = last["price"] - prev
        out["change_pct"] = (last["price"] - prev) / prev * 100
        out["is_new_low"] = last["price"] < min(prices[:-1])

    if target:
        out["gap_to_target"] = last["price"] - target
        out["target_hit"] = last["price"] <= target

    if out["target_hit"]:
        out["status"] = "target"
    elif len(history) == 1:
        out["status"] = "new"
    elif out["change_pct"] <= -0.5:
        out["status"] = "drop"
    elif out["change_pct"] >= 0.5:
        out["status"] = "rise"
    else:
        out["status"] = "flat"
    return out
