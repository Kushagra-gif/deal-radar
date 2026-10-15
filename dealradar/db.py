"""Tiny SQLite store: watchlist, price history and a log of agent runs."""
from __future__ import annotations

import json
import random
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .analysis import store_name

SCHEMA = """
CREATE TABLE IF NOT EXISTS products(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL UNIQUE,
    name TEXT,
    store TEXT,
    target_price REAL,
    currency TEXT,
    is_demo INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS prices(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    price REAL NOT NULL,
    currency TEXT,
    original_price REAL,
    in_stock INTEGER,
    seller TEXT,
    run_id TEXT,
    checked_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_prices_product ON prices(product_id, checked_at);
CREATE TABLE IF NOT EXISTS runs(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
    product_label TEXT,
    run_id TEXT,
    status TEXT,
    error TEXT,
    duration_s REAL,
    created_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._memory = sqlite3.connect(":memory:") if self.path == ":memory:" else None
        with self._conn() as con:
            con.executescript(SCHEMA)

    @contextmanager
    def _conn(self):
        con = self._memory or sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        try:
            yield con
            con.commit()
        finally:
            if self._memory is None:
                con.close()

    # ---- products ---------------------------------------------------------
    def add_product(self, url: str, name: str | None = None, target: float | None = None,
                    is_demo: bool = False, currency: str | None = None) -> int | None:
        """Insert a product. Returns its id, or None if the URL is already tracked."""
        url = url.strip()
        with self._conn() as con:
            try:
                cur = con.execute(
                    "INSERT INTO products(url, name, store, target_price, currency, is_demo, created_at)"
                    " VALUES(?,?,?,?,?,?,?)",
                    (url, name, store_name(url), target or None, currency, int(is_demo), _now()),
                )
            except sqlite3.IntegrityError:
                return None
            return cur.lastrowid

    def products(self) -> list[dict]:
        with self._conn() as con:
            return [dict(r) for r in con.execute("SELECT * FROM products ORDER BY id")]

    def get_product(self, pid: int) -> dict | None:
        with self._conn() as con:
            row = con.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
            return dict(row) if row else None

    def set_target(self, pid: int, target: float | None) -> None:
        with self._conn() as con:
            con.execute("UPDATE products SET target_price=? WHERE id=?", (target or None, pid))

    def delete_product(self, pid: int) -> None:
        with self._conn() as con:
            con.execute("DELETE FROM products WHERE id=?", (pid,))

    # ---- prices -----------------------------------------------------------
    def add_price(self, pid: int, price: float, currency: str | None = None,
                  original_price: float | None = None, in_stock: bool | None = None,
                  seller: str | None = None, run_id: str | None = None,
                  name: str | None = None, checked_at: str | None = None) -> None:
        with self._conn() as con:
            con.execute(
                "INSERT INTO prices(product_id, price, currency, original_price, in_stock, seller, run_id, checked_at)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (pid, price, currency, original_price, None if in_stock is None else int(in_stock),
                 seller, run_id, checked_at or _now()),
            )
            # Keep the product's display name and currency fresh from the latest read.
            if name:
                con.execute("UPDATE products SET name=? WHERE id=?", (name, pid))
            if currency:
                con.execute("UPDATE products SET currency=? WHERE id=?", (currency, pid))

    def history(self, pid: int) -> list[dict]:
        with self._conn() as con:
            rows = con.execute("SELECT * FROM prices WHERE product_id=? ORDER BY checked_at, id", (pid,))
            return [dict(r) for r in rows]

    def all_history(self) -> dict[int, list[dict]]:
        grouped: dict[int, list[dict]] = {}
        with self._conn() as con:
            for r in con.execute("SELECT * FROM prices ORDER BY checked_at, id"):
                grouped.setdefault(r["product_id"], []).append(dict(r))
        return grouped

    # ---- run log ----------------------------------------------------------
    def log_run(self, product_id: int | None, label: str, run_id: str | None, status: str,
                error: str | None = None, duration_s: float | None = None) -> None:
        with self._conn() as con:
            con.execute(
                "INSERT INTO runs(product_id, product_label, run_id, status, error, duration_s, created_at)"
                " VALUES(?,?,?,?,?,?,?)",
                (product_id, label, run_id, status, error, duration_s, _now()),
            )

    def runs(self, limit: int = 200) -> list[dict]:
        with self._conn() as con:
            return [dict(r) for r in con.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))]

    # ---- backup / restore -------------------------------------------------
    def export_json(self) -> str:
        history = self.all_history()
        payload = []
        for p in self.products():
            if p["is_demo"]:
                continue
            payload.append({**p, "history": [
                {k: h[k] for k in ("price", "currency", "original_price", "in_stock", "seller", "checked_at")}
                for h in history.get(p["id"], [])
            ]})
        return json.dumps({"app": "deal-radar", "version": 1, "products": payload}, indent=2)

    def import_json(self, text: str) -> tuple[int, int]:
        data = json.loads(text)
        added = skipped = 0
        for item in data.get("products", []):
            pid = self.add_product(item["url"], item.get("name"), item.get("target_price"),
                                   currency=item.get("currency"))
            if pid is None:
                skipped += 1
                continue
            added += 1
            for h in item.get("history", []):
                self.add_price(pid, h["price"], h.get("currency"), h.get("original_price"),
                               h.get("in_stock"), h.get("seller"), checked_at=h["checked_at"])
        return added, skipped

    # ---- demo data (so the dashboard looks alive without spending credits) --
    def has_demo(self) -> bool:
        with self._conn() as con:
            return con.execute("SELECT 1 FROM products WHERE is_demo=1 LIMIT 1").fetchone() is not None

    def seed_demo(self) -> int:
        rng = random.Random(11)
        # (name, store slug, start price, drift per check, target, currency, end behaviour)
        items = [
            ("Sony WH-1000XM5 Wireless Noise Cancelling Headphones", "shop-demo.example/sony-xm5", 29990, -0.012, 24990, "INR"),
            ("Logitech MX Master 3S Wireless Mouse", "gadgets-demo.example/mx-master-3s", 9495, -0.004, 7500, "INR"),
            ("Kindle Paperwhite (16 GB) 6.8-inch Display", "books-demo.example/kindle-pw", 14999, 0.006, 12999, "INR"),
            ("Samsung 980 PRO 1TB NVMe SSD", "storage-demo.example/980-pro-1tb", 11499, 0.0, 9999, "INR"),
            ("Apple AirPods Pro (2nd generation)", "shop-demo.example/airpods-pro-2", 24900, -0.018, 22000, "INR"),
        ]
        added = 0
        now = datetime.now(timezone.utc)
        for name, slug, start, drift, target, cur in items:
            pid = self.add_product(f"https://{slug}", name, target, is_demo=True, currency=cur)
            if pid is None:
                continue
            added += 1
            price = float(start)
            for i in range(14):
                price = max(price * (1 + drift + rng.uniform(-0.012, 0.012)), 1)
                stamp = (now - timedelta(days=13 - i, hours=rng.randint(0, 5))).isoformat(timespec="seconds")
                self.add_price(pid, round(price), cur, round(start * 1.1), True, "Demo store", checked_at=stamp)
        return added

    def clear_demo(self) -> None:
        with self._conn() as con:
            con.execute("DELETE FROM products WHERE is_demo=1")
