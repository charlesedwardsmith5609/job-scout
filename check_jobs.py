#!/usr/bin/env python3
"""
Daily job board scanner — Charles Smith
charlesedwardsmith5609@gmail.com

Sources (broad — discover jobs at ANY company):
  • Remotive       — remote job aggregator, public API
  • Jobicy         — remote job aggregator, public API
  • Arbeitnow      — global job board, public API
  • RemoteOK       — remote job aggregator, public API
  • Working Nomads — remote job aggregator, public API
  • We Work Remotely — remote job board, RSS
  • Wellfound      — startup jobs, HTML scraping (best-effort)
  • Built In       — tech job board, HTML scraping (best-effort)

Priority company boards (fast-track for active applications):
  • Greenhouse boards — specific companies currently in process
  • Lever boards     — specific companies currently in process
"""

import hashlib
import json
import os
import smtplib
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests
try:
    from bs4 import BeautifulSoup
    BS4_OK = True
except ImportError:
    BS4_OK = False

# ── Configuration ───────────────────────────────────────────────────────────────

RECIPIENT  = "charlesedwardsmith5609@gmail.com"
SEEN_FILE  = Path(__file__).parent / "seen_jobs.json"
MIN_SCORE  = 4
REQUEST_TO = 20

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html, */*",
}

# ── Priority company boards ─────────────────────────────────────────────────────
# Keep this SHORT — just companies you are actively in process with.
# Everything else is discovered automatically by the broad sources below.
# Add/remove as your pipeline changes.

PRIORITY_GREENHOUSE = {
    "Dragos":   "dragos",
    "Stripe":   "stripe",
    "Temporal": "temporaltechnologies",
}

PRIORITY_LEVER = {
    "Riot Games": "riotgames",   # uses Lever
}

# ── Scoring ─────────────────────────────────────────────────────────────────────

TITLE_MUST = [
    "engineering manager",
    "software development manager",
    "software engineering manager",
    "head of engineering",
    "director of engineering",
    "vp of engineering",
    "vp, engineering",
    "engineering lead",
    "manager of engineering",
    "manager, engineering",
    "manager, platform",
    "manager, infrastructure",
    "manager, developer",
    "manager, cloud",
    "manager, sre",
    "manager, devops",
    "manager, reliability",
    "manager, backend",
    "manager, compute",
    "technology manager",
    "technical manager",
    "team lead, engineering",
    "engineering team lead",
]

TITLE_EXCLUDE = [
    "software engineer,",    "senior engineer,",     "staff engineer",
    "principal engineer",    "data engineer",        "ml engineer",
    "devops engineer",       "security engineer,",   "site reliability engineer,",
    "product manager",       "technical program manager", "program manager",
    "recruiter",             "designer",             "data scientist",
    "analyst",               "writer",               "coordinator",
    "specialist",            "account ",             "sales",
    "marketing",             "finance",              "legal",
    "support",               "customer success",     "office manager",
]

SCORING = [
    # Charles's core domain — high signal
    ("platform engineering",  6), ("middleware",           6),
    ("secrets management",    6), ("api gateway",          6),
    ("developer experience",  5), ("devex",                5),
    ("infrastructure",        5), ("kubernetes",           5),
    ("distributed systems",   5), ("site reliability",     5),
    ("compute",               5), ("observability",        5),
    ("security platform",     5), ("backend platform",     5),
    ("agentic",               5), ("service mesh",         5),
    ("workload identity",     5), ("live service",         4),
    ("oidc",                  4), ("sre",                  4),
    ("cloud engineering",     4), ("devops",               3),
    ("reliability",           3), ("eks",                  3),
    ("ai platform",           4), ("ml platform",          3),
    ("aws",                   2), ("terraform",            2),
    # Standalone department names (common in job titles like "Manager, Platform")
    ("platform",              3), ("backend",              3),
    ("cloud",                 2), ("data platform",        4),
    # Level and location signals
    ("senior",                2), ("remote",               2),
    ("united states",         1),
]


# Locations that are US/remote-eligible — anything else gets filtered out
LOCATION_ALLOW = {
    "remote", "worldwide", "anywhere", "global", "international",
    "us", "usa", "u.s.", "united states", "north america", "americas",
    "us or canada", "us/canada", "canada",
}

def location_ok(loc: str) -> bool:
    """Return True if the location is plausibly US/remote-eligible."""
    if not loc or not loc.strip():
        return True   # unspecified = allow
    l = loc.lower()
    return any(p in l for p in LOCATION_ALLOW)

def passes_title(title: str) -> bool:
    t = title.lower()
    return (any(k in t for k in TITLE_MUST) and
            not any(k in t for k in TITLE_EXCLUDE))

def score(title: str, description: str = "", location: str = "") -> tuple[int, list[str]]:
    if not passes_title(title):
        return 0, []
    if not location_ok(location):
        return 0, []   # drop international-only roles
    blob  = f"{title} {description} {location}".lower()
    noise = {"senior", "remote", "united states", "aws", "terraform",
             "devops", "reliability", "sre", "eks", "ml platform",
             "ai platform", "canada", "north america"}
    # Base score of 3 so "Senior Engineering Manager" (title only) scores 5
    total, hits = 3, []
    for kw, pts in SCORING:
        if kw in blob:
            total += pts
            if kw not in noise:
                hits.append(kw)
    return total, hits[:6]

def uid(*parts: str) -> str:
    return hashlib.md5("|".join(str(p) for p in parts).encode()).hexdigest()[:16]

# ── HTTP helper ─────────────────────────────────────────────────────────────────

def get(url: str, **kwargs) -> requests.Response | None:
    try:
        r = requests.get(url, timeout=REQUEST_TO, headers=HEADERS, **kwargs)
        r.raise_for_status()
        return r
    except Exception as e:
        print(f"    ⚠  {url[:70]}  → {e}", file=sys.stderr)
        return None

# ── Broad sources ───────────────────────────────────────────────────────────────

def fetch_remotive() -> list[dict]:
    """https://remotive.com/api/remote-jobs  — covers thousands of companies"""
    categories = ["software-dev", "devops-sysadmin", "product"]
    jobs = []
    for cat in categories:
        r = get("https://remotive.com/api/remote-jobs",
                params={"category": cat, "limit": 150})
        if not r:
            continue
        for j in r.json().get("jobs", []):
            title = j.get("title", "")
            desc  = j.get("description", "")
            loc   = j.get("candidate_required_location", "Remote")
            sc, kw = score(title, desc, loc)
            if sc >= MIN_SCORE:
                jobs.append({
                    "id":       f"rm_{j.get('id','')}",
                    "title":    title,
                    "company":  j.get("company_name", "Unknown"),
                    "url":      j.get("url", ""),
                    "location": loc or "Remote",
                    "score":    sc, "keywords": kw, "source": "Remotive",
                })
    return jobs

def fetch_jobicy() -> list[dict]:
    """https://jobicy.com/api/v2/remote-jobs  — remote job aggregator"""
    r = get("https://jobicy.com/api/v2/remote-jobs",
            params={"industry": "eng-tech", "count": 100})
    if not r:
        return []
    jobs = []
    for j in r.json().get("jobs", []):
        title = j.get("jobTitle", "")
        desc  = j.get("jobDescription", "") or j.get("jobExcerpt", "")
        loc   = j.get("jobGeo", "Remote")
        sc, kw = score(title, desc, loc)
        if sc >= MIN_SCORE:
            jobs.append({
                "id":       f"jc_{j.get('id', uid(j.get('url','')))}",
                "title":    title,
                "company":  j.get("companyName", "Unknown"),
                "url":      j.get("url", ""),
                "location": loc,
                "score":    sc, "keywords": kw, "source": "Jobicy",
            })
    return jobs

def fetch_arbeitnow() -> list[dict]:
    """https://www.arbeitnow.com/api/job-board-api  — broad global board, paginated"""
    jobs = []
    page = 1
    while page <= 5:      # cap at 5 pages (~500 jobs) to stay fast
        r = get("https://www.arbeitnow.com/api/job-board-api",
                params={"page": page})
        if not r:
            break
        data = r.json()
        items = data.get("data", [])
        if not items:
            break
        for j in items:
            # Only remote jobs
            if not j.get("remote"):
                continue
            title = j.get("title", "")
            desc  = j.get("description", "")
            loc   = j.get("location", "Remote")
            sc, kw = score(title, desc, loc)
            if sc >= MIN_SCORE:
                jobs.append({
                    "id":       f"an_{uid(j.get('slug',''), str(page))}",
                    "title":    title,
                    "company":  j.get("company_name", "Unknown"),
                    "url":      j.get("url", ""),
                    "location": loc,
                    "score":    sc, "keywords": kw, "source": "Arbeitnow",
                })
        # Stop if there's no next page
        if not data.get("links", {}).get("next"):
            break
        page += 1
    return jobs

def fetch_remoteok() -> list[dict]:
    """https://remoteok.com/api  — aggregated remote jobs"""
    r = get("https://remoteok.com/api", params={"tags": "manager,engineering"})
    if not r:
        return []
    jobs = []
    for j in r.json():
        if not isinstance(j, dict) or "position" not in j:
            continue
        title = j.get("position", "")
        desc  = j.get("description", "")
        loc   = j.get("location", "Remote")
        sc, kw = score(title, desc, loc)
        if sc >= MIN_SCORE:
            jobs.append({
                "id":       f"ro_{j.get('id','')}",
                "title":    title,
                "company":  j.get("company", "Unknown"),
                "url":      j.get("url", ""),
                "location": loc,
                "score":    sc, "keywords": kw, "source": "RemoteOK",
            })
    return jobs

def fetch_workingnomads() -> list[dict]:
    """https://www.workingnomads.com/api  — remote job aggregator"""
    jobs, url = [], "https://www.workingnomads.com/api/exposed_jobs/"
    params, pages = {"category": "engineering", "limit": 100}, 0
    while url and pages < 5:
        r = get(url, params=params)
        if not r:
            break
        data = r.json()
        for j in data.get("results", []):
            title = j.get("title", "")
            desc  = j.get("description", "")
            sc, kw = score(title, desc, "remote")
            if sc >= MIN_SCORE:
                jobs.append({
                    "id":       f"wn_{j.get('id','')}",
                    "title":    title,
                    "company":  j.get("company_name", "Unknown"),
                    "url":      j.get("url", ""),
                    "location": "Remote",
                    "score":    sc, "keywords": kw, "source": "Working Nomads",
                })
        url, params, pages = data.get("next"), {}, pages + 1
    return jobs

def fetch_wwr() -> list[dict]:
    """We Work Remotely — RSS feeds"""
    feeds = [
        "https://weworkremotely.com/categories/remote-senior-exec-management-jobs.rss",
        "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss",
        "https://weworkremotely.com/categories/remote-back-end-programming-jobs.rss",
    ]
    jobs = []
    for feed_url in feeds:
        r = get(feed_url)
        if not r:
            continue
        try:
            root = ET.fromstring(r.content)
        except ET.ParseError:
            continue
        for item in root.findall(".//item"):
            raw   = item.findtext("title") or ""
            parts = raw.split("|", 1)
            company = parts[0].strip() if len(parts) > 1 else "Unknown"
            title   = parts[-1].strip()
            desc    = item.findtext("description") or ""
            link    = item.findtext("link") or ""
            sc, kw  = score(title, desc, "remote")
            if sc >= MIN_SCORE:
                jobs.append({
                    "id":       f"wwr_{uid(link)}",
                    "title":    title,
                    "company":  company,
                    "url":      link,
                    "location": "Remote",
                    "score":    sc, "keywords": kw, "source": "We Work Remotely",
                })
    return jobs

def fetch_wellfound() -> list[dict]:
    """Wellfound — HTML scraping, best-effort"""
    if not BS4_OK:
        return []
    r = get("https://wellfound.com/role/r/engineering-manager")
    if not r:
        return []
    soup   = BeautifulSoup(r.text, "lxml")
    jobs   = []
    script = soup.find("script", {"id": "__NEXT_DATA__"})
    if not script:
        print("    ⚠  Wellfound: no embedded JSON (JS-rendered page)", file=sys.stderr)
        return []
    try:
        data     = json.loads(script.string)
        pp       = data.get("props", {}).get("pageProps", {})
        listings = pp.get("jobListings") or pp.get("jobs") or []
        for j in listings:
            title   = j.get("title") or j.get("jobTitle", "")
            company = (j.get("company") or {}).get("name", "Unknown")
            loc     = (j.get("locationNames") or ["Remote"])
            loc     = loc[0] if isinstance(loc, list) else "Remote"
            link    = j.get("url") or j.get("slug", "")
            if link and not link.startswith("http"):
                link = f"https://wellfound.com/jobs/{link}"
            desc    = j.get("description", "")
            sc, kw  = score(title, desc, loc)
            if sc >= MIN_SCORE:
                jobs.append({
                    "id":       f"wf_{uid(link or title)}",
                    "title":    title, "company": company, "url": link,
                    "location": loc, "score": sc, "keywords": kw,
                    "source":   "Wellfound",
                })
    except Exception as e:
        print(f"    ⚠  Wellfound parse error: {e}", file=sys.stderr)
    return jobs

def fetch_builtin() -> list[dict]:
    """Built In — HTML scraping, best-effort"""
    if not BS4_OK:
        return []
    r = get("https://builtin.com/jobs/remote",
            params={"search[keywords]": "engineering manager",
                    "search[job_type]": "remote"})
    if not r:
        return []
    soup   = BeautifulSoup(r.text, "lxml")
    jobs   = []
    script = soup.find("script", {"id": "__NEXT_DATA__"})
    if not script:
        return []
    try:
        data     = json.loads(script.string)
        pp       = data.get("props", {}).get("pageProps", {})
        listings = pp.get("jobs") or pp.get("jobListings") or []
        for j in listings:
            title   = j.get("title", "")
            company = (j.get("company") or {}).get("name", "Unknown")
            loc     = j.get("builtInRemoteStatus", "Remote")
            link    = j.get("url") or j.get("slug", "")
            if link and not link.startswith("http"):
                link = f"https://builtin.com{link}"
            sc, kw  = score(title, j.get("description", ""), loc)
            if sc >= MIN_SCORE:
                jobs.append({
                    "id":       f"bi_{uid(link or title)}",
                    "title":    title, "company": company, "url": link,
                    "location": loc, "score": sc, "keywords": kw,
                    "source":   "Built In",
                })
    except Exception as e:
        print(f"    ⚠  Built In parse error: {e}", file=sys.stderr)
    return jobs

# ── Priority company boards (fast-track) ────────────────────────────────────────

def fetch_greenhouse_board(name: str, slug: str) -> list[dict]:
    r = get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
            params={"content": "true"}, headers={})
    if not r:
        return []
    jobs = []
    for j in r.json().get("jobs", []):
        title = j.get("title", "")
        loc   = " ".join(o.get("name","") for o in j.get("offices", []))
        sc, kw = score(title, j.get("content",""), loc)
        if sc >= MIN_SCORE:
            jobs.append({
                "id":       f"gh_{j['id']}",
                "title":    title, "company": name,
                "url":      j.get("absolute_url",""),
                "location": loc or "Not specified",
                "score":    sc, "keywords": kw, "source": "Greenhouse",
            })
    return jobs

def fetch_lever_board(name: str, slug: str) -> list[dict]:
    r = get(f"https://api.lever.co/v0/postings/{slug}",
            params={"mode": "json", "limit": 250})
    if not r:
        return []
    jobs = []
    for j in r.json():
        title = j.get("text", "")
        loc   = j.get("categories",{}).get("location","")
        sc, kw = score(title,
                       j.get("descriptionPlain","") or j.get("description",""),
                       f"{loc} {j.get('workplaceType','')}")
        if sc >= MIN_SCORE:
            jobs.append({
                "id":       f"lv_{j['id']}",
                "title":    title, "company": name,
                "url":      j.get("hostedUrl",""),
                "location": loc or "Not specified",
                "score":    sc, "keywords": kw, "source": "Lever",
            })
    return jobs

# ── State ───────────────────────────────────────────────────────────────────────

def load_seen() -> set:
    return set(json.loads(SEEN_FILE.read_text())) if SEEN_FILE.exists() else set()

def save_seen(seen: set) -> None:
    SEEN_FILE.write_text(json.dumps(sorted(seen), indent=2))

# ── Email ───────────────────────────────────────────────────────────────────────

SOURCE_COLORS = {
    "Remotive":        "#1B4E6B", "Jobicy":          "#2D6A4F",
    "Arbeitnow":       "#4E2D6A", "RemoteOK":        "#6B4E1B",
    "Working Nomads":  "#1B6B4E", "We Work Remotely":"#6B1B4E",
    "Wellfound":       "#6B1B2D", "Built In":        "#2D4E1B",
    "Greenhouse":      "#1B2D4F", "Lever":           "#2D4F1B",
}

def build_email(matches: list[dict]) -> str:
    now, count = datetime.now(timezone.utc).strftime("%B %d, %Y"), len(matches)
    ACCENT, TAG_BG = "#1E6FA8", "#E8F0FE"

    def badge(s):
        c = "#2e7d32" if s >= 15 else "#1565c0" if s >= 8 else "#555"
        return (f'<span style="background:{c};color:white;padding:3px 10px;'
                f'border-radius:20px;font-size:12px;font-weight:700">{s}</span>')

    def src_tag(src):
        bg = SOURCE_COLORS.get(src, "#555")
        return (f'<span style="background:{bg};color:white;padding:1px 7px;'
                f'border-radius:8px;font-size:10px;margin-right:4px">{src}</span>')

    rows = ""
    for j in sorted(matches, key=lambda x: -x["score"]):
        kws = "".join(
            f'<span style="background:{TAG_BG};color:{ACCENT};padding:2px 7px;'
            f'border-radius:10px;font-size:11px;margin:2px 2px 0 0;'
            f'display:inline-block">{k}</span>'
            for k in j["keywords"]
        )
        rows += f"""
        <tr>
          <td style="padding:13px 16px;border-bottom:1px solid #eee;vertical-align:top">
            <div>{src_tag(j['source'])}
              <a href="{j['url']}" style="color:#1B2D4F;font-weight:700;
                 font-size:14px;text-decoration:none">{j['title']}</a></div>
            <div style="color:#666;font-size:12px;margin:3px 0 6px">
              <strong>{j['company']}</strong>&nbsp;·&nbsp;{j['location']}</div>
            <div>{kws}</div>
          </td>
          <td style="padding:13px 16px;border-bottom:1px solid #eee;
              text-align:center;vertical-align:middle;width:56px">{badge(j['score'])}</td>
        </tr>"""

    return f"""<html><body style="margin:0;padding:20px;background:#F2F4F6;
  font-family:Calibri,Helvetica,Arial,sans-serif">
<div style="max-width:680px;margin:0 auto;background:white;border-radius:8px;
  overflow:hidden;box-shadow:0 1px 6px rgba(0,0,0,.12)">
  <div style="background:#1B2D4F;padding:22px 24px">
    <div style="color:white;font-size:20px;font-weight:700">
      🎯 {count} new EM role{"s" if count!=1 else ""} found</div>
    <div style="color:#AAC4DD;font-size:13px;margin-top:4px">{now}</div>
  </div>
  <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse">
    <tr style="background:#F8F9FA">
      <th style="padding:8px 16px;text-align:left;font-size:11px;color:#888;
          text-transform:uppercase;letter-spacing:.5px">Role</th>
      <th style="padding:8px 16px;text-align:center;font-size:11px;color:#888;
          text-transform:uppercase;letter-spacing:.5px;width:56px">Score</th>
    </tr>{rows}
  </table>
  <div style="padding:12px 16px;font-size:11px;color:#AAA;text-align:center;
      border-top:1px solid #EEE">
    green ≥ 15 &nbsp;·&nbsp; blue ≥ 8 &nbsp;·&nbsp; grey ≥ 4
    &nbsp;&nbsp;|&nbsp;&nbsp; score = keyword match strength
  </div>
</div></body></html>"""

def send_email(html: str, count: int) -> None:
    sender, pw = os.environ["GMAIL_ADDRESS"], os.environ["GMAIL_APP_PASSWORD"]
    msg            = MIMEMultipart("alternative")
    msg["Subject"] = f"🎯 {count} new EM role{'s' if count!=1 else ''} match your profile"
    msg["From"]    = f"Job Scout <{sender}>"
    msg["To"]      = RECIPIENT
    msg.attach(MIMEText(html, "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as srv:
        srv.login(sender, pw)
        srv.sendmail(sender, RECIPIENT, msg.as_string())
    print(f"✉  Email sent — {count} matches")

# ── Main ────────────────────────────────────────────────────────────────────────

def run_source(label: str, fn) -> list[dict]:
    print(f"\n── {label} {'─'*(42-len(label))}")
    try:
        results = fn()
        if results:
            print(f"   {len(results)} match(es)")
        return results
    except Exception as e:
        print(f"   ⚠  Unhandled error: {e}", file=sys.stderr)
        return []

def main() -> None:
    print(f"Job Scout — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    seen, matches = load_seen(), []

    def process(jobs: list[dict]) -> None:
        for j in jobs:
            if j["id"] not in seen:
                matches.append(j)
                print(f"   ✓ [{j['score']:2d}] {j['company']} — {j['title']}")
            seen.add(j["id"])

    # ── Broad discovery (all companies) ─────────────────────────────
    process(run_source("Remotive",         fetch_remotive))
    process(run_source("Jobicy",           fetch_jobicy))
    process(run_source("Arbeitnow",        fetch_arbeitnow))
    process(run_source("RemoteOK",         fetch_remoteok))
    process(run_source("Working Nomads",   fetch_workingnomads))
    process(run_source("We Work Remotely", fetch_wwr))
    process(run_source("Wellfound",        fetch_wellfound))
    process(run_source("Built In",         fetch_builtin))

    # ── Priority company boards (active pipeline) ────────────────────
    if PRIORITY_GREENHOUSE or PRIORITY_LEVER:
        print(f"\n── Priority Boards {'─'*23}")
        for name, slug in PRIORITY_GREENHOUSE.items():
            print(f"   Greenhouse / {name}…")
            process(fetch_greenhouse_board(name, slug))
        for name, slug in PRIORITY_LEVER.items():
            print(f"   Lever / {name}…")
            process(fetch_lever_board(name, slug))

    save_seen(seen)
    print(f"\n{'─'*44}")
    print(f"Total new matches: {len(matches)}")

    if matches:
        send_email(build_email(matches), len(matches))
    else:
        print("Nothing new — no email sent.")

if __name__ == "__main__":
    main()
