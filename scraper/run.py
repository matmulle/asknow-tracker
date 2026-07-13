#!/usr/bin/env python3
"""
Scrapes RITM tickets from ServiceNow portal and stores them in PostgreSQL.

Usage:
  python -m scraper.run                        # full sync
  python -m scraper.run --headed               # show browser window
  python -m scraper.run --headed --debug       # save screenshots + HTML to debug/
  python -m scraper.run --ticket RITM1234567   # single ticket
"""
import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from playwright.async_api import Page, async_playwright
from sqlalchemy.dialects.postgresql import insert as pg_insert

from db.models import Ticket
from db.session import AsyncSessionLocal

load_dotenv()

BASE_URL = "https://visaasknow.service-now.com"
_snow_id = os.environ["SNOW_ID"]  # full value e.g. DYNAMIC90d1921e5f510100a9ad2572f2b477fe
LIST_URL = (
    f"{BASE_URL}/sp?id=ritm_list&table=sc_req_item"
    f"&filter=request.requested_for{_snow_id}"
    f"%5EORrequest.opened_by{_snow_id}"
    "&d=desc&o=number"
)
DEBUG_DIR = Path("debug")


async def snapshot(page: Page, name: str) -> None:
    """Save a screenshot and HTML dump to debug/ for offline inspection."""
    DEBUG_DIR.mkdir(exist_ok=True)
    await page.wait_for_timeout(1000)
    await page.screenshot(path=DEBUG_DIR / f"{name}.png", full_page=True)
    for _ in range(5):
        try:
            html = await page.content()
            (DEBUG_DIR / f"{name}.html").write_text(html, encoding="utf-8")
            break
        except Exception:
            await page.wait_for_timeout(1000)
    print(f"  [debug] saved debug/{name}.png + .html")


async def login(page: Page, debug: bool = False, navigate: bool = True) -> bool:
    """
    State-machine login: handles the full SSO chain in any order.
    ServiceNow → Microsoft SSO → Visa ADFS (password) → ADFS OTP → ServiceNow
    Set navigate=False when the page is already on an auth URL (e.g. a popup).
    """
    if navigate:
        print(f"Navigating to: {LIST_URL}")
        await page.goto(LIST_URL, wait_until="domcontentloaded", timeout=60_000)

    for step in range(10):
        await page.wait_for_timeout(2000)  # let JS redirects settle
        from urllib.parse import urlparse as _urlparse
        _p = _urlparse(page.url)
        host, path = _p.netloc, _p.path
        print(f"  [{step}] {host}{path}")

        if debug:
            await snapshot(page, f"auth_{step:02d}")

        # Done: on ServiceNow proper (check host, not full URL which contains encoded redirects)
        if host == "visaasknow.service-now.com" and "auth_redirect" not in path:
            print("  Authenticated.")
            break

        # Microsoft SSO: fill email if empty, click Next if visible, else wait for auto-redirect
        if "microsoftonline.com" in host:
            print("  Microsoft SSO...")
            try:
                email_input = await page.query_selector("input[type='email'], input[name='UserName']")
                if email_input:
                    val = await email_input.get_attribute("value") or ""
                    if not val.strip():
                        await page.fill("input[type='email'], input[name='UserName']", os.environ["SNOW_USERNAME"])
                submit = await page.wait_for_selector("input[type='submit']:not([disabled])", timeout=5_000)
                async with page.expect_navigation(wait_until="domcontentloaded", timeout=30_000):
                    await submit.click()
            except Exception:
                # Page may have auto-redirected to ADFS — just wait
                await page.wait_for_timeout(4000)
            continue

        # Visa ADFS
        if host == "adfs.trusted.visa.com":
            # Give the page a moment to render whichever form is shown
            await page.wait_for_timeout(1000)

            # OTP page
            otp_input = await page.query_selector("#ADFS\\.OTP\\.signon")
            if otp_input:
                # Check if the previous OTP attempt failed (error element visible)
                err_el = await page.query_selector("#ADFS\\.OTP\\.error")
                if err_el and await err_el.is_visible():
                    print("  ADFS OTP — previous code rejected, need fresh OTP")
                    os.environ.pop("SNOW_OTP", None)  # force interactive prompt
                print("  ADFS OTP — entering OTP...")
                otp = os.environ.get("SNOW_OTP") or input("\n  OTP required — enter your one-time password: ")
                await otp_input.fill(otp.strip())
                await page.click("#ADFS\\.OTP\\.submit")
                await page.wait_for_load_state("domcontentloaded")
                continue

            # Password page
            password_input = await page.query_selector("input[type='password']")
            if password_input and await password_input.is_visible():
                print("  ADFS — entering password...")
                await password_input.fill(os.environ["SNOW_PASSWORD"])
                await page.wait_for_selector("#submitButton", timeout=10_000)
                await page.click("#submitButton")
                await page.wait_for_load_state("domcontentloaded")
                continue

            # ADFS page still loading or in an intermediate state — wait and retry
            print("  ADFS — waiting for form...")
            continue

        # auth_redirect: ServiceNow mid-redirect, wait it out
        if "auth_redirect" in path:
            print("  auth_redirect — waiting...")
            continue

        # Unknown — wait for user to handle manually
        print(f"  Unknown page — waiting for ServiceNow (2 min)...")
        try:
            await page.wait_for_function(
                "() => location.hostname === 'visaasknow.service-now.com' && !location.pathname.includes('auth_redirect')",
                timeout=120_000,
            )
        except Exception:
            print("ERROR: Timed out waiting for authentication.")
            return False

    # If login ended on something other than the list page, navigate there now
    from urllib.parse import urlparse as _up
    if "id=ritm_list" not in page.url:
        await page.goto(LIST_URL, wait_until="domcontentloaded", timeout=60_000)
        await page.wait_for_timeout(2000)

    print(f"  Now at: {page.url}")
    return True


