# Vantaca → Google Calendar Sync

A Python automation that keeps a Google Calendar in sync with a community
association's Vantaca/CAMS resident portal, with **no manual work**. It's
designed to run on a daily schedule via GitHub Actions, mirroring the portal's
calendar into Google Calendar, creating new events, updating changed ones, and
removing deleted ones.

## The problem

My HOA's events live inside a resident portal that residents have to log into
to check. There's no calendar feed or export. That means the schedule is easy
to miss and impossible to see alongside everyone's normal Google Calendar.

## What it does

On each run, it automatically:

1. **Logs into** the portal and loads the calendar (a headless browser, since
   the calendar is rendered by JavaScript inside an iframe).
2. **Extracts** each event — title, description, date, and start/end times.
3. **Syncs** the events into a shared Google Calendar through the Google
   Calendar API:
   - new events are **created**
   - changed events are **updated**
   - events removed from the portal are **deleted**

The result is a Google Calendar that always matches the portal, which anyone
can subscribe to.

## How it works

| Step | Tool |
|------|------|
| Log in + render the JS calendar | **Playwright** (headless Chromium) |
| Parse the event HTML | **BeautifulSoup** |
| Read/write calendar events | **Google Calendar API** (service account) |
| Run on a daily schedule | **GitHub Actions** (scheduled cron) |

The sync is **idempotent** — each Google event is tagged with the portal's
unique event ID (via `extendedProperties`), so re-running never creates
duplicates and always converges to match the source.

## Running it

Secrets are passed as environment variables (never committed):

| Variable | Purpose |
|----------|---------|
| `CAMS_USER`, `CAMS_PASS` | Portal login |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Google service-account key (JSON) |
| `GOOGLE_CALENDAR_NAME` | Target calendar name (default provided) |
| `GOOGLE_CALENDAR_ID` | *(optional)* target calendar ID |

Local run:

```bash
pip install -r requirements.txt
playwright install chromium
python sync.py
```

Scheduled run: the included GitHub Actions workflow
(`.github/workflows/sync.yml`) is set up to run it on a daily schedule and can
also be triggered by hand. To enable it, add the secrets above under
**Settings → Secrets and variables → Actions**.

## Notes

- A personal project, built and tested end to end to solve a real problem and
  learn the stack; not currently deployed live.
- Built for my own homeowners' association; portal specifics are particular to
  the Vantaca/CAMS platform.
- No credentials or calendar IDs are stored in the code — everything sensitive
  is injected at runtime.
