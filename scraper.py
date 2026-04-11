"""
=============================================================================
 Job Scraper — RemoteOK Public API  (https://remoteok.com/api)
 Author  : Senior Python / Web-Scraping Engineer
 Target  : https://remoteok.com/api?tag=<query>
 Output  : RemoteOK_Jobs_<timestamp>.xlsx
=============================================================================
 No HTML scraping needed — RemoteOK provides a free, public JSON API.
 No API key, no login, no bot-blocking, no 403 errors.
=============================================================================
 Usage:
   python scraper.py                              # default: python
   python scraper.py --query "data engineer"
   python scraper.py --query "react" --max 50
=============================================================================
"""

import argparse
import logging
import time
import re
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

# ──────────────────────────────────────────────────────────────────────────────
# LOGGING
# ──────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  [%(levelname)s]  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ──────────────────────────────────────────────────────────────────────────────

API_BASE        = "https://remoteok.com/api"
COMPANY_NAME    = "RemoteOK"
REQUEST_TIMEOUT = 20

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

SKILL_KEYWORDS = [
    "Python", "JavaScript", "TypeScript", "React", "Node.js",
    "Django", "FastAPI", "Flask", "SQL", "PostgreSQL", "MySQL",
    "MongoDB", "Redis", "Docker", "Kubernetes", "AWS", "GCP",
    "Azure", "REST", "GraphQL", "CI/CD", "Git", "Linux",
    "Machine Learning", "TensorFlow", "PyTorch", "Pandas", "NumPy",
    "Spark", "Kafka", "Airflow", "Go", "Java", "Scala", "Rust",
    "Ruby", "PHP", "Swift", "Kotlin", "Vue", "Angular", "Next.js",
]

EXCEL_COLUMNS = [
    "JobTitle", "Location", "ExperienceRequired",
    "SkillsRequired", "Salary", "JobURL", "JobDescriptionSummary",
]

# ──────────────────────────────────────────────────────────────────────────────
# API FETCH
# ──────────────────────────────────────────────────────────────────────────────

def fetch_jobs_from_api(tag: str) -> list:
    """
    Call the RemoteOK public JSON API and return the raw list of job dicts.

    Endpoint: https://remoteok.com/api?tag=<tag>
    The API returns a JSON array where the first element is a legal notice
    (not a job), so we filter by items that have 'id' and 'position'.
    """
    url = f"{API_BASE}?tag={tag}"
    logger.info("Calling RemoteOK API: %s", url)

    try:
        response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()

        jobs = [item for item in data if item.get("id") and item.get("position")]
        logger.info("API returned %d job listings for tag: '%s'", len(jobs), tag)
        return jobs

    except requests.exceptions.HTTPError as e:
        logger.error("HTTP error from API: %s", e)
        return []
    except requests.exceptions.RequestException as e:
        logger.error("Network error: %s", e)
        return []
    except ValueError:
        logger.error("Invalid JSON response from API.")
        return []


# ──────────────────────────────────────────────────────────────────────────────
# HTML CLEANING
# ──────────────────────────────────────────────────────────────────────────────

def strip_html(raw_html: str) -> str:
    """Remove HTML tags and return clean plain text."""
    if not raw_html:
        return ""
    soup = BeautifulSoup(raw_html, "html.parser")
    return soup.get_text(separator=" ", strip=True)


# ──────────────────────────────────────────────────────────────────────────────
# JOB PARSING
# ──────────────────────────────────────────────────────────────────────────────

def extract_job_details(raw: dict) -> dict:
    """
    Map a raw API job dict to our structured job dict.

    RemoteOK API fields used:
      position     -> JobTitle
      location     -> Location
      salary_min / salary_max -> Salary
      url          -> JobURL
      description  -> JobDescriptionSummary (HTML stripped)
      tags         -> SkillsRequired
    """
    # Title
    title = raw.get("position", "").strip()

    # Location
    location = raw.get("location", "").strip() or "Remote – Worldwide"

    # Salary
    salary_min = raw.get("salary_min")
    salary_max = raw.get("salary_max")
    if salary_min and salary_max:
        salary = f"${int(salary_min):,} – ${int(salary_max):,}"
    elif salary_min:
        salary = f"${int(salary_min):,}+"
    else:
        salary = ""

    # Job URL
    job_url = raw.get("url", "")
    if job_url and not job_url.startswith("http"):
        job_url = urljoin("https://remoteok.com", job_url)

    # Description — clean HTML to plain text
    description = strip_html(raw.get("description", ""))
    summary = description[:300].strip() if description else ""

    # Skills — from API tags + keyword scan of description
    api_tags = raw.get("tags", [])
    skills_from_tags = [t.strip().title() for t in (api_tags or []) if t.strip()]

    skills_from_desc = [
        kw for kw in SKILL_KEYWORDS
        if re.search(rf"\b{re.escape(kw)}\b", description, re.IGNORECASE)
    ]

    seen = set()
    all_skills = []
    for s in skills_from_tags + skills_from_desc:
        if s.lower() not in seen:
            seen.add(s.lower())
            all_skills.append(s)
    skills = ", ".join(all_skills[:12])

    # Experience — extract from description text
    exp_match = re.search(
        r"(\d+\+?\s*(?:to\s*\d+\s*)?years?\s*(?:of\s*)?(?:experience|exp)?)",
        description, re.IGNORECASE,
    )
    experience = exp_match.group(1).strip() if exp_match else ""

    return {
        "JobTitle"             : title,
        "Location"             : location,
        "ExperienceRequired"   : experience,
        "SkillsRequired"       : skills,
        "Salary"               : salary,
        "JobURL"               : job_url,
        "JobDescriptionSummary": summary,
    }


def parse_jobs(raw_jobs: list, max_results: int) -> list:
    """Extract structured data, de-duplicate by URL, cap at max_results."""
    jobs      = []
    seen_urls = set()

    for raw in raw_jobs:
        if len(jobs) >= max_results:
            break

        job = extract_job_details(raw)

        if not job["JobTitle"]:
            continue
        if job["JobURL"] in seen_urls:
            continue

        seen_urls.add(job["JobURL"])
        jobs.append(job)
        logger.info(
            "  ✓  %-45s | %-25s | %s",
            job["JobTitle"][:45],
            job["Location"][:25],
            job["Salary"] or "Salary N/A",
        )
        time.sleep(0.05)

    return jobs


# ──────────────────────────────────────────────────────────────────────────────
# EXCEL EXPORT
# ──────────────────────────────────────────────────────────────────────────────

def style_excel(filepath: str) -> None:
    """Apply professional formatting: header colours, alternating rows, freeze, filter."""
    wb = load_workbook(filepath)
    ws = wb.active
    ws.title = "Jobs"

    HEADER_FILL = PatternFill("solid", start_color="1F3864", end_color="1F3864")
    ROW_FILL_A  = PatternFill("solid", start_color="FFFFFF", end_color="FFFFFF")
    ROW_FILL_B  = PatternFill("solid", start_color="EEF2FF", end_color="EEF2FF")
    HEADER_FONT = Font(name="Arial", bold=True, color="FFFFFF", size=11)
    CELL_FONT   = Font(name="Arial", size=10)
    BORDER = Border(
        bottom=Side(style="thin", color="CCCCCC"),
        right =Side(style="thin", color="CCCCCC"),
    )

    for cell in ws[1]:
        cell.font      = HEADER_FONT
        cell.fill      = HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border    = BORDER
    ws.row_dimensions[1].height = 30

    for row_idx, row in enumerate(ws.iter_rows(min_row=2), start=2):
        fill = ROW_FILL_A if row_idx % 2 == 0 else ROW_FILL_B
        for cell in row:
            cell.font      = CELL_FONT
            cell.fill      = fill
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            cell.border    = BORDER

    widths = {
        "JobTitle": 35, "Location": 22, "ExperienceRequired": 20,
        "SkillsRequired": 45, "Salary": 22, "JobURL": 50,
        "JobDescriptionSummary": 60,
    }
    for col_idx, col_name in enumerate(EXCEL_COLUMNS, start=1):
        ws.column_dimensions[get_column_letter(col_idx)].width = widths.get(col_name, 20)

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    wb.save(filepath)
    logger.info("Excel formatting applied → %s", filepath)


def save_to_excel(jobs: list):
    """Convert job list to a styled .xlsx file with a timestamped name."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename  = f"{COMPANY_NAME}_Jobs_{timestamp}.xlsx"

    df = pd.DataFrame(jobs, columns=EXCEL_COLUMNS)
    for col in EXCEL_COLUMNS:
        if col not in df.columns:
            df[col] = ""

    df.to_excel(filename, index=False, engine="openpyxl")
    logger.info("Raw data written: %s (%d rows)", filename, len(df))
    style_excel(filename)
    return filename, df


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        description="Scrape remote job listings from RemoteOK API and export to Excel."
    )
    parser.add_argument("--query", "-q", default="python",
                        help="Job tag to search — e.g. python, react, devops (default: python)")
    parser.add_argument("--max",   "-m", type=int, default=50,
                        help="Max number of jobs to collect (default: 50)")
    return parser.parse_args()


# ──────────────────────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────────────────────

def main():
    args = parse_args()

    logger.info("=" * 60)
    logger.info(" Job Scraper — RemoteOK API")
    logger.info(" Query : %s", args.query)
    logger.info(" Max   : %d jobs", args.max)
    logger.info("=" * 60)

    raw_jobs = fetch_jobs_from_api(tag=args.query)

    if not raw_jobs:
        logger.warning("No jobs returned from API. Check your query or internet connection.")
        return

    logger.info("Parsing job details...")
    jobs = parse_jobs(raw_jobs, max_results=args.max)

    if not jobs:
        logger.warning("No valid jobs after parsing. Exiting.")
        return

    output_file, df = save_to_excel(jobs)

    print("\n" + "═" * 110)
    print("  SAMPLE OUTPUT — First 5 rows")
    print("═" * 110)
    pd.set_option("display.max_columns", None)
    pd.set_option("display.max_colwidth", 38)
    pd.set_option("display.width", 130)
    print(df[["JobTitle", "Location", "Salary", "SkillsRequired", "ExperienceRequired"]].head())
    print("═" * 110)
    print(f"\n✅  Saved {len(jobs)} jobs  →  {output_file}\n")


if __name__ == "__main__":
    main()