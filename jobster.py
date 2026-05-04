#!/usr/bin/env python3
"""
JOBSTER
==================
Crawls daily job listings from all major job portals.
Keywords can be defined in config.json.
Output: A Markdown file with clickable links and job titles.

Usage:
	1. Edit config.json (keywords, optional settings)
	2. python job_crawler.py
	3. Results in ./results/jobs_YYYY-MM-DD.md

For daily execution: Set up a cronjob
	crontab -e
	0 8 * * * cd /path/to/script && python3 job_crawler.py

Dependencies:
	pip install requests beautifulsoup4 lxml
"""

import json
import os
import re
import sys
import time
import hashlib
import logging
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus, urljoin, urlparse

try:
	import requests
	from bs4 import BeautifulSoup
except ImportError:
	print("Missing dependencies. Please install:")
	print("pip install requests beautifulsoup4 lxml")
	sys.exit(1)


# ─── Logging ────────────────────────────────────────────────────────────────

logging.basicConfig(
	level=logging.INFO,
	format="%(asctime)s [%(levelname)s] %(message)s",
	datefmt="%H:%M:%S",
)
log = logging.getLogger("JobCrawler")


# ─── Load Configuration ────────────────────────────────────────────────────

CONFIG_FILE = "config.json"
DEFAULT_CONFIG = {
	"keywords": ["Python Developer", "Data Engineer", "DevOps"],
	"location": {
		"country": "Deutschland",
		"city": "",
		"radius_km": 50,
	},
	"results_dir": "./results",
	"max_results_per_source": 50,
	"request_delay_seconds": 2,
	"user_agent": (
		"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
		"AppleWebKit/537.36 (KHTML, like Gecko) "
		"Chrome/120.0.0.0 Safari/537.36"
	),
}

# ─── Radius Mapping ─────────────────────────────────────────────────────────
# Portals use different radius formats.
# These helpers build the location string + radius parameter per portal.

def _loc_string(cfg: dict) -> str:
	"""Builds the combined location string: 'City' or 'Country'."""
	loc = cfg.get("location", {})
	if isinstance(loc, str):  # Backwards compatibility
		return loc
	city = loc.get("city", "").strip()
	country = loc.get("country", "Deutschland").strip()
	return city if city else country

def _radius_km(cfg: dict) -> int:
	"""Returns the configured radius in km."""
	loc = cfg.get("location", {})
	if isinstance(loc, int):
		return loc
	if isinstance(loc, dict):
		return loc.get("radius_km", 50)
	return 50

def _radius_miles(cfg: dict) -> int:
	"""Radius in miles (for Indeed)."""
	return max(1, int(_radius_km(cfg) * 0.621371))

def _loc_display(cfg: dict) -> str:
	"""Display string for logging and Markdown output."""
	loc = cfg.get("location", {})
	if isinstance(loc, str):
		return loc
	parts = []
	city = loc.get("city", "").strip()
	country = loc.get("country", "").strip()
	radius = loc.get("radius_km", 50)
	if city:
		parts.append(city)
	if country:
		parts.append(country)
	label = ", ".join(parts) if parts else "Deutschland"
	if city and radius:
		label += f" (+{radius} km)"
	return label


def load_config() -> dict:
	"""Loads config.json or creates a default configuration."""
	if not Path(CONFIG_FILE).exists():
		with open(CONFIG_FILE, "w", encoding="utf-8") as f:
			json.dump(DEFAULT_CONFIG, f, indent=2, ensure_ascii=False)
		log.info(f"New {CONFIG_FILE} created – please adjust keywords!")
	with open(CONFIG_FILE, "r", encoding="utf-8") as f:
		cfg = json.load(f)
	# Fill in defaults
	for k, v in DEFAULT_CONFIG.items():
		cfg.setdefault(k, v)
	return cfg


# ─── HTTP Session ────────────────────────────────────────────────────────────

