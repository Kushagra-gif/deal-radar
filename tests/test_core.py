from types import SimpleNamespace as NS

import pytest
from tinyfish import AuthenticationError, RunStatus

from dealradar import agent
from dealradar.analysis import is_valid_url, money, parse_price, summarize, time_ago
from dealradar.db import Store


# ---------------------------------------------------------------- analysis
@pytest.mark.parametrize("raw,expected", [
    (1299, 1299.0), ("1,299.50", 1299.5), ("₹ 1,29,999", 129999.0), ("Rs. 499", 499.0),
    ("$12.99", 12.99), ("12,99", 12.99), ("1.299,50", 1299.5), (0, None), ("free", None), (None, None), (True, None),
])
def test_parse_price(raw, expected):
    assert parse_price(raw) == expected


def test_money_formats_indian_grouping():
    assert money(1234567, "INR") == "₹12,34,567"
    assert money(1299.5, "USD") == "$1,299.50"
    assert money(None) == "-"


def test_is_valid_url():
    assert is_valid_url("https://shop.example.com/p/1")
    assert not is_valid_url("javascript:alert(1)")
    assert not is_valid_url("not a link")


def test_time_ago_never():
    assert time_ago(None) == "never"


def hist(*prices):
    return [{"price": p, "currency": "INR", "original_price": None, "in_stock": 1, "checked_at": f"2026-01-{i+1:02d}T10:00:00+00:00"}
            for i, p in enumerate(prices)]


def test_summarize_statuses():
    p = {"target_price": None, "currency": "INR"}
    assert summarize(p, [])["status"] == "unchecked"
    assert summarize(p, hist(100))["status"] == "new"
    assert summarize(p, hist(100, 90))["status"] == "drop"
    assert summarize(p, hist(100, 110))["status"] == "rise"
    assert summarize(p, hist(100, 100))["status"] == "flat"
    s = summarize({"target_price": 95}, hist(100, 90))
    assert s["status"] == "target" and s["target_hit"] and s["is_new_low"]
    assert summarize({"target_price": 50}, hist(100, 90))["gap_to_target"] == 40


# ---------------------------------------------------------------- parsing agent output
def test_parse_result_ok_and_wrapped():
    r = agent.parse_result({"product_name": " Mouse ", "current_price": "₹1,299", "currency": "inr", "original_price": 1999, "in_stock": True})
    assert r.ok and r.price == 1299 and r.currency == "INR" and r.original_price == 1999 and r.name == "Mouse"
    assert agent.parse_result({"result": {"current_price": 5, "currency": "usd", "product_name": "x"}}).ok


def test_parse_result_rejects_bad_reads():
    assert not agent.parse_result(None).ok
    assert not agent.parse_result({"product_name": "x", "current_price": 0, "currency": "INR"}).ok
    # a list price lower than the current price is ignored
    assert agent.parse_result({"product_name": "x", "current_price": 10, "currency": "USD", "original_price": 5}).original_price is None


# ---------------------------------------------------------------- batch runner with a fake TinyFish
class FakeClient:
    """Mimics client.agent.queue / client.runs.get. `script` maps url -> list of run snapshots."""

    def __init__(self, script, queue_error=None):
        self.script, self.counters, self.by_run, self.queue_calls, self.queue_error = script, {}, {}, [], queue_error
        self.max_active = 0
        self.agent = NS(queue=self._queue)
        self.runs = NS(get=self._get)

    def _queue(self, **kw):
        if self.queue_error:
            raise self.queue_error
        self.queue_calls.append(kw)
        run_id = f"run_{len(self.queue_calls)}"
        self.by_run[run_id] = kw["url"]
        self.counters[run_id] = 0
        active = sum(1 for r, c in self.counters.items() if c < len(self.script[self.by_run[r]]) - 1)
        self.max_active = max(self.max_active, active)
        return NS(run_id=run_id, error=None)

    def _get(self, run_id):
        snaps = self.script[self.by_run[run_id]]
        i = min(self.counters[run_id], len(snaps) - 1)
        self.counters[run_id] += 1
        return snaps[i]


def snap(status, result=None, error=None, live=None):
    return NS(status=status, result=result, error=NS(message=error) if error else None, streaming_url=live)


GOOD = {"product_name": "Mouse", "current_price": 1299, "currency": "INR"}
NOW = {"clock": 0.0}


