# asknow-tracker

A local scraper + web UI for tracking your ServiceNow RITM tickets on the Visa AskNow portal. The portal's built-in list view is too broad to distinguish tickets — this tool pulls them into a local database and gives you a filterable, sortable table.

## Prerequisites

- macOS (SSO login is handled automatically by the system)
- [Google Chrome](https://www.google.com/chrome/) installed
- [uv](https://docs.astral.sh/uv/getting-started/installation/) — Python package manager
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) — for PostgreSQL

## First-time setup

**1. Start PostgreSQL**

```bash
docker compose up -d
```

**2. Create your `.env` file**

```bash
cp .env.example .env   # if it exists, otherwise create it manually
```

`.env` contents:

```
SNOW_ID=DYNAMICyour_sys_id
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/asknow
```

To find your `SNOW_ID`: open the AskNow ticket list in a browser, look at the URL — it contains `request.requested_forDYNAMIC<your_id>`. Copy the full value starting with `DYNAMIC`.

**3. Run database migrations**

```bash
uv run alembic upgrade head
```

**4. First scrape — complete SSO login**

```bash
uv run python -m scraper.run
```

This opens a Chrome window. Log in with your Visa credentials — macOS handles MFA automatically via SSO. Once you reach the ticket list, the scraper takes over and runs headlessly. Your session is saved in `.chrome_data/` for future runs.

## Day-to-day usage

**Sync tickets** (headless, reuses saved session):

```bash
uv run python -m scraper.run
```

**Start the web UI:**

```bash
uv run uvicorn web.main:app --reload --port 8000
```

Then open [http://localhost:8000](http://localhost:8000).

You can also trigger a sync directly from the web UI via the **Sync Tickets** button.

## Scraper flags

| Flag | Description |
|------|-------------|
| `--full-sync` | Re-scrape all tickets, ignoring cached timestamps |
| `--ticket RITM1234567` | Scrape a single ticket only |
| `--headed` | Show the browser window (useful for debugging) |
| `--debug` | Save screenshots + HTML dumps to `debug/` |
| `--reauth` | Wipe the saved Chrome session and log in again |

## Session management

Your Chrome session is stored in `.chrome_data/`. If it expires, the scraper will automatically reopen Chrome for you to log in again. Use `--reauth` to force a fresh login at any time.

## Web UI features

- Filter by State, Request type, Current stage, Requested by / for
- Multi-select checkboxes on every filter dropdown
- Toggle column visibility
- Click column headers to sort
- **Scraped data** button on each row shows all raw fields from ServiceNow