def make_session(user_agent: str) -> requests.Session:
	s = requests.Session()
	s.headers.update({
		"User-Agent": user_agent,
		"Accept-Language": "de-DE,de;q=0.9,en;q=0.5",
		"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
	})
	return s


def safe_get(session: requests.Session, url: str, delay: float = 2.0, retries: int = 3, **kwargs):
	"""GET with retry and rate limiting. Accepts extra kwargs for headers, params, etc."""
	timeout = kwargs.pop("timeout", 15)
	time.sleep(delay)
	for attempt in range(retries):
		try:
			r = session.get(url, timeout=timeout, **kwargs)
			r.raise_for_status()
			return r
		except requests.RequestException as e:
			log.warning(f"  Attempt {attempt+1}/{retries} failed: {e}")
			if attempt < retries - 1:
				time.sleep(3 * (attempt + 1))
	return None


# ─── Job Data Structure ─────────────────────────────────────────────────────

class Job:
	def __init__(self, title: str, url: str, source: str, company: str = ""):
		self.title = title.strip()
		self.url = url.strip()
		self.source = source
		self.company = company.strip()

	@property
	def uid(self) -> str:
		return hashlib.md5(f"{self.title}|{self.url}".encode()).hexdigest()[:12]

	def __repr__(self):
		return f"Job({self.title!r}, {self.source})"


# ─── Crawlers per Job Portal ────────────────────────────────────────────────
# Each crawler returns a list of Job objects.
# Portals are queried via their public search pages.
# ────────────────────────────────────────────────────────────────────────────


def crawl_indeed(session, keyword, location, cfg) -> list[Job]:
	"""Indeed.de – DISABLED: Indeed blocks all non-browser requests with 403.
	
	Replaced by Jobbörse.de (Google-powered aggregator) as a drop-in substitute.
	Indeed requires JavaScript execution and advanced fingerprinting that cannot
	be bypassed with plain HTTP requests. Use Selenium/Playwright if needed.
	"""
	jobs = []
	q = quote_plus(keyword)
	radius = _radius_km(cfg)
	url = f"https://www.jobborse.de/stellenangebote/?what={q}&where={quote_plus(location)}&radius={radius}"
	log.info(f"  Jobbörse.de (replaces Indeed): '{keyword}' (radius={radius}km)")

	r = safe_get(session, url, delay=cfg["request_delay_seconds"])
	if not r:
		return jobs

	soup = BeautifulSoup(r.text, "lxml")

	for card in soup.select("div.job-item, article, li.result, div[class*='job'], div[class*='result']"):
		link_el = card.select_one("a[href*='/stellenangebot'], a[href*='/job'], a[href]")
		title_el = card.select_one("h2, h3, .job-title, [class*='title']")
		company_el = card.select_one(".company, [class*='company'], [class*='employer']")

		title = title_el.get_text(strip=True) if title_el else ""
		href = link_el.get("href", "") if link_el else ""
		company = company_el.get_text(strip=True) if company_el else ""

		if not title and link_el:
			title = link_el.get_text(strip=True)

		if title and href and len(title) > 5:
			full_url = urljoin("https://www.jobborse.de", href)
			jobs.append(Job(title, full_url, "Jobbörse.de", company))

		if len(jobs) >= cfg["max_results_per_source"]:
			break

	log.info(f"    → {len(jobs)} results")
	return jobs