def run(client, urls, **kw):
    products = [{"id": i + 1, "url": u, "name": None} for i, u in enumerate(urls)]
    NOW["clock"] = 0.0

    def tick(_):
        NOW["clock"] += 10

    return agent.run_checks(client, products, sleep=tick, clock=lambda: NOW["clock"], **kw)


def test_run_checks_success_and_live_url():
    client = FakeClient({"https://a.com/1": [snap(RunStatus.RUNNING, live="wss://live"), snap(RunStatus.COMPLETED, GOOD)]})
    states = run(client, ["https://a.com/1"])
    assert states[0].status == "done" and states[0].reading.price == 1299 and states[0].streaming_url == "wss://live"
    call = client.queue_calls[0]
    assert call["output_schema"] == agent.PRICE_SCHEMA and call["browser_profile"].value == "stealth"


def test_run_checks_failure_modes():
    client = FakeClient({
        "https://a.com/failed": [snap(RunStatus.FAILED, error="Blocked by site")],
        "https://a.com/noprice": [snap(RunStatus.COMPLETED, {"product_name": "x", "current_price": 0, "currency": "INR"})],
        "https://a.com/stuck": [snap(RunStatus.RUNNING)],
    })
    states = run(client, list(client.script), timeout_s=60, poll_s=10)
    by_url = {s.product["url"]: s for s in states}
    assert by_url["https://a.com/failed"].error == "Blocked by site"
    assert "No price" in by_url["https://a.com/noprice"].error
    assert "Timed out" in by_url["https://a.com/stuck"].error
    assert all(s.status == "failed" for s in states)


def test_run_checks_respects_parallel_limit():
    script = {f"https://a.com/{i}": [snap(RunStatus.RUNNING), snap(RunStatus.COMPLETED, GOOD)] for i in range(6)}
    client = FakeClient(script)
    states = run(client, list(script), max_parallel=2)
    assert len(states) == 6 and all(s.status == "done" for s in states)
    assert client.max_active <= 2


def test_bad_key_stops_the_batch():
    err = AuthenticationError("bad key", response=NS(status_code=401, headers={}, request=None))
    with pytest.raises(AuthenticationError):
        run(FakeClient({"https://a.com/1": []}, queue_error=err), ["https://a.com/1"])


def test_other_queue_errors_fail_only_that_product():
    from tinyfish import SDKError
    states = run(FakeClient({"https://a.com/1": []}, queue_error=SDKError("boom")), ["https://a.com/1"])
    assert states[0].status == "failed" and "boom" in states[0].error


# ---------------------------------------------------------------- store
def test_store_roundtrip(tmp_path):
    s = Store(tmp_path / "t.db")
    pid = s.add_product("https://shop.example.com/a", target=900)
    assert s.add_product("https://shop.example.com/a") is None  # duplicate
    s.add_price(pid, 1000, "INR", 1200, True, "Shop", "run_1", name="Thing")
    s.add_price(pid, 950, "INR")
    assert [h["price"] for h in s.history(pid)] == [1000, 950]
    assert s.get_product(pid)["name"] == "Thing"

    backup = s.export_json()
    fresh = Store(tmp_path / "t2.db")
    assert fresh.import_json(backup) == (1, 0)
    assert fresh.import_json(backup) == (0, 1)
    assert len(fresh.history(fresh.products()[0]["id"])) == 2

    s.delete_product(pid)
    assert s.history(pid) == []  # cascade


def test_demo_data_is_excluded_from_export(tmp_path):
    s = Store(tmp_path / "d.db")
    assert s.seed_demo() == 5 and s.has_demo()
    assert '"products": []' in s.export_json()
    s.clear_demo()
    assert not s.has_demo()


def test_tag_card_is_safe_html():
    from dealradar import theme
    p = {"url": "https://shop.example.com/a", "name": "Mouse <script>x</script> $5", "store": "shop.example.com", "target_price": 10}
    h = [{"price": 9.99, "currency": "USD", "original_price": 20, "in_stock": 1, "checked_at": "2026-01-02T10:00:00+00:00"}]
    html = theme.tag_card(p, summarize(p, h))
    assert "<script>" not in html  # names come from the web, so they must be escaped
    assert "$" not in html  # raw dollar signs would be read as LaTeX by Streamlit markdown
