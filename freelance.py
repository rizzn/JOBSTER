#!/usr/bin/env python3
"""
FREELANCSTER
==================
Crawls daily freelance project listings from all major freelancer portals.
Keywords can be defined in config.json.
Output: A Markdown file with clickable links and project titles.

Usage:
	1. Edit config.json (keywords, optional settings)
	2. python freelance.py
	3. Results in ./results/freelance_YYYY-MM-DD.md

For daily execution: Set up a cronjob
	crontab -e
	0 8 * * * cd /path/to/script && python3 freelance.py

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
log = logging.getLogger("FreelanceCrawler")


# ─── Load Configuration ────────────────────────────────────────────────────

CONFIG_FILE = "config.json"
DEFAULT_CONFIG = {
	"keywords": ["Python", "DevOps", "Data Engineering", "Cloud Architect"],
	"location": {
		"country": "Deutschland",
		"city": "",
		"radius_km": 50,
		"remote_only": False,
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

# ─── Location Helpers ───────────────────────────────────────────────────────

def _loc_string(cfg: dict) -> str:
	"""Builds the combined location string: 'City' or 'Country'."""
	loc = cfg.get("location", {})
	if isinstance(loc, str):
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

def _remote_only(cfg: dict) -> bool:
	"""Whether to filter for remote-only projects."""
	loc = cfg.get("location", {})
	if isinstance(loc, dict):
		return loc.get("remote_only", False)
	return False

def _loc_display(cfg: dict) -> str:
	"""Display string for logging and Markdown output."""
	loc = cfg.get("location", {})
	if isinstance(loc, str):
		return loc
	parts = []
	city = loc.get("city", "").strip()
	country = loc.get("country", "").strip()
	radius = loc.get("radius_km", 50)
	remote = loc.get("remote_only", False)
	if city:
		parts.append(city)
	if country:
		parts.append(country)
	label = ", ".join(parts) if parts else "Deutschland"
	if city and radius:
		label += f" (+{radius} km)"
	if remote:
		label += " [Remote only]"
	return label


def load_config() -> dict:
	"""Loads config.json or creates a default configuration."""
	if not Path(CONFIG_FILE).exists():
		with open(CONFIG_FILE, "w", encoding="utf-8") as f:
			json.dump(DEFAULT_CONFIG, f, indent=2, ensure_ascii=False)
		log.info(f"New {CONFIG_FILE} created – please adjust keywords!")
	with open(CONFIG_FILE, "r", encoding="utf-8") as f:
		cfg = json.load(f)
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
	"""GET with retry and rate limiting."""
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


# ─── Project Data Structure ─────────────────────────────────────────────────

class Project:
	def __init__(self, title: str, url: str, source: str, company: str = "", rate: str = "", remote: str = ""):
		self.title = title.strip()
		self.url = url.strip()
		self.source = source
		self.company = company.strip()
		self.rate = rate.strip()
		self.remote = remote.strip()

	@property
	def uid(self) -> str:
		return hashlib.md5(f"{self.title}|{self.url}".encode()).hexdigest()[:12]

	def __repr__(self):
		return f"Project({self.title!r}, {self.source})"


# ─── Crawlers per Freelancer Portal ─────────────────────────────────────────
# Each crawler returns a list of Project objects.
# ────────────────────────────────────────────────────────────────────────────


def crawl_freelance_de(session, keyword, location, cfg) -> list[Project]:
	"""freelance.de – largest German freelancer portal."""
	projects = []
	q = quote_plus(keyword)
	radius = _radius_km(cfg)
	remote = _remote_only(cfg)
	url = f"https://www.freelance.de/search/project.php?search_word={q}&city={quote_plus(location)}&distance={radius}&sort=1"
	if remote:
		url += "&remote=1"
	log.info(f"  freelance.de: '{keyword}' (radius={radius}km)")

	r = safe_get(session, url, delay=cfg["request_delay_seconds"])
	if not r:
		return projects

	soup = BeautifulSoup(r.text, "lxml")

	for card in soup.select("div.project-list-item, div[class*='project'], tr.project-row, div.search-result"):
		link_el = card.select_one("a[href*='/Projekte/'], a[href*='/project/'], a[href]")
		title_el = card.select_one("h2, h3, .project-title, [class*='title'], a[class*='title']")
		company_el = card.select_one(".company, [class*='company'], [class*='client']")
		rate_el = card.select_one("[class*='rate'], [class*='budget'], [class*='hour']")
		remote_el = card.select_one("[class*='remote'], [class*='location']")

		title = title_el.get_text(strip=True) if title_el else ""
		href = link_el.get("href", "") if link_el else ""
		company = company_el.get_text(strip=True) if company_el else ""
		rate = rate_el.get_text(strip=True) if rate_el else ""
		remote_txt = remote_el.get_text(strip=True) if remote_el else ""

		if not title and link_el:
			title = link_el.get_text(strip=True)

		if title and href and len(title) > 5:
			full_url = urljoin("https://www.freelance.de", href)
			projects.append(Project(title, full_url, "freelance.de", company, rate, remote_txt))

		if len(projects) >= cfg["max_results_per_source"]:
			break

	log.info(f"    → {len(projects)} results")
	return projects


def crawl_freelancermap(session, keyword, location, cfg) -> list[Project]:
	"""freelancermap.de – large German freelancer marketplace."""
	projects = []
	q = quote_plus(keyword)
	radius = _radius_km(cfg)
	remote = _remote_only(cfg)
	url = f"https://www.freelancermap.de/projektboerse.html?query={q}&city={quote_plus(location)}&radius={radius}&sort=1"
	if remote:
		url += "&remote=1"
	log.info(f"  freelancermap.de: '{keyword}' (radius={radius}km)")

	r = safe_get(session, url, delay=cfg["request_delay_seconds"])
	if not r:
		return projects

	soup = BeautifulSoup(r.text, "lxml")

	for card in soup.select("div.project-list-item, div[class*='project'], li.project, div.card"):
		link_el = card.select_one("a[href*='/projekt/'], a[href*='freelancermap.de'], a[href]")
		title_el = card.select_one("h2, h3, .project-title, [class*='title']")
		company_el = card.select_one(".company, [class*='company'], [class*='recruiter']")
		rate_el = card.select_one("[class*='rate'], [class*='budget'], [class*='hour']")
		remote_el = card.select_one("[class*='remote'], [class*='einsatzort']")

		title = title_el.get_text(strip=True) if title_el else ""
		href = link_el.get("href", "") if link_el else ""
		company = company_el.get_text(strip=True) if company_el else ""
		rate = rate_el.get_text(strip=True) if rate_el else ""
		remote_txt = remote_el.get_text(strip=True) if remote_el else ""

		if not title and link_el:
			title = link_el.get_text(strip=True)

		if title and href and len(title) > 5:
			full_url = urljoin("https://www.freelancermap.de", href)
			projects.append(Project(title, full_url, "freelancermap.de", company, rate, remote_txt))

		if len(projects) >= cfg["max_results_per_source"]:
			break

	log.info(f"    → {len(projects)} results")
	return projects


def crawl_gulp(session, keyword, location, cfg) -> list[Project]:
	"""GULP.de – IT freelancer projects (Randstad group)."""
	projects = []
	q = quote_plus(keyword)
	radius = _radius_km(cfg)
	url = f"https://www.gulp.de/gulp2/g/projekte?query={q}&location={quote_plus(location)}&radius={radius}&order=DATE"
	log.info(f"  GULP: '{keyword}' (radius={radius}km)")

	r = safe_get(session, url, delay=cfg["request_delay_seconds"])
	if not r:
		return projects

	soup = BeautifulSoup(r.text, "lxml")

	for card in soup.select("div.project-item, div[class*='project'], article, li.result, div.card"):
		link_el = card.select_one("a[href*='/projekte/'], a[href*='gulp.de'], a[href]")
		title_el = card.select_one("h2, h3, .project-title, [class*='title']")
		company_el = card.select_one(".company, [class*='company'], [class*='provider']")
		rate_el = card.select_one("[class*='rate'], [class*='hourly'], [class*='budget']")
		remote_el = card.select_one("[class*='remote'], [class*='location'], [class*='ort']")

		title = title_el.get_text(strip=True) if title_el else ""
		href = link_el.get("href", "") if link_el else ""
		company = company_el.get_text(strip=True) if company_el else ""
		rate = rate_el.get_text(strip=True) if rate_el else ""
		remote_txt = remote_el.get_text(strip=True) if remote_el else ""

		if not title and link_el:
			title = link_el.get_text(strip=True)

		if title and href and len(title) > 5:
			full_url = urljoin("https://www.gulp.de", href)
			projects.append(Project(title, full_url, "GULP", company, rate, remote_txt))

		if len(projects) >= cfg["max_results_per_source"]:
			break

	log.info(f"    → {len(projects)} results")
	return projects


def crawl_hays(session, keyword, location, cfg) -> list[Project]:
	"""Hays.de – large staffing firm with freelance/contracting projects."""
	projects = []
	q = quote_plus(keyword)
	radius = _radius_km(cfg)
	url = f"https://www.hays.de/jobsuche/stellenangebote-jobs?q={q}&location={quote_plus(location)}&d={radius}&e=contracting"
	log.info(f"  Hays (Contracting): '{keyword}' (radius={radius}km)")

	r = safe_get(session, url, delay=cfg["request_delay_seconds"])
	if not r:
		return projects

	soup = BeautifulSoup(r.text, "lxml")

	for card in soup.select("div.job-item, div[class*='JobCard'], article, li.search-result, div.card"):
		link_el = card.select_one("a[href*='hays.de'], a[href*='/job/'], a[href]")
		title_el = card.select_one("h2, h3, .job-title, [class*='title']")
		company_el = card.select_one(".company, [class*='company']")
		rate_el = card.select_one("[class*='salary'], [class*='rate']")
		remote_el = card.select_one("[class*='remote'], [class*='location']")

		title = title_el.get_text(strip=True) if title_el else ""
		href = link_el.get("href", "") if link_el else ""
		company = company_el.get_text(strip=True) if company_el else ""
		rate = rate_el.get_text(strip=True) if rate_el else ""
		remote_txt = remote_el.get_text(strip=True) if remote_el else ""

		if not title and link_el:
			title = link_el.get_text(strip=True)

		if title and href and len(title) > 5:
			full_url = urljoin("https://www.hays.de", href)
			projects.append(Project(title, full_url, "Hays", company, rate, remote_txt))

		if len(projects) >= cfg["max_results_per_source"]:
			break

	log.info(f"    → {len(projects)} results")
	return projects


def crawl_solcom(session, keyword, location, cfg) -> list[Project]:
	"""SOLCOM – IT freelancer projects and contracting."""
	projects = []
	q = quote_plus(keyword)
	radius = _radius_km(cfg)
	url = f"https://www.solcom.de/de/projektanfragen.aspx?search={q}&loc={quote_plus(location)}&radius={radius}"
	log.info(f"  SOLCOM: '{keyword}' (radius={radius}km)")

	r = safe_get(session, url, delay=cfg["request_delay_seconds"])
	if not r:
		return projects

	soup = BeautifulSoup(r.text, "lxml")

	for card in soup.select("div.project, div[class*='project'], article, li.result, div.card, tr[class*='project']"):
		link_el = card.select_one("a[href*='solcom.de'], a[href*='/projekt'], a[href]")
		title_el = card.select_one("h2, h3, .project-title, [class*='title'], td.title")
		company_el = card.select_one(".company, [class*='company']")
		rate_el = card.select_one("[class*='rate'], [class*='budget']")
		remote_el = card.select_one("[class*='remote'], [class*='ort'], [class*='location']")

		title = title_el.get_text(strip=True) if title_el else ""
		href = link_el.get("href", "") if link_el else ""
		company = company_el.get_text(strip=True) if company_el else ""
		rate = rate_el.get_text(strip=True) if rate_el else ""
		remote_txt = remote_el.get_text(strip=True) if remote_el else ""

		if not title and link_el:
			title = link_el.get_text(strip=True)

		if title and href and len(title) > 5:
			full_url = urljoin("https://www.solcom.de", href)
			projects.append(Project(title, full_url, "SOLCOM", company, rate, remote_txt))

		if len(projects) >= cfg["max_results_per_source"]:
			break

	log.info(f"    → {len(projects)} results")
	return projects


def crawl_etengo(session, keyword, location, cfg) -> list[Project]:
	"""Etengo.de – IT freelancer staffing."""
	projects = []
	q = quote_plus(keyword)
	radius = _radius_km(cfg)
	url = f"https://www.etengo.de/projektboerse/?search={q}&location={quote_plus(location)}&radius={radius}"
	log.info(f"  Etengo: '{keyword}' (radius={radius}km)")

	r = safe_get(session, url, delay=cfg["request_delay_seconds"])
	if not r:
		return projects

	soup = BeautifulSoup(r.text, "lxml")

	for card in soup.select("div.project, div[class*='project'], article, li.result, div.card"):
		link_el = card.select_one("a[href*='etengo.de'], a[href*='/projekt'], a[href]")
		title_el = card.select_one("h2, h3, .project-title, [class*='title']")
		company_el = card.select_one(".company, [class*='company']")
		rate_el = card.select_one("[class*='rate'], [class*='budget']")
		remote_el = card.select_one("[class*='remote'], [class*='location']")

		title = title_el.get_text(strip=True) if title_el else ""
		href = link_el.get("href", "") if link_el else ""
		company = company_el.get_text(strip=True) if company_el else ""
		rate = rate_el.get_text(strip=True) if rate_el else ""
		remote_txt = remote_el.get_text(strip=True) if remote_el else ""

		if not title and link_el:
			title = link_el.get_text(strip=True)

		if title and href and len(title) > 5:
			full_url = urljoin("https://www.etengo.de", href)
			projects.append(Project(title, full_url, "Etengo", company, rate, remote_txt))

		if len(projects) >= cfg["max_results_per_source"]:
			break

	log.info(f"    → {len(projects)} results")
	return projects


def crawl_projektwerk(session, keyword, location, cfg) -> list[Project]:
	"""Projektwerk.com – freelancer project marketplace."""
	projects = []
	q = quote_plus(keyword)
	radius = _radius_km(cfg)
	url = f"https://www.projektwerk.com/de/projekte?q={q}&location={quote_plus(location)}&radius={radius}"
	log.info(f"  Projektwerk: '{keyword}' (radius={radius}km)")

	r = safe_get(session, url, delay=cfg["request_delay_seconds"])
	if not r:
		return projects

	soup = BeautifulSoup(r.text, "lxml")

	for card in soup.select("div.project, div[class*='project'], article, li.result, div.card"):
		link_el = card.select_one("a[href*='projektwerk.com'], a[href*='/projekt'], a[href]")
		title_el = card.select_one("h2, h3, .project-title, [class*='title']")
		company_el = card.select_one(".company, [class*='company']")
		rate_el = card.select_one("[class*='rate'], [class*='budget']")
		remote_el = card.select_one("[class*='remote'], [class*='location']")

		title = title_el.get_text(strip=True) if title_el else ""
		href = link_el.get("href", "") if link_el else ""
		company = company_el.get_text(strip=True) if company_el else ""
		rate = rate_el.get_text(strip=True) if rate_el else ""
		remote_txt = remote_el.get_text(strip=True) if remote_el else ""

		if not title and link_el:
			title = link_el.get_text(strip=True)

		if title and href and len(title) > 5:
			full_url = urljoin("https://www.projektwerk.com", href)
			projects.append(Project(title, full_url, "Projektwerk", company, rate, remote_txt))

		if len(projects) >= cfg["max_results_per_source"]:
			break

	log.info(f"    → {len(projects)} results")
	return projects


def crawl_twago(session, keyword, location, cfg) -> list[Project]:
	"""Twago.de – European freelancer marketplace."""
	projects = []
	q = quote_plus(keyword)
	url = f"https://www.twago.de/project/search/?search={q}&sort=newest"
	log.info(f"  Twago: '{keyword}'")

	r = safe_get(session, url, delay=cfg["request_delay_seconds"])
	if not r:
		return projects

	soup = BeautifulSoup(r.text, "lxml")

	for card in soup.select("div.project, div[class*='project'], article, li.result, div.card"):
		link_el = card.select_one("a[href*='twago.de'], a[href*='/project/'], a[href]")
		title_el = card.select_one("h2, h3, .project-title, [class*='title']")
		company_el = card.select_one(".company, [class*='company'], [class*='client']")
		rate_el = card.select_one("[class*='budget'], [class*='price']")
		remote_el = card.select_one("[class*='remote'], [class*='location']")

		title = title_el.get_text(strip=True) if title_el else ""
		href = link_el.get("href", "") if link_el else ""
		company = company_el.get_text(strip=True) if company_el else ""
		rate = rate_el.get_text(strip=True) if rate_el else ""
		remote_txt = remote_el.get_text(strip=True) if remote_el else ""

		if not title and link_el:
			title = link_el.get_text(strip=True)

		if title and href and len(title) > 5:
			full_url = urljoin("https://www.twago.de", href)
			projects.append(Project(title, full_url, "Twago", company, rate, remote_txt))

		if len(projects) >= cfg["max_results_per_source"]:
			break

	log.info(f"    → {len(projects)} results")
	return projects


def crawl_upwork(session, keyword, location, cfg) -> list[Project]:
	"""Upwork – global freelancer marketplace (public search)."""
	projects = []
	q = quote_plus(keyword)
	url = f"https://www.upwork.com/nx/search/jobs/?q={q}&sort=recency"
	log.info(f"  Upwork: '{keyword}'")

	r = safe_get(session, url, delay=cfg["request_delay_seconds"])
	if not r:
		return projects

	soup = BeautifulSoup(r.text, "lxml")

	for card in soup.select("section.up-card-section, div[data-test='job-tile'], article, div[class*='job-tile']"):
		link_el = card.select_one("a[href*='/jobs/'], a[href*='upwork.com'], a[href]")
		title_el = card.select_one("h2, h3, a[data-test='job-tile-title'], [class*='title']")
		rate_el = card.select_one("[data-test='budget'], [class*='budget'], [class*='rate']")

		title = title_el.get_text(strip=True) if title_el else ""
		href = link_el.get("href", "") if link_el else ""
		rate = rate_el.get_text(strip=True) if rate_el else ""

		if not title and link_el:
			title = link_el.get_text(strip=True)

		if title and href and len(title) > 5:
			full_url = urljoin("https://www.upwork.com", href)
			projects.append(Project(title, full_url, "Upwork", "", rate, "Remote"))

		if len(projects) >= cfg["max_results_per_source"]:
			break

	log.info(f"    → {len(projects)} results")
	return projects


def crawl_malt(session, keyword, location, cfg) -> list[Project]:
	"""Malt.de – European freelancer platform (strong in DACH)."""
	projects = []
	q = quote_plus(keyword)
	url = f"https://www.malt.de/s?q={q}&page=1"
	log.info(f"  Malt: '{keyword}'")

	r = safe_get(session, url, delay=cfg["request_delay_seconds"])
	if not r:
		return projects

	soup = BeautifulSoup(r.text, "lxml")

	for card in soup.select("div[class*='project'], article, div.card, li.result, div[class*='search-result']"):
		link_el = card.select_one("a[href*='malt.de'], a[href*='/project/'], a[href]")
		title_el = card.select_one("h2, h3, [class*='title']")
		rate_el = card.select_one("[class*='rate'], [class*='price'], [class*='budget']")

		title = title_el.get_text(strip=True) if title_el else ""
		href = link_el.get("href", "") if link_el else ""
		rate = rate_el.get_text(strip=True) if rate_el else ""

		if not title and link_el:
			title = link_el.get_text(strip=True)

		if title and href and len(title) > 5:
			full_url = urljoin("https://www.malt.de", href)
			projects.append(Project(title, full_url, "Malt", "", rate, ""))

		if len(projects) >= cfg["max_results_per_source"]:
			break

	log.info(f"    → {len(projects)} results")
	return projects


# ─── Crawler Registry ───────────────────────────────────────────────────────

CRAWLERS = [
	("freelance.de",       crawl_freelance_de),
	("freelancermap.de",   crawl_freelancermap),
	("GULP",               crawl_gulp),
	("Hays (Contracting)", crawl_hays),
	("SOLCOM",             crawl_solcom),
	("Etengo",             crawl_etengo),
	("Projektwerk",        crawl_projektwerk),
	("Twago",              crawl_twago),
	("Upwork",             crawl_upwork),
	("Malt",               crawl_malt),
]


# ─── Deduplication ───────────────────────────────────────────────────────────

def deduplicate(projects: list[Project]) -> list[Project]:
	"""Removes duplicates based on normalized title + domain."""
	seen = set()
	unique = []
	for p in projects:
		domain = urlparse(p.url).netloc
		key = re.sub(r"\s+", " ", p.title.lower()) + "|" + domain
		if key not in seen:
			seen.add(key)
			unique.append(p)
	return unique


# ─── Markdown Export ─────────────────────────────────────────────────────────

def export_markdown(projects: list[Project], keywords: list[str], loc_display: str, output_path: Path):
	"""Writes all projects to a Markdown file."""
	today = datetime.now().strftime("%Y-%m-%d %H:%M")

	by_source: dict[str, list[Project]] = {}
	for p in projects:
		by_source.setdefault(p.source, []).append(p)

	lines = [
		f"# Freelance Projects – {today}",
		"",
		f"**Keywords:** {', '.join(keywords)}",
		f"**Location:** {loc_display}",
		f"**Projects found:** {len(projects)} (after deduplication)",
		f"**Sources:** {', '.join(by_source.keys())}",
		"",
		"---",
		"",
	]

	for source, source_projects in sorted(by_source.items()):
		lines.append(f"## {source} ({len(source_projects)})")
		lines.append("")
		lines.append("| # | Project Title | Company | Rate/Budget | Remote |")
		lines.append("|---|---------------|---------|-------------|--------|")
		for i, p in enumerate(source_projects, 1):
			title_link = f"[{p.title}]({p.url})"
			company = p.company if p.company else "–"
			rate = p.rate if p.rate else "–"
			remote = p.remote if p.remote else "–"
			lines.append(f"| {i} | {title_link} | {company} | {rate} | {remote} |")
		lines.append("")

	lines.append("---")
	lines.append("")
	lines.append("## All Results (Quick Overview)")
	lines.append("")
	for i, p in enumerate(projects, 1):
		company_str = f" – *{p.company}*" if p.company else ""
		rate_str = f" ({p.rate})" if p.rate else ""
		lines.append(f"{i}. [{p.title}]({p.url}){company_str}{rate_str} `{p.source}`")

	lines.append("")
	lines.append(f"---\n*Generated on {today} with Freelance Crawler*")

	output_path.parent.mkdir(parents=True, exist_ok=True)
	output_path.write_text("\n".join(lines), encoding="utf-8")
	log.info(f"Result saved: {output_path}")


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
	print()
	print("╔══════════════════════════════════════╗")
	print("║            FREELANCSTER              ║")
	print("╚══════════════════════════════════════╝")
	print()

	cfg = load_config()
	session = make_session(cfg["user_agent"])
	keywords = cfg["keywords"]
	location = _loc_string(cfg)
	loc_display = _loc_display(cfg)

	log.info(f"Keywords:  {keywords}")
	log.info(f"Location:  {loc_display}")
	log.info(f"Sources:   {len(CRAWLERS)} freelancer portals")
	print()

	all_projects: list[Project] = []

	for keyword in keywords:
		log.info(f"── Keyword: '{keyword}' ──")
		for name, crawler_fn in CRAWLERS:
			try:
				found = crawler_fn(session, keyword, location, cfg)
				all_projects.extend(found)
			except Exception as e:
				log.error(f"  {name} failed: {e}")
		print()

	unique_projects = deduplicate(all_projects)
	log.info(f"Total: {len(all_projects)} → {len(unique_projects)} after deduplication")

	today_str = datetime.now().strftime("%Y-%m-%d")
	output_path = Path(cfg["results_dir"]) / f"freelance_{today_str}.md"
	export_markdown(unique_projects, keywords, loc_display, output_path)

	print()
	print(f"  Output: {output_path}")
	print(f"  {len(unique_projects)} projects found")
	print()


if __name__ == "__main__":
	main()