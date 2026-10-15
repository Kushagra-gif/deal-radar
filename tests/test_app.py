"""Smoke-test the real Streamlit app in-process (no network, no API key)."""
from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parent.parent / "app.py")


def test_app_loads_empty_and_shows_key_hint():
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    assert any("TINYFISH_API_KEY" in i.value for i in at.sidebar.info)
    assert at.button(key=None) is not None


def test_demo_data_renders_cards():
    at = AppTest.from_file(APP, default_timeout=30).run()
    load = next(b for b in at.sidebar.button if b.label == "Load demo data")
    load.click().run()
    assert not at.exception
    html = " ".join(m.value for m in at.markdown)
    assert "dr-tag" in html and "Sony WH-1000XM5" in html
    assert "Target hit" in html or "New low" in html
    # history tab needs a product selected and should draw without errors
    assert len(at.selectbox) >= 2