async def goto_authenticated(page: Page, url: str) -> None:
    """Navigate to a URL, re-running the auth flow if a sign-in popup appears."""
    await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    await page.wait_for_timeout(2000)
    from urllib.parse import urlparse as _up
    host = _up(page.url).netloc
    path = _up(page.url).path
    if host != "visaasknow.service-now.com" or "auth_redirect" in path:
        print("  Session expired — re-authenticating...")
        await login(page)


async def get_all_ticket_numbers(page: Page, debug: bool = False) -> list[dict]:
    """Collect all ticket numbers and sys_ids via Angular scope, across all pages."""
    # is_session_valid already navigated to LIST_URL — only re-navigate if needed
    if "id=ritm_list" not in page.url:
        await goto_authenticated(page, LIST_URL)
    try:
        await page.wait_for_selector("tr[ng-repeat]", timeout=15_000)
    except Exception:
        pass

    tickets: list[dict] = []
    page_num = 1

    while True:
        print(f"Parsing list page {page_num}...")

        if debug:
            await snapshot(page, f"05_list_page_{page_num}")

        # Rows use ng-click with no hrefs — extract number, sys_id, and updated_at
        batch = await page.evaluate("""
            () => {
                const str = v => typeof v === 'object' ? (v.display_value || v.value || '') : String(v || '');
                const rows = document.querySelectorAll('tr[ng-repeat]');
                const results = [];
                for (const row of rows) {
                    try {
                        const scope = angular.element(row).scope();
                        if (scope && scope.item && scope.item.sys_id) {
                            const item = scope.item;
                            // u_status = "YYYY-MM-DD HH:MM GMT - ..." — take exactly 20 chars
                            const list_updated_at = str(item.u_status).slice(0, 20).trim();
                            results.push({
                                number:          str(item.number),
                                sys_id:          str(item.sys_id),
                                list_updated_at: list_updated_at,
                            });
                        }
                    } catch(e) {}
                }
                return results;
            }
        """)

        for item in batch:
            item["href"] = f"/sp?id=ticket&table=sc_req_item&sys_id={item['sys_id']}"

        tickets.extend(batch)

        # Get total pages from Angular scope
        num_pages = await page.evaluate("""
            () => {
                const rows = document.querySelectorAll('tr[ng-repeat]');
                for (const row of rows) {
                    let node = row.parentElement;
                    while (node) {
                        try {
                            const s = angular.element(node).scope();
                            if (s && s.data && s.data.num_pages !== undefined)
                                return {num_pages: s.data.num_pages, row_count: s.data.row_count};
                        } catch(e) {}
                        node = node.parentElement;
                    }
                }
                return {num_pages: 1, row_count: null};
            }
        """)
        total_pages = num_pages.get("num_pages", 1) if num_pages else 1
        row_count = num_pages.get("row_count") if num_pages else None
        print(f"  {len(tickets)} tickets on page {page_num}/{total_pages}" +
              (f" ({row_count} total)" if row_count else ""))

        if page_num >= total_pages:
            break

        page_num += 1
        next_url = LIST_URL + f"&p={page_num}"
        await page.goto(next_url, wait_until="domcontentloaded", timeout=60_000)
        try:
            await page.wait_for_selector("tr[ng-repeat]", timeout=10_000)
        except Exception:
            pass

    # Deduplicate
    seen: set[str] = set()
    unique = []
    for t in tickets:
        if t["number"] not in seen:
            seen.add(t["number"])
            unique.append(t)

    return unique