def crawl_glassdoor(session, keyword, location, cfg) -> list[Job]:
	"""Glassdoor.de – job portal with company reviews."""
	jobs = []
	q = quote_plus(keyword)
	radius = _radius_km(cfg)
	url = f"https://www.glassdoor.de/Job/jobs.htm?sc.keyword={q}&locT=C&locKeyword={quote_plus(location)}&radius={radius}"
	log.info(f"  Glassdoor: '{keyword}' (radius={radius}km)")

	r = safe_get(session, url, delay=cfg["request_delay_seconds"])
	if not r:
		return jobs

	soup = BeautifulSoup(r.text, "lxml")

	for card in soup.select("li.JobsList_jobListItem__JBBUV, li[data-test='jobListing'], div.jobCard, li.react-job-listing"):
		link_el = card.select_one("a[href*='/job-listing/'], a[href*='glassdoor.de/job'], a[href*='/partner/']")
		title_el = card.select_one("a[data-test='job-title'], h2, .job-title, [class*='jobTitle']")
		company_el = card.select_one("[data-test='emp-name'], .employer-name, [class*='companyName']")

		title = title_el.get_text(strip=True) if title_el else ""
		href = link_el.get("href", "") if link_el else ""
		company = company_el.get_text(strip=True) if company_el else ""

		if not title and link_el:
			title = link_el.get_text(strip=True)

		if title and href:
			full_url = urljoin("https://www.glassdoor.de", href)
			jobs.append(Job(title, full_url, "Glassdoor", company))

		if len(jobs) >= cfg["max_results_per_source"]:
			break

	log.info(f"    → {len(jobs)} results")
	return jobs


def crawl_stepstone(session, keyword, location, cfg) -> list[Job]:
	"""StepStone.de – one of the largest German job portals."""
	jobs = []
	q = quote_plus(keyword)
	loc = quote_plus(location)
	radius = _radius_km(cfg)
	url = f"https://www.stepstone.de/jobs/{q}/in-{loc}?radius={radius}"
	log.info(f"  StepStone: '{keyword}' (radius={radius}km)")

	r = safe_get(session, url, delay=cfg["request_delay_seconds"])
	if not r:
		return jobs

	soup = BeautifulSoup(r.text, "lxml")

	for card in soup.select("article[data-at='job-item'], div[data-genesis-element='CARD'], li[data-at='job-item']"):
		link_el = card.select_one("a[href*='/stellenangebote'], a[href*='stepstone.de']")
		title_el = card.select_one("h2, h3, [data-at='job-item-title'], a[data-at='job-item-title']")
		company_el = card.select_one("[data-at='job-item-company-name'], span.at-listing-nav-company-name-link")

		title = title_el.get_text(strip=True) if title_el else ""
		href = link_el.get("href", "") if link_el else ""
		company = company_el.get_text(strip=True) if company_el else ""

		if not title and link_el:
			title = link_el.get_text(strip=True)

		if title and href:
			full_url = urljoin("https://www.stepstone.de", href)
			jobs.append(Job(title, full_url, "StepStone", company))

		if len(jobs) >= cfg["max_results_per_source"]:
			break

	log.info(f"    → {len(jobs)} results")
	return jobs


def crawl_linkedin(session, keyword, location, cfg) -> list[Job]:
	"""LinkedIn Jobs – public search (no login required)."""
	jobs = []
	q = quote_plus(keyword)
	radius = _radius_km(cfg)
	# LinkedIn uses distance in miles
	dist_mi = _radius_miles(cfg)
	url = f"https://www.linkedin.com/jobs/search/?keywords={q}&location={quote_plus(location)}&distance={dist_mi}&sortBy=DD"
	log.info(f"  LinkedIn: '{keyword}' (radius={dist_mi}mi)")

	r = safe_get(session, url, delay=cfg["request_delay_seconds"])
	if not r:
		return jobs

	soup = BeautifulSoup(r.text, "lxml")

	for card in soup.select("div.base-card, li.result-card, div.job-search-card"):
		link_el = card.select_one("a.base-card__full-link, a[href*='linkedin.com/jobs/view']")
		title_el = card.select_one("h3, h4, span.sr-only, .base-search-card__title")
		company_el = card.select_one("h4.base-search-card__subtitle, a.hidden-nested-link")

		title = title_el.get_text(strip=True) if title_el else ""
		href = link_el.get("href", "") if link_el else ""
		company = company_el.get_text(strip=True) if company_el else ""

		if title and href:
			jobs.append(Job(title, href.split("?")[0], "LinkedIn", company))

		if len(jobs) >= cfg["max_results_per_source"]:
			break

	log.info(f"    → {len(jobs)} results")
	return jobs


