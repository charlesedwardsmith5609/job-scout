# job-scout
Daily Job Scrape

Scans **7 job board sources** daily and emails new senior EM openings that
match Charles's profile to **charlesedwardsmith5609@gmail.com**.

| Source                      | Type          | Notes
|-----------------------------|---------------|----------
| Greenhouse (21 companies)   | Public API    | Reliable 
| Lever (8 companies)         | Public API    | Reliable 
| RemoteOK                    | Public API    | Reliable 
| Working Nomads              | Public API    | Reliable 
| We Work Remotely            | RSS           | Reliable 
| Wellfound                   | HTML scraping | Best-effort; may return nothing if they change their site
| Built In                    | HTML scraping | Best-effort; same caveat

---

## One-time setup (~10 min)

### 1 — Create a private GitHub repo

github.com → New repository → name it `job-scout` → **Private** → Create.

Upload these files, keeping the folder structure:
```
check_jobs.py
seen_jobs.json
README.md
.github/
  workflows/
    daily_scan.yml
```

### 2 — Get a Gmail App Password

1. Go to **myaccount.google.com → Security** → enable 2-Step Verification
2. Go to **myaccount.google.com/apppasswords**
3. Name: `Job Scout` → Generate
4. Copy the 16-character password (shown once only)

### 3 — Add GitHub Secrets

Repo → **Settings → Secrets and variables → Actions → New repository secret**

| Name | Value |
|------|-------|
| `GMAIL_ADDRESS` | Any Gmail you own to send *from* |
| `GMAIL_APP_PASSWORD` | 16-char App Password from step 2 |

### 4 — Trigger a manual first run

Repo → **Actions → Daily Job Scout → Run workflow → Run workflow**

First run emails all current matching openings (will be many).
Every subsequent run only emails genuinely new postings.

---

## Schedule

Runs every day at **9:00 AM Pacific** (16:00 UTC).

---

## Tuning

**Add a company (Greenhouse):**
Find their Greenhouse board slug in the URL of their jobs page
(`boards-api.greenhouse.io/v1/boards/{slug}/jobs`) and add to
`GREENHOUSE` dict in `check_jobs.py`.

**Add a company (Lever):**
Same idea — their jobs are at `jobs.lever.co/{slug}`.
Add to `LEVER` dict.

**Adjust scoring:**
- `SCORING` — list of (keyword, points) pairs
- `MIN_SCORE` — lower to get more results (try 3); raise to reduce noise
- `TITLE_MUST` / `TITLE_EXCLUDE` — controls which titles pass the filter

**Add a new job board:**
Write a `fetch_xxx()` function that returns a list of dicts with keys:
`id, title, company, url, location, score, keywords, source`
Then call it in `main()`.

---

## About Wellfound and Built In

Both are React/Next.js single-page apps, which means simple HTTP requests
don't get the rendered job data. The scraper tries to extract embedded
`__NEXT_DATA__` JSON (which sometimes works), but it may return zero
results if they change their site structure.

For reliable Wellfound and Built In coverage, also set up their
native email alerts directly:
- **Wellfound:** wellfound.com/jobs → set saved search alert for
  "Engineering Manager" + Remote
- **Built In:** builtin.com/jobs/remote → search "Engineering Manager" →
  "Save search" → enable email alerts

---

## Companies monitored

**Greenhouse (21):** Atlassian, Cloudflare, Coinbase, Confluent, Cribl,
Databricks, Datadog, Dragos, Figma, GitLab, Grafana Labs, HashiCorp,
LaunchDarkly, MongoDB, Notion, PagerDuty, Riot Games, Snyk, Stripe,
Temporal, Vercel

**Lever (8):** dbt Labs, Harness, Honeycomb, Incident.io, Linear, Pulumi,
Retool, Sentry