async def fetch_ticket_portal(page: Page, sys_id: str) -> dict:
    """Navigate to a ticket's portal page and extract all fields + variables via Angular scope."""
    url = f"{BASE_URL}/sp?id=ticket&table=sc_req_item&sys_id={sys_id}"
    await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    # Wait for Angular to render variables, then a short settle time
    try:
        await page.wait_for_selector('[ng-repeat="variable in data.variables"]', timeout=8_000)
        await page.wait_for_timeout(1000)
    except Exception:
        await page.wait_for_timeout(2000)

    return await page.evaluate(r"""
        () => {
            const result = {};
            const isSysId = s => /^[a-f0-9]{32}$/.test(s);

            // --- Visible variables: DOM text gives correct resolved display values ---
            const varRows = document.querySelectorAll('[ng-repeat="variable in data.variables"]');
            for (const row of varRows) {
                const label_el = row.querySelector('label.ng-binding');
                const value_el = row.querySelector('label.ng-binding ~ div.ng-binding');
                if (label_el && value_el) {
                    const label = label_el.innerText.trim();
                    const val   = value_el.innerText.trim();
                    if (label && val && !isSysId(val)) result[label] = val;
                }
            }

            // --- Hidden variables + scope fallback: Angular scope extraction.
            // Some variables (list collector summaries, lookup types) are filtered out of
            // the DOM by ng-if. They appear in scope with label=" " and value="Key: val".
            const scopeEl = document.querySelector('[ng-if="data.variables.length > 0"]');
            if (scopeEl) {
                let node = scopeEl;
                while (node) {
                    try {
                        const scope = angular.element(node).scope();
                        if (scope && scope.data && scope.data.variables) {
                            for (const v of scope.data.variables) {
                                // displayValue is often null — use value as fallback
                                // String(null) = "null" in JS, so guard explicitly
                                const raw = (v.displayValue != null && v.displayValue !== '')
                                    ? v.displayValue
                                    : (v.value || '');
                                const val = String(raw).trim();
                                if (!val || isSysId(val)) continue;

                                const label = (v.question_text || v.label || '').trim();
                                if (label && !(label in result)) {
                                    result[label] = val;
                                } else if (!label) {
                                    // Empty label: try parsing "Key: value" from the value itself
                                    const m = val.match(/^([^:]+):\s*(.+)$/);
                                    if (m) result[m[1].trim()] = m[2].trim();
                                }
                            }
                            if (scope.data.requestNumber) result['request_number'] = scope.data.requestNumber;
                            break;
                        }
                    } catch(e) {}
                    node = node.parentElement;
                }
            }

            // --- data.fields: structured sidebar fields ---
            const fieldEls = document.querySelectorAll('[ng-repeat="field in data.fields"]');
            for (const el of fieldEls) {
                let node = el;
                while (node) {
                    try {
                        const scope = angular.element(node).scope();
                        if (scope && scope.data && scope.data.fields) {
                            for (const f of scope.data.fields) {
                                const label = (f.label || f.name || '').trim();
                                const val = String(f.displayValue !== undefined ? f.displayValue : f.value || '').trim();
                                if (label && val && !isSysId(val) && !(label in result)) result[label] = val;
                            }
                            break;
                        }
                    } catch(e) {}
                    node = node.parentElement;
                }
            }

            // Current stage: the bold .stage-on element in the stage progress list
            const stageOn = document.querySelector('.stage-on');
            if (stageOn) result['current_stage'] = stageOn.innerText.trim();

            // Title from heading
            const font = document.querySelector('.panel-heading font');
            if (font) {
                const parts = font.innerText.trim().split(' - ');
                if (parts.length > 1) result['short_description'] = parts.slice(1).join(' - ').trim();
            }
            return result;
        }
    """)


