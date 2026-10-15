"""End-to-end: add a link -> agent runs -> price saved -> balance drops. Uses a fake TinyFish, no network."""
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace as NS

from streamlit.testing.v1 import AppTest
from tinyfish import RunStatus

APP = str(Path(__file__).resolve().parent.parent / "app.py")


class FakeTinyFish:
    def __init__(self):
        self.balance = Decimal("10.00")
        self.agent = NS(queue=self._queue)
        self.runs = NS(get=self._get, list=lambda **kw: None)
        self.wallet = NS(get=lambda: NS(available_balance=str(self.balance), currency="USD"))
        self.search = NS(query=lambda q: NS(results=[NS(url="https://shop.example.com/p/1", title="Result one", site_name="shop.example.com", snippet="")]))
        self.n = 0

    def _queue(self, **kw):
        self.n += 1
        self.balance -= Decimal("0.25")
        return NS(run_id=f"run_{self.n}", error=None)

    def _get(self, run_id):
        return NS(status=RunStatus.COMPLETED, streaming_url=None, error=None,
                  result={"product_name": "Test Mouse", "current_price": 1299, "original_price": 1999, "currency": "INR", "in_stock": True})


def test_full_price_check_flow(monkeypatch):
    from dealradar import agent, config
    from dealradar.db import Store

    fake = FakeTinyFish()
    monkeypatch.setattr(agent, "make_client", lambda key: fake)
    monkeypatch.setattr(config, "POLL_SECONDS", 0)
    monkeypatch.setenv("TINYFISH_API_KEY", "sk-tinyfish-test-key-123456")

    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception
    assert any("Connected" in s.value for s in at.sidebar.success)

    at.text_area[0].set_value("https://shop.example.com/p/1\nnot a link").run()
    next(b for b in at.button if b.label == "Add to watchlist").click().run()
    assert not at.exception, at.exception

    store = Store(config.DB_PATH)
    products = [p for p in store.products() if not p["is_demo"]]
    assert len(products) == 1
    history = store.history(products[0]["id"])
    assert history and history[-1]["price"] == 1299 and history[-1]["currency"] == "INR"
    assert store.get_product(products[0]["id"])["name"] == "Test Mouse"
    assert store.runs()[0]["status"] == "done" and store.runs()[0]["run_id"] == "run_1"

    assert at.session_state["runs_used"] == 1
    assert at.session_state["balance_now"] == Decimal("9.75")  # credits visibly went down
    html = " ".join(m.value for m in at.markdown)
    assert "Test Mouse" in html and "₹1,299" in html
