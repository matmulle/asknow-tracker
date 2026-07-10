# asknow-tracker

## Purpose
Scrapes ServiceNow RITM tickets from the Visa AskNow portal into a local PostgreSQL database and displays them in a local web UI. The portal's built-in list view is too broad to distinguish tickets — this tool solves that.

## Target system
- URL: `https://visaasknow.service-now.com/sp?id=ritm_list&table=sc_req_item`
- Auth: Persistent Chrome profile (`.chrome_data/`) — macOS SSO handles login automatically
- REST API returns 403 for basic auth (SSO only); ticket variables scraped via portal page DOM

## Tech stack
- **Scraper:** Python + Playwright (`launch_persistent_context` with system Chrome for SSO)
- **Database:** PostgreSQL via SQLAlchemy (async) + Alembic for migrations
- **Web UI:** FastAPI + Jinja2 + uvicorn
- **Package manager:** uv (`system-certs = true` handles corporate SSL proxy)
- **Config:** `python-dotenv` — loaded from `.env`

## Project structure
```
asknow-tracker/
├── scraper/run.py       # Main scraper: auth, list parsing, ticket scraping, upsert
├── db/models.py         # SQLAlchemy Ticket model
├── db/session.py        # Async DB session factory
├── web/main.py          # FastAPI routes + SSE sync stream endpoint
├── web/templates/       # Jinja2 templates (base.html, index.html, ticket.html)
├── migrations/          # Alembic migration versions (0001–0006)
├── .chrome_data/        # Persistent Chrome profile for SSO (gitignored)
├── .env                 # Secrets (gitignored)
└── docker-compose.yml   # PostgreSQL only
```

## Environment variables (.env)
```
SNOW_ID=DYNAMICyour_sys_id   # full filter value from the ticket list URL
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/asknow
```
Find your SNOW_ID in the list URL after `request.requested_forDYNAMIC`.

## Run commands
```bash
# Start postgres
docker compose up -d

# First run — opens Chrome window, complete SSO (macOS handles MFA automatically)
uv run python -m scraper.run

# Subsequent runs — fully headless, session reused from .chrome_data/
uv run python -m scraper.run

# Start web UI
uv run uvicorn web.main:app --reload --port 8000
# → http://localhost:8000
```

## Scraper flags
```
--reauth      Wipe .chrome_data/ and force a new Chrome login
--full-sync   Re-scrape all tickets ignoring the updated_at comparison
--ticket RITM Scrape a single ticket only (e.g. --ticket RITM26700392)
--headed      Show the Playwright browser window (useful for debugging scraping)
--debug       Save screenshots + HTML dumps to debug/
```

## Auth flow
1. Open persistent Chrome context from `.chrome_data/` using system Chrome (`channel="chrome"`)
2. If `.chrome_data/` is empty or `--reauth`: open Chrome headed → user logs in via macOS SSO (no OTP needed)
3. If session expired: reopen Chrome headed automatically and wait for user to complete SSO
4. After login: switch to headless for all scraping — session persists in `.chrome_data/`

## Scraping strategy
1. Navigate to the ticket list (reuses existing page from session check — no double navigation)
2. Extract ticket numbers, sys_ids, and `u_status` timestamps from Angular scope via JS
3. Compare `u_status` timestamp against stored `updated_at` — skip unchanged tickets
4. REST API batch-fetches basic fields for all changed tickets in one call
5. For each changed ticket: navigate portal page, extract fields + variables via DOM + Angular scope
6. `sc_item_option` table is forbidden via REST — variables come from portal page DOM only
7. Upsert into PostgreSQL by ticket number (idempotent)

## Important constraints
- `.env` and `.chrome_data/` are gitignored — never commit
- Corporate proxy blocks PyPI inside Docker — web UI and scraper always run locally
- `sc_item_option` REST API returns 403 — no workaround, portal scraping required for variables
- `sys_updated_on` is null in the list page Angular scope — use `u_status` field for change detection