async def fetch_all_ticket_data(page: Page, tickets: list[dict]) -> dict:
    """
    Fetch all ticket data: basic fields via REST API (one batch call),
    variables via portal page navigation (sc_item_option is forbidden via REST).
    Returns a dict keyed by sys_id.
    """
    sys_ids = [t["sys_id"] for t in tickets]

    # Step 1: batch fetch basic fields via REST API
    # Step 1: batch-fetch basic structured fields via REST API (one request for all tickets)
    rest_result = await page.evaluate("""
        async (sysIds) => {
            const disp = v => {
                if (v === null || v === undefined) return '';
                if (typeof v === 'object') {
                    const dv = v.display_value;
                    if (dv !== null && dv !== undefined && dv !== '') return String(dv).trim();
                    return String(v.value ?? '').trim();
                }
                return String(v).trim();
            };
            const headers = {Accept: 'application/json', 'X-UserToken': window.g_ck || ''};
            const FIELDS = 'sys_id,number,short_description,state,stage,opened_at,sys_updated_on,' +
                            'due_date,requested_for,request,cat_item,assignment_group,assigned_to,approval';
            const q = encodeURIComponent('sys_idIN' + sysIds.join(','));
            try {
                const r = await fetch(
                    `/api/now/table/sc_req_item?sysparm_query=${q}&sysparm_display_value=all` +
                    `&sysparm_fields=${FIELDS}&sysparm_limit=${sysIds.length}`,
                    {headers}
                );
                const body = await r.json();
                const out = {};
                for (const item of (body.result || [])) {
                    const sid = item.sys_id?.value || item.sys_id;
                    out[sid] = {
                        number:           disp(item.number),
                        short_description:disp(item.short_description),
                        state:            disp(item.state),
                        stage:            disp(item.stage),
                        opened_at:          disp(item.opened_at),
                        updated_at:         disp(item.sys_updated_on),
                        expected_delivery:  disp(item.due_date),
                        requested_for:      disp(item.requested_for),
                        request_number:     disp(item.request),
                        cat_item:           disp(item.cat_item),
                        assignment_group:   disp(item.assignment_group),
                        assigned_to:        disp(item.assigned_to),
                        approval:           disp(item.approval),
                    };
                }
                return out;
            } catch(e) { return {}; }
        }
    """, sys_ids)

    out = rest_result or {}

    # Step 2: navigate to each ticket's portal page to extract variables
    # (sc_item_option is forbidden via REST API)
    for ticket in tickets:
        sid = ticket["sys_id"]
        print(f"  {ticket['number']}", end=" ... ", flush=True)
        try:
            portal_fields = await fetch_ticket_portal(page, sid)
            # Merge: portal fields override REST fields where both exist, adds variables
            merged = {**out.get(sid, {}), **portal_fields}
            out[sid] = merged
            print(f"{len(merged)} fields")
        except Exception as e:
            print(f"ERROR: {e}")

    return out


async def upsert_ticket(session, number: str, href: str, fields: dict) -> None:
    """Upsert a ticket into PostgreSQL."""
    raw = dict(fields)

    def pop(*keys):
        for k in keys:
            v = raw.pop(k, None)
            if v:
                return v
        return None

    known = {
        "short_description": pop("short_description", "short_desc"),
        "state":             pop("state"),
        "stage":             pop("stage"),
        "current_stage":     pop("current_stage"),
        "opened_at":         pop("opened_at", "Created", "opened", "created"),
        "updated_at":        pop("updated_at", "Updated", "updated", "updated_on", "sys_updated_on"),
        "expected_delivery": pop("expected_delivery", "due_date"),
        "requested_by":      pop("requested_for"),
        "requested_for":     pop("Requested for"),
        "groups":            pop("Groups"),
        "group_name":        pop("Group Name"),
        "roles":             pop("Role(s)"),
        "application":       pop("Related Application / Component Service"),
        "category":          pop("category"),
        "request_number":    pop("request_number", "request"),
        "detail_url":        href if href.startswith("http") else f"{BASE_URL}{href}",
    }

    values = {
        "number": number,
        "raw_fields": raw,
        "synced_at": datetime.now(timezone.utc),
        **{k: v for k, v in known.items() if v is not None},
    }

    stmt = (
        pg_insert(Ticket)
        .values(**values)
        .on_conflict_do_update(
            index_elements=["number"],
            set_={k: values[k] for k in values if k != "number"},
        )
    )
    await session.execute(stmt)
    await session.commit()


CHROME_DATA = Path(".chrome_data")  # persistent Chrome profile for SSO session


