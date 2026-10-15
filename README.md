# Deal Radar

Track product prices with [TinyFish](https://tinyfish.ai) web agents. Paste product links, and a real
browser agent opens each page, reads the live price, and saves it. You get a price-tag dashboard, a price
history chart, and a Target hit sticker when something drops to the price you wanted.

Built with Python and Streamlit. The only paid service it uses is your TinyFish account.

## Run it in 3 steps

1. **Install** (Python 3.11 or newer is required by the TinyFish SDK):
   ```bash
   python -m venv .venv
   source .venv/bin/activate        # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```
2. **Add your API key.** Open the file named `.env`, and replace `PASTE_YOUR_TINYFISH_API_KEY_HERE` with your key.
   Create a key at <https://agent.tinyfish.ai/api-keys> (it is shown only once, so copy it straight away).
3. **Start the app:**
   ```bash
   streamlit run app.py
   ```

No key yet? You can also paste it into the sidebar while the app is running, and use **Load demo data** to see the
dashboard without spending anything.

## Proving that your key is really being used

The app makes this easy to see:

- **Sidebar, Wallet balance.** After each check it shows how much your balance went down this session.
- **Run log tab.** Every check is a real TinyFish agent run. Click **Open run** to see what the agent did.
- **Your TinyFish dashboard** (<https://agent.tinyfish.ai>) lists the same runs and your usage.

Try it with a single product first. Add one link, check the balance, and confirm the run shows up in the dashboard.

## How the app uses TinyFish

| Feature | TinyFish call |
| --- | --- |
| Read a price from a product page | `client.agent.queue(goal, url, output_schema, browser_profile, proxy_config)`, then poll `client.runs.get(run_id)` |
| Guaranteed clean data | `output_schema` makes the agent return `product_name`, `current_price`, `currency`, `original_price`, `in_stock`, `seller` |
| Several products at once | Runs are started in parallel (1 to 5, set in the sidebar) and polled together |
| Live view of the agent | `streaming_url` from the run, shown as a Watch live link while it works |
| Balance and key check | `client.wallet.get()` (read only, starts no run) |
| Find product pages | `client.search.query(...)` |

Bad reads never reach your history. The app checks that the price is a real positive number before saving it.

## Deploy it (Streamlit Community Cloud)

1. Push this folder to a GitHub repository. `.env` and `.streamlit/secrets.toml` are in `.gitignore`, so your key stays private.
2. On <https://share.streamlit.io> create an app from the repo, with `app.py` as the main file.
3. In **Advanced settings**, choose Python 3.11 or newer, and paste this into **Secrets**:
   ```toml
   TINYFISH_API_KEY = "your-real-key"
   APP_PASSWORD = "choose-a-password"
   ```

**Read this before sharing the link.** Anyone who opens a public app can start agent runs on your account.
Set `APP_PASSWORD` so only people you trust get in. `MAX_RUNS_PER_SESSION` (default 40) caps runs per visit.
Streamlit Cloud also resets the local database when the app restarts, so use **Export watchlist** in the sidebar
to keep a backup.

## Project layout

```
app.py                 Streamlit interface (sidebar, watchlist, add products, history, run log)
dealradar/
  agent.py             All TinyFish calls: queue, poll, parse, balance, search
  analysis.py          Price parsing, formatting and deal logic (no network)
  db.py                SQLite store for products, price history and runs
  theme.py             Styling and the price-tag cards
  config.py            API key lookup and safety limits
tests/                 28 tests, including a full price check with a fake TinyFish client
.env                   Put your key here (ignored by git)
```

Run the tests with `pip install -r requirements-dev.txt` and then `pytest`. They use a fake client, so they never spend credits.

## Troubleshooting

- **"No price found on the page."** The shop may have blocked the agent, the item may be sold out, or the link may be
  a search page. Turn on Stealth browser, use the direct link to one product, and try again.
- **A check takes over a minute.** Normal for big shops. The agent drives a real browser. Keep the tab open until it finishes.
- **"TinyFish rejected this API key."** Create a new key. Keys are shown only once and cannot be recovered.
- **Rate limit message.** Lower "Products checked at once" in the sidebar.

Please respect each shop's terms of service, and use this for your own price tracking.

## Ideas to extend it

- Email or Telegram alert when a Target hit happens (add a webhook or a small scheduler)
- Compare the same product across stores with TinyFish Search plus one agent run per store
- Scheduled daily checks with GitHub Actions or a cron job calling the same `run_checks` function