def crawl_xing(session, keyword, location, cfg) -> list[Job]:
	"""XING Jobs (now 'New Work SE') – German career network."""
	jobs = []
	q = quote_plus(keyword)
	radius = _radius_km(cfg)
	url = f"https://www.xing.com/jobs/search?keywords={q}&location={quote_plus(location)}&radius={radius}&sort=date"
	log.info(f"  XING: '{keyword}' (radius={radius}km)")

	r = safe_get(session, url, delay=cfg["request_delay_seconds"])
	if not r:
		return jobs

	soup = BeautifulSoup(r.text, "lxml")

	for card in soup.select("li[data-testid='search-result'], div[class*='JobSearchResult'], article"):
		link_el = card.select_one("a[href*='/jobs/'], a[href*='xing.com']")
		title_el = card.select_one("h3, h2, [data-testid='job-title'], div[class*='title']")
		company_el = card.select_one("[data-testid='company-name'], span[class*='company']")

		title = title_el.get_text(strip=True) if title_el else ""
		href = link_el.get("href", "") if link_el else ""
		company = company_el.get_text(strip=True) if company_el else ""

		if not title and link_el:
			title = link_el.get_text(strip=True)

		if title and href:
			full_url = urljoin("https://www.xing.com", href)
			jobs.append(Job(title, full_url, "XING", company))

		if len(jobs) >= cfg["max_results_per_source"]:
			break

	log.info(f"    → {len(jobs)} results")
	return jobs


def crawl_monster(session, keyword, location, cfg) -> list[Job]:
	"""Monster.de"""
	jobs = []
	q = quote_plus(keyword)
	radius = _radius_km(cfg)
	url = f"https://www.monster.de/jobs/suche/?q={q}&where={quote_plus(location)}&radius={radius}&sort=dt.rv.di"
	log.info(f"  Monster: '{keyword}' (radius={radius}km)")

	r = safe_get(session, url, delay=cfg["request_delay_seconds"])
	if not r:
		return jobs

	soup = BeautifulSoup(r.text, "lxml")

	for card in soup.select("div[data-testid='svx-job-card'], section.card-content, div.summary"):
		link_el = card.select_one("a[href*='monster.de/job'], a[data-testid='jobTitle']")
		title_el = card.select_one("h2, h3, [data-testid='jobTitle'], a[data-testid='jobTitle']")
		company_el = card.select_one("[data-testid='company'], span.company")

		title = title_el.get_text(strip=True) if title_el else ""
		href = link_el.get("href", "") if link_el else ""
		company = company_el.get_text(strip=True) if company_el else ""

		if title and href:
			full_url = urljoin("https://www.monster.de", href)
			jobs.append(Job(title, full_url, "Monster", company))

		if len(jobs) >= cfg["max_results_per_source"]:
			break

	log.info(f"    → {len(jobs)} results")
	return jobs


def crawl_stellenanzeigen(session, keyword, location, cfg) -> list[Job]:
	"""Stellenanzeigen.de"""
	jobs = []
	q = quote_plus(keyword)
	radius = _radius_km(cfg)
	url = f"https://www.stellenanzeigen.de/stellenangebote/?stichwort={q}&ort={quote_plus(location)}&umkreis={radius}"
	log.info(f"  Stellenanzeigen.de: '{keyword}' (radius={radius}km)")

	r = safe_get(session, url, delay=cfg["request_delay_seconds"])
	if not r:
		return jobs

	soup = BeautifulSoup(r.text, "lxml")

	for card in soup.select("div.job-item, article.offer, div[class*='ListItem'], li.offer"):
		link_el = card.select_one("a[href*='stellenanzeigen.de/job'], a[href*='/job/']")
		title_el = card.select_one("h2, h3, .jobTitle, .job-title")
		company_el = card.select_one(".company-name, .companyName, span[class*='company']")

		title = title_el.get_text(strip=True) if title_el else ""
		href = link_el.get("href", "") if link_el else ""
		company = company_el.get_text(strip=True) if company_el else ""

		if not title and link_el:
			title = link_el.get_text(strip=True)

		if title and href:
			full_url = urljoin("https://www.stellenanzeigen.de", href)
			jobs.append(Job(title, full_url, "Stellenanzeigen.de", company))

		if len(jobs) >= cfg["max_results_per_source"]:
			break

	log.info(f"    → {len(jobs)} results")
	return jobs