async def is_session_valid(page: Page) -> bool:
    """Navigate to LIST_URL and check if the session is authenticated."""
    try:
        await page.goto(LIST_URL, wait_until="domcontentloaded", timeout=20_000)
        from urllib.parse import urlparse as _up
        _p = _up(page.url)
        return _p.netloc == "visaasknow.service-now.com" and "auth_redirect" not in _p.path
    except Exception:
        return False


async def main() -> None:
    parser = argparse.ArgumentParser(description="Sync ServiceNow tickets to local DB")
    parser.add_argument("--headed", action="store_true", help="Show Playwright browser (for debugging scraping)")
    parser.add_argument("--debug", action="store_true", help="Save screenshots + HTML to debug/")
    parser.add_argument("--ticket", metavar="RITM", help="Scrape a single ticket only")
    parser.add_argument("--reauth", action="store_true", help="Force re-authentication via Chrome")
    parser.add_argument("--full-sync", action="store_true", help="Re-scrape all tickets regardless of updated_at")
    args = parser.parse_args()

    async with async_playwright() as p:
        import shutil as _shutil

        if args.reauth and CHROME_DATA.exists():
            _shutil.rmtree(CHROME_DATA)

        # Open headed if no saved profile yet, headless otherwise
        needs_login = not CHROME_DATA.exists() or not any(CHROME_DATA.iterdir())
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(CHROME_DATA),
            channel="chrome",
            headless=not needs_login and not args.headed,
        )
        page = context.pages[0] if context.pages else await context.new_page()

        # If session is no longer valid, show Chrome headed for re-login in the same context
        if not needs_login and not await is_session_valid(page):
            print("Session expired — please log in again in the Chrome window.")
            needs_login = True

        if needs_login:
            print("Opening Chrome for login — complete your Visa SSO in the browser window.")
            await page.goto(LIST_URL, wait_until="domcontentloaded", timeout=60_000)
            print("Waiting for you to reach the ticket list...")
            await page.wait_for_selector("tr[ng-repeat]", timeout=300_000)
            print("Logged in.")

        tickets = await get_all_ticket_numbers(page, debug=args.debug)

        if not tickets:
            print("No tickets found. Run with --headed --debug to capture page structure.")
            await context.close()
            sys.exit(1)

        if args.ticket:
            tickets = [t for t in tickets if t["number"] == args.ticket]
            if not tickets:
                print(f"Ticket {args.ticket} not found in list.")
                await context.close()
                sys.exit(1)

        # Skip tickets unchanged since last ingestion (unless --full-sync)
        if not args.ticket and not args.full_sync:
            from sqlalchemy import select as _select
            async with AsyncSessionLocal() as _s:
                _res = await _s.execute(_select(Ticket.number, Ticket.updated_at))
                db_updated = {row.number: (row.updated_at or "") for row in _res}

            changed, skipped = [], 0
            for t in tickets:
                db_val = db_updated.get(t["number"], "")
                list_val = t.get("list_updated_at", "")
                # Compare first 16 chars (YYYY-MM-DD HH:MM) to ignore minor format diffs
                # Only skip if both timestamps are present and match
                if list_val and db_val and db_val[:16] == list_val[:16]:
                    skipped += 1
                elif not list_val and t["number"] in db_updated:
                    # u_status is empty for this ticket (e.g. closed/cancelled) — can't compare;
                    # assume unchanged since it's already in the DB. Use --full-sync to force.
                    skipped += 1
                else:
                    changed.append(t)
            print(f"\n{len(changed)} tickets changed, {skipped} unchanged — skipping unchanged.")
            tickets = changed

        print(f"Fetching data for {len(tickets)} tickets...")
        all_data = await fetch_all_ticket_data(page, tickets)

        async with AsyncSessionLocal() as session:
            for ticket in tickets:
                sid = ticket["sys_id"]
                data = all_data.get(sid)
                if not data:
                    print(f"  {ticket['number']} — no data returned, skipping")
                    continue
                variables = data.pop("variables", {})
                fields = {**data, **variables}
                # Store u_status timestamp as updated_at so next comparison is exact
                if ticket.get("list_updated_at"):
                    fields["updated_at"] = ticket["list_updated_at"]
                href = ticket["href"]
                try:
                    await upsert_ticket(session, ticket["number"], href, fields)
                except Exception as e:
                    print(f"    ERROR: {e}")

        # Close all pages cleanly (navigating away avoids showing the session-expired banner)
        for p in context.pages:
            try:
                await p.close()
            except Exception:
                pass
        await context.close()
        print(f"\nDone. {len(tickets)} tickets synced.")


if __name__ == "__main__":
    asyncio.run(main())
