#!/usr/bin/env python3
"""
Vantaca -> Google Calendar sync

Scrapes a community association's Vantaca/CAMS resident portal calendar and
mirrors it into a Google Calendar, keeping the two in sync automatically
(creates new events, updates changed ones, and removes deleted ones).
"""

import os
import re
import json
import logging
from datetime import datetime
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from google.oauth2 import service_account
from googleapiclient.discovery import build

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# --- Config (all secrets come from environment variables) --------------------
LOGIN_URL     = "https://portal.camsmgt.com/public?modal=login"
CALENDAR_URL  = "https://portal.camsmgt.com/community/calendar"
CAMS_USER     = os.environ["CAMS_USER"]
CAMS_PASS     = os.environ["CAMS_PASS"]
CALENDAR_NAME = os.environ.get("GOOGLE_CALENDAR_NAME", "Community Calendar (CAMS)")
CALENDAR_ID   = os.environ.get("GOOGLE_CALENDAR_ID")  # optional; falls back to name lookup

SERVICE_ACCOUNT_JSON = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]

SCOPES = ["https://www.googleapis.com/auth/calendar"]
SOURCE_TAG = "vantaca-sync"


# --- Scrape ------------------------------------------------------------------
def scrape_events() -> list[dict]:
    events = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36")
        )
        page = context.new_page()

        log.info("Logging in...")
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        page.fill('input[name="email"]', CAMS_USER)
        page.fill('input[name="password"]', CAMS_PASS)
        page.click('button[type="submit"]')
        page.wait_for_timeout(3000)

        log.info("Navigating to calendar...")
        page.goto(CALENDAR_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(8000)

        # Events render inside an iframe
        frame = page.frame_locator("iframe").first
        frame.locator(".k-event").first.wait_for(timeout=15000)
        html = frame.locator("html").inner_html()
        browser.close()

    soup = BeautifulSoup(html, "html.parser")
    event_divs = soup.find_all("div", class_="k-event")

    for div in event_divs:
        aria = div.get("aria-label", "")
        uid  = div.get("data-uid", "")
        title_tag = div.find("strong")
        title = title_tag.get_text(strip=True) if title_tag else aria

        parsed = parse_aria(aria, title)
        if not parsed:
            log.warning(f"Could not parse: {aria}")
            continue

        spans = div.find_all("span", recursive=False)
        desc = ""
        for s in spans:
            if s.get("class") and any(c in ["k-event-actions"] for c in s.get("class", [])):
                continue
            t = s.get_text(strip=True)
            if t and t != title:
                desc = t
                break

        events.append({"uid": uid, "title": title, "desc": desc, **parsed})
        log.info(f"  Found: {title} -> {parsed}")

    log.info(f"Scraped {len(events)} events")
    return events


def parse_aria(aria: str, title: str) -> dict | None:
    rest = aria
    if title and aria.startswith(title):
        rest = aria[len(title):].strip()
    rest = re.sub(r"^on\s+", "", rest, flags=re.IGNORECASE)

    m = re.match(r"\w+,\s+(\w+ \d+,\s+\d{4})\s+\(all day\)", rest, re.IGNORECASE)
    if m:
        dt = datetime.strptime(m.group(1), "%B %d, %Y")
        return {"all_day": True, "start": dt.date().isoformat(), "end": dt.date().isoformat()}

    m = re.match(r"\w+,\s+(\w+ \d+,\s+\d{4})\s+at\s+([\d:]+ [AP]M)\s+to\s+([\d:]+ [AP]M)", rest, re.IGNORECASE)
    if m:
        date_str, start_str, end_str = m.group(1), m.group(2), m.group(3)
        start_dt = datetime.strptime(f"{date_str} {start_str}", "%B %d, %Y %I:%M %p")
        end_dt   = datetime.strptime(f"{date_str} {end_str}",   "%B %d, %Y %I:%M %p")
        return {"all_day": False, "start": start_dt.isoformat(), "end": end_dt.isoformat()}

    return None


# --- Google Calendar ---------------------------------------------------------
def get_calendar_service():
    info = json.loads(SERVICE_ACCOUNT_JSON)
    creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    return build("calendar", "v3", credentials=creds)


def resolve_calendar_id(service) -> str:
    """Use GOOGLE_CALENDAR_ID if provided, otherwise find the calendar by name."""
    if CALENDAR_ID:
        return CALENDAR_ID
    calendars = service.calendarList().list().execute()
    for cal in calendars.get("items", []):
        if cal["summary"] == CALENDAR_NAME:
            return cal["id"]
    available = [c["summary"] for c in calendars.get("items", [])]
    log.info(f"Available calendars: {available}")
    raise ValueError(
        f"Calendar '{CALENDAR_NAME}' not found. "
        "Share it with the service account, or set GOOGLE_CALENDAR_ID."
    )


def get_existing_events(service, calendar_id: str) -> dict[str, str]:
    result = {}
    page_token = None
    while True:
        resp = service.events().list(
            calendarId=calendar_id,
            privateExtendedProperty=f"source={SOURCE_TAG}",
            pageToken=page_token,
            maxResults=250,
        ).execute()
        for ev in resp.get("items", []):
            uid = ev.get("extendedProperties", {}).get("private", {}).get("vantacaUid")
            if uid:
                result[uid] = ev["id"]
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return result


def build_gcal_event(ev: dict) -> dict:
    if ev["all_day"]:
        start = {"date": ev["start"]}
        end   = {"date": ev["end"]}
    else:
        start = {"dateTime": ev["start"], "timeZone": "America/New_York"}
        end   = {"dateTime": ev["end"],   "timeZone": "America/New_York"}

    return {
        "summary": ev["title"],
        "description": ev.get("desc", ""),
        "start": start,
        "end": end,
        "extendedProperties": {"private": {"source": SOURCE_TAG, "vantacaUid": ev["uid"]}},
    }


def sync_to_google(events: list[dict]):
    service     = get_calendar_service()
    calendar_id = resolve_calendar_id(service)
    existing    = get_existing_events(service, calendar_id)

    scraped_uids = {ev["uid"] for ev in events if ev["uid"]}

    for ev in events:
        uid = ev["uid"]
        if not uid:
            continue
        gcal_ev = build_gcal_event(ev)
        if uid in existing:
            log.info(f"Updating: {ev['title']}")
            service.events().update(calendarId=calendar_id, eventId=existing[uid], body=gcal_ev).execute()
        else:
            log.info(f"Creating: {ev['title']}")
            service.events().insert(calendarId=calendar_id, body=gcal_ev).execute()

    # Remove events that no longer exist in the source calendar
    for uid, gid in existing.items():
        if uid not in scraped_uids:
            log.info(f"Deleting removed event: {uid}")
            service.events().delete(calendarId=calendar_id, eventId=gid).execute()

    log.info("Sync complete.")


# --- Main --------------------------------------------------------------------
if __name__ == "__main__":
    events = scrape_events()
    if events:
        sync_to_google(events)
    else:
        log.warning("No events scraped; skipping Google Calendar update.")