def crawl_karriere(session, keyword, location, cfg) -> list[Job]:
	"""Karriere.de (Handelsblatt) – longer timeout due to slow responses."""
	jobs = []
	q = quote_plus(keyword)
	radius = _radius_km(cfg)
	delay = cfg["request_delay_seconds"]
	log.info(f"  Karriere.de: '{keyword}' (radius={radius}km)")

	# Primary URL
	url = f"https://jobs.karriere.de/jobs/?q={q}&loc={quote_plus(location)}&radius={radius}"

	# Karriere.de is often slow → increase timeout to 30s, only 1 retry
	time.sleep(delay)
	r = None
	for attempt in range(2):
		try:
			r = session.get(url, timeout=30)
			r.raise_for_status()
			break
		except requests.RequestException as e:
			log.warning(f"    Attempt {attempt+1}/2 failed: {e}")
			if attempt == 0:
				time.sleep(5)

	if not r:
		log.warning("    Karriere.de: Timeout → skipping")
		return jobs

	soup = BeautifulSoup(r.text, "lxml")

	for card in soup.select("div.job-card, article.job, li.job-item, div[class*='JobCard']"):
		link_el = card.select_one("a[href*='karriere.de'], a[href*='/job/']")
		title_el = card.select_one("h2, h3, .job-title, [class*='title']")
		company_el = card.select_one(".company, [class*='company']")

		title = title_el.get_text(strip=True) if title_el else ""
		href = link_el.get("href", "") if link_el else ""
		company = company_el.get_text(strip=True) if company_el else ""

		if not title and link_el:
			title = link_el.get_text(strip=True)

		if title and href:
			full_url = urljoin("https://jobs.karriere.de", href)
			jobs.append(Job(title, full_url, "Karriere.de", company))

		if len(jobs) >= cfg["max_results_per_source"]:
			break

	log.info(f"    → {len(jobs)} results")
	return jobs


def crawl_kimeta(session, keyword, location, cfg) -> list[Job]:
	"""Kimeta.de – meta job search engine."""
	jobs = []
	q = quote_plus(keyword)
	radius = _radius_km(cfg)
	url = f"https://www.kimeta.de/stellenangebote?q={q}&where={quote_plus(location)}&radius={radius}"
	log.info(f"  Kimeta: '{keyword}' (radius={radius}km)")

	r = safe_get(session, url, delay=cfg["request_delay_seconds"])
	if not r:
		return jobs

	soup = BeautifulSoup(r.text, "lxml")

	for card in soup.select("div.job, article, li.job-item, div[class*='result']"):
		link_el = card.select_one("a[href*='kimeta.de'], a[href*='/job']")
		title_el = card.select_one("h2, h3, .job-title, [class*='title']")
		company_el = card.select_one(".company, [class*='company']")

		title = title_el.get_text(strip=True) if title_el else ""
		href = link_el.get("href", "") if link_el else ""
		company = company_el.get_text(strip=True) if company_el else ""

		if not title and link_el:
			title = link_el.get_text(strip=True)

		if title and href:
			full_url = urljoin("https://www.kimeta.de", href)
			jobs.append(Job(title, full_url, "Kimeta", company))

		if len(jobs) >= cfg["max_results_per_source"]:
			break

	log.info(f"    → {len(jobs)} results")
	return jobs


# ─── Crawler Registry ───────────────────────────────────────────────────────

CRAWLERS = [
	("Jobbörse.de",         crawl_indeed),
	("StepStone",           crawl_stepstone),
	("LinkedIn",            crawl_linkedin),
	("XING",                crawl_xing),
	("Monster",             crawl_monster),
	("Stellenanzeigen.de",  crawl_stellenanzeigen),
	("Karriere.de",         crawl_karriere),
	("Kimeta",              crawl_kimeta),
	("Glassdoor",           crawl_glassdoor),
]


