"""
=============================================================================
 Job Scraper — We Work Remotely (weworkremotely.com)
 Author  : Senior Python / Web-Scraping Engineer
 Target  : https://weworkremotely.com/remote-jobs/search?term=<query>
 Output  : WeWorkRemotely_Jobs_<timestamp>.xlsx
=============================================================================
 Tech Stack : requests · BeautifulSoup4 · pandas · openpyxl
 Usage      : python scraper.py [--url URL] [--query QUERY] [--pages N]
=============================================================================
"""

import argparse
import logging
import time
import re
from datetime import datetime
from urllib.parse import urljoin, urlencode

import requests
from bs4 import BeautifulSoup
import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

# ──────────────────────────────────────────────────────────────────────────────
# LOGGING SETUP
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

BASE_URL       = "https://weworkremotely.com"
SEARCH_PATH    = "/remote-jobs/search"
COMPANY_NAME   = "WeWorkRemotely"
REQUEST_DELAY  = 1.5          # seconds between requests (be polite)
REQUEST_TIMEOUT = 15          # seconds before giving up on a request
MAX_RETRIES    = 3            # retries on transient failures

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

EXCEL_COLUMNS = [
    "JobTitle",
    "Location",
    "ExperienceRequired",
    "SkillsRequired",
    "Salary",
    "JobURL",
    "JobDescriptionSummary",
]

# ──────────────────────────────────────────────────────────────────────────────
# HTTP HELPERS
# ──────────────────────────────────────────────────────────────────────────────

def create_session() -> requests.Session:
    """Build a persistent requests.Session with default headers."""
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


def fetch_page(url: str, session: requests.Session, retries: int = MAX_RETRIES) -> BeautifulSoup | None:
    """
    Fetch a URL and return a BeautifulSoup object.

    Retries up to `retries` times on connection/timeout errors.
    Returns None if all attempts fail.
    """
    for attempt in range(1, retries + 1):
        try:
            logger.info("Fetching (attempt %d): %s", attempt, url)
            response = session.get(url, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            return BeautifulSoup(response.text, "html.parser")

        except requests.exceptions.HTTPError as e:
            logger.warning("HTTP error %s for %s", e.response.status_code, url)
            if e.response.status_code in {403, 404, 410}:
                return None          # permanent failure — don't retry
            time.sleep(REQUEST_DELAY * attempt)

        except requests.exceptions.RequestException as e:
            logger.warning("Request error (attempt %d): %s", attempt, e)
            time.sleep(REQUEST_DELAY * attempt)

    logger.error("All %d attempts failed for: %s", retries, url)
    return None


# ──────────────────────────────────────────────────────────────────────────────
# PAGINATION
# ──────────────────────────────────────────────────────────────────────────────

def handle_pagination(soup: BeautifulSoup, current_url: str) -> str | None:
    """
    Detect a 'Next' page link and return its absolute URL.

    We Work Remotely uses a <a rel="next"> or a pagination element
    labelled "Next ›". Returns None if no next page exists.
    """
    # Strategy 1: <a rel="next"> (semantic HTML)
    next_tag = soup.find("a", rel="next")
    if next_tag and next_tag.get("href"):
        return urljoin(BASE_URL, next_tag["href"])

    # Strategy 2: any link whose text is "Next" / "Next »" / "›"
    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True)
        if re.search(r"next|›|»", text, re.IGNORECASE):
            return urljoin(BASE_URL, a["href"])

    # Strategy 3: pagination container
    pagination = soup.select_one(".pagination, nav[aria-label='pagination']")
    if pagination:
        active = pagination.find("li", class_=re.compile(r"active|current", re.I))
        if active:
            sibling = active.find_next_sibling("li")
            if sibling:
                link = sibling.find("a", href=True)
                if link:
                    return urljoin(BASE_URL, link["href"])

    return None   # no more pages


# ──────────────────────────────────────────────────────────────────────────────
# JOB LIST PARSING
# ──────────────────────────────────────────────────────────────────────────────

def parse_jobs(soup: BeautifulSoup) -> list[dict]:
    """
    Parse all job cards on a single search-results page.

    We Work Remotely renders job cards as <li> elements inside
    <section class="jobs"> → <ul class="jobs">.
    Each <li> has:
      • <span class="title">  — job title
      • <span class="company"> — company name (used to enrich location)
      • <a href>              — job detail link
    """
    jobs = []
    # Primary selector: ul.jobs li with a link
    job_items = soup.select("section.jobs ul.jobs li:not(.view-all)")

    if not job_items:
        # Fallback: broader selector
        job_items = soup.select("ul.jobs li")

    logger.info("  Found %d job cards on this page", len(job_items))

    for item in job_items:
        # Skip divider/ad rows (they have no link)
        link_tag = item.find("a", href=True)
        if not link_tag:
            continue

        job_url = urljoin(BASE_URL, link_tag["href"])

        title_tag   = item.select_one("span.title")
        company_tag = item.select_one("span.company")
        region_tag  = item.select_one("span.region, .region, .location")

        job = {
            "JobTitle"            : title_tag.get_text(strip=True)   if title_tag   else "",
            "Location"            : region_tag.get_text(strip=True)  if region_tag  else "Remote",
            "ExperienceRequired"  : "",   # populated via detail page
            "SkillsRequired"      : "",   # populated via detail page
            "Salary"              : "",   # populated via detail page
            "JobURL"              : job_url,
            "JobDescriptionSummary": "",  # populated via detail page
        }

        # If company tag is present, it often doubles as region indicator
        if not job["Location"] and company_tag:
            company_text = company_tag.get_text(separator=" | ", strip=True)
            if "|" in company_text:
                job["Location"] = company_text.split("|")[-1].strip()

        jobs.append(job)

    return jobs


# ──────────────────────────────────────────────────────────────────────────────
# JOB DETAIL EXTRACTION
# ──────────────────────────────────────────────────────────────────────────────

def extract_job_details(job: dict, session: requests.Session) -> dict:
    """
    Visit the individual job page and enrich the job dict with:
      • ExperienceRequired
      • SkillsRequired
      • Salary
      • JobDescriptionSummary (first 300 chars of description)

    All fields default to "" if not found — never raises an exception.
    """
    url = job.get("JobURL", "")
    if not url:
        return job

    soup = fetch_page(url, session)
    if soup is None:
        return job

    try:
        # ── Description body ────────────────────────────────────────────────
        desc_tag = (
            soup.select_one(".listing-container")
            or soup.select_one("div.job-description")
            or soup.select_one("article")
            or soup.select_one("main")
        )
        description = desc_tag.get_text(separator=" ", strip=True) if desc_tag else ""

        # Summary = first 300 characters of clean text
        job["JobDescriptionSummary"] = description[:300].strip() if description else ""

        # ── Salary ───────────────────────────────────────────────────────────
        salary_match = re.search(
            r"(\$[\d,]+(?:\s*[-–]\s*\$[\d,]+)?(?:\s*(?:k|K|USD|per\s+year|/yr|annually))?)",
            description,
        )
        if salary_match:
            job["Salary"] = salary_match.group(1).strip()

        # ── Experience ───────────────────────────────────────────────────────
        experience_match = re.search(
            r"(\d+\+?\s*(?:to\s*\d+\s*)?years?\s*(?:of\s*)?(?:experience|exp)?)",
            description,
            re.IGNORECASE,
        )
        if experience_match:
            job["ExperienceRequired"] = experience_match.group(1).strip()

        # ── Skills ───────────────────────────────────────────────────────────
        # Common technical keywords found in job descriptions
        SKILL_KEYWORDS = [
            "Python", "JavaScript", "TypeScript", "React", "Node.js",
            "Django", "FastAPI", "Flask", "SQL", "PostgreSQL", "MySQL",
            "MongoDB", "Redis", "Docker", "Kubernetes", "AWS", "GCP",
            "Azure", "REST", "GraphQL", "CI/CD", "Git", "Linux",
            "Machine Learning", "Data Science", "TensorFlow", "PyTorch",
            "Pandas", "NumPy", "Spark", "Kafka", "Airflow", "Go",
            "Java", "Scala", "Rust", "Ruby", "PHP", "Swift", "Kotlin",
        ]
        found_skills = [kw for kw in SKILL_KEYWORDS if re.search(rf"\b{re.escape(kw)}\b", description, re.IGNORECASE)]
        job["SkillsRequired"] = ", ".join(found_skills) if found_skills else ""

    except Exception as exc:
        logger.warning("Error extracting details from %s: %s", url, exc)

    return job


# ──────────────────────────────────────────────────────────────────────────────
# EXCEL EXPORT
# ──────────────────────────────────────────────────────────────────────────────

def style_excel(filepath: str) -> None:
    """
    Apply professional formatting to the saved Excel file:
      • Bold, coloured header row
      • Auto-fitted column widths
      • Alternating row shading
      • All cells wrapped and vertically centred
      • Frozen top row
    """
    wb = load_workbook(filepath)
    ws = wb.active
    ws.title = "Jobs"

    # ── Colour palette ───────────────────────────────────────────────────────
    HEADER_FILL = PatternFill("solid", start_color="1F3864", end_color="1F3864")  # dark navy
    ROW_FILL_A  = PatternFill("solid", start_color="FFFFFF", end_color="FFFFFF")  # white
    ROW_FILL_B  = PatternFill("solid", start_color="EEF2FF", end_color="EEF2FF")  # light blue
    HEADER_FONT = Font(name="Arial", bold=True, color="FFFFFF", size=11)
    CELL_FONT   = Font(name="Arial", size=10)
    BORDER      = Border(
        bottom=Side(style="thin", color="CCCCCC"),
        right =Side(style="thin", color="CCCCCC"),
    )

    # ── Header row ───────────────────────────────────────────────────────────
    for cell in ws[1]:
        cell.font      = HEADER_FONT
        cell.fill      = HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border    = BORDER

    ws.row_dimensions[1].height = 30

    # ── Data rows ────────────────────────────────────────────────────────────
    for row_idx, row in enumerate(ws.iter_rows(min_row=2), start=2):
        fill = ROW_FILL_A if row_idx % 2 == 0 else ROW_FILL_B
        for cell in row:
            cell.font      = CELL_FONT
            cell.fill      = fill
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            cell.border    = BORDER

    # ── Column widths (auto-fit with caps) ───────────────────────────────────
    col_widths = {
        "JobTitle"            : 35,
        "Location"            : 22,
        "ExperienceRequired"  : 20,
        "SkillsRequired"      : 40,
        "Salary"              : 18,
        "JobURL"              : 45,
        "JobDescriptionSummary": 60,
    }
    for col_idx, col_name in enumerate(EXCEL_COLUMNS, start=1):
        ws.column_dimensions[get_column_letter(col_idx)].width = col_widths.get(col_name, 20)

    # ── Freeze top row ───────────────────────────────────────────────────────
    ws.freeze_panes = "A2"

    # ── Auto-filter on header ─────────────────────────────────────────────────
    ws.auto_filter.ref = ws.dimensions

    wb.save(filepath)
    logger.info("Excel formatting applied → %s", filepath)


def save_to_excel(jobs: list[dict], company_name: str) -> str:
    """Convert job list → pandas DataFrame → styled .xlsx file."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename  = f"{company_name}_Jobs_{timestamp}.xlsx"

    df = pd.DataFrame(jobs, columns=EXCEL_COLUMNS)

    # Fill any missing columns with empty string
    for col in EXCEL_COLUMNS:
        if col not in df.columns:
            df[col] = ""

    df.to_excel(filename, index=False, engine="openpyxl")
    logger.info("Raw data written to %s (%d rows)", filename, len(df))

    style_excel(filename)
    return filename


# ──────────────────────────────────────────────────────────────────────────────
# MAIN ORCHESTRATION
# ──────────────────────────────────────────────────────────────────────────────

def scrape(search_query: str, max_pages: int = 5) -> list[dict]:
    """
    Orchestrate the full scrape:
      1. Build search URL
      2. Loop pages (handle_pagination)
      3. For each page → parse_jobs (list cards)
      4. For each job card → extract_job_details (visit detail page)
      5. De-duplicate by JobURL
    """
    session = create_session()
    all_jobs: list[dict] = []
    seen_urls: set[str] = set()

    params      = {"term": search_query}
    current_url = f"{BASE_URL}{SEARCH_PATH}?{urlencode(params)}"
    page_num    = 1

    while current_url and page_num <= max_pages:
        logger.info("━━ Scraping page %d: %s", page_num, current_url)
        soup = fetch_page(current_url, session)

        if soup is None:
            logger.warning("Could not fetch page %d. Stopping.", page_num)
            break

        page_jobs = parse_jobs(soup)

        if not page_jobs:
            logger.info("No jobs found on page %d. Likely end of results.", page_num)
            break

        for job in page_jobs:
            url = job["JobURL"]
            if url in seen_urls:
                logger.debug("Skipping duplicate: %s", url)
                continue

            seen_urls.add(url)

            logger.info("  → Fetching details: %s", job["JobTitle"])
            job = extract_job_details(job, session)
            all_jobs.append(job)

            time.sleep(REQUEST_DELAY)   # polite delay between detail requests

        # Move to next page
        next_url = handle_pagination(soup, current_url)
        current_url = next_url
        page_num   += 1

        if current_url:
            time.sleep(REQUEST_DELAY)   # polite delay between list pages

    logger.info("Scraping complete. Total unique jobs collected: %d", len(all_jobs))
    return all_jobs


# ──────────────────────────────────────────────────────────────────────────────
# CLI ENTRY POINT
# ──────────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scrape job listings from We Work Remotely and export to Excel."
    )
    parser.add_argument(
        "--query", "-q",
        default="python developer",
        help="Job search query (default: 'python developer')",
    )
    parser.add_argument(
        "--pages", "-p",
        type=int,
        default=5,
        help="Maximum pages to scrape (default: 5)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    logger.info("============================================================")
    logger.info(" Job Scraper — We Work Remotely")
    logger.info(" Query   : %s", args.query)
    logger.info(" Max pages: %d", args.pages)
    logger.info("============================================================")

    jobs = scrape(search_query=args.query, max_pages=args.pages)

    if not jobs:
        logger.warning("No jobs were collected. Exiting.")
        return

    output_file = save_to_excel(jobs, COMPANY_NAME)

    # ── DataFrame preview ───────────────────────────────────────────────────
    df = pd.DataFrame(jobs, columns=EXCEL_COLUMNS)
    print("\n" + "═" * 100)
    print("  SAMPLE OUTPUT — First 5 rows")
    print("═" * 100)
    pd.set_option("display.max_columns", None)
    pd.set_option("display.max_colwidth", 40)
    pd.set_option("display.width", 120)
    print(df.head())
    print("═" * 100)
    print(f"\n✅  Saved {len(jobs)} jobs → {output_file}")


if __name__ == "__main__":
    main()