# ─── Deduplication ───────────────────────────────────────────────────────────

def deduplicate(jobs: list[Job]) -> list[Job]:
	"""Removes duplicates based on normalized title + domain."""
	seen = set()
	unique = []
	for job in jobs:
		domain = urlparse(job.url).netloc
		key = re.sub(r"\s+", " ", job.title.lower()) + "|" + domain
		if key not in seen:
			seen.add(key)
			unique.append(job)
	return unique


# ─── Markdown Export ─────────────────────────────────────────────────────────

def export_markdown(jobs: list[Job], keywords: list[str], loc_display: str, output_path: Path):
	"""Writes all jobs to a Markdown file."""
	today = datetime.now().strftime("%Y-%m-%d %H:%M")

	# Group jobs by source
	by_source: dict[str, list[Job]] = {}
	for job in jobs:
		by_source.setdefault(job.source, []).append(job)

	lines = [
		f"# Job Results – {today}",
		"",
		f"**Keywords:** {', '.join(keywords)}",
		f"**Location:** {loc_display}",
		f"**Jobs found:** {len(jobs)} (after deduplication)",
		f"**Sources:** {', '.join(by_source.keys())}",
		"",
		"---",
		"",
	]

	for source, source_jobs in sorted(by_source.items()):
		lines.append(f"## {source} ({len(source_jobs)})")
		lines.append("")
		lines.append("| # | Job Title | Company |")
		lines.append("|---|-----------|---------|")
		for i, job in enumerate(source_jobs, 1):
			title_link = f"[{job.title}]({job.url})"
			company = job.company if job.company else "–"
			lines.append(f"| {i} | {title_link} | {company} |")
		lines.append("")

	# Flat list as quick overview
	lines.append("---")
	lines.append("")
	lines.append("## All Results (Quick Overview)")
	lines.append("")
	for i, job in enumerate(jobs, 1):
		company_str = f" – *{job.company}*" if job.company else ""
		lines.append(f"{i}. [{job.title}]({job.url}){company_str} `{job.source}`")

	lines.append("")
	lines.append(f"---\n*Generated on {today} with Job Crawler*")

	output_path.parent.mkdir(parents=True, exist_ok=True)
	output_path.write_text("\n".join(lines), encoding="utf-8")
	log.info(f"Result saved: {output_path}")


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
	print()
	print("╔══════════════════════════════════════╗")
	print("║               JOBSTER               ║")
	print("╚══════════════════════════════════════╝")
	print()

	cfg = load_config()
	session = make_session(cfg["user_agent"])
	keywords = cfg["keywords"]
	location = _loc_string(cfg)
	loc_display = _loc_display(cfg)

	log.info(f"Keywords:  {keywords}")
	log.info(f"Location:  {loc_display}")
	log.info(f"Sources:   {len(CRAWLERS)} job portals")
	print()

	all_jobs: list[Job] = []

	for keyword in keywords:
		log.info(f"── Keyword: '{keyword}' ──")
		for name, crawler_fn in CRAWLERS:
			try:
				found = crawler_fn(session, keyword, location, cfg)
				all_jobs.extend(found)
			except Exception as e:
				log.error(f"  {name} failed: {e}")
		print()

	# Deduplicate
	unique_jobs = deduplicate(all_jobs)
	log.info(f"Total: {len(all_jobs)} → {len(unique_jobs)} after deduplication")

	# Export
	today_str = datetime.now().strftime("%Y-%m-%d")
	output_path = Path(cfg["results_dir"]) / f"jobs_{today_str}.md"
	export_markdown(unique_jobs, keywords, loc_display, output_path)

	print()
	print(f"  Output: {output_path}")
	print(f"  {len(unique_jobs)} jobs found")
	print()


if __name__ == "__main__":
	main()