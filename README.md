<p align="center">
  <img src="logo.png" alt="JOBSTER Logo" width="400">
</p>

<p align="center">
  <strong>Crawl daily job listings from all major German job portals with a single command.</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.8+-blue?logo=python&logoColor=white" alt="Python 3.8+">
  <img src="https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20Windows-lightgrey" alt="Platform">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
</p>

---

## What It Does

JOBSTER searches **10 German job portals** simultaneously for your configured keywords and location, deduplicates the results, and outputs a clean Markdown file with clickable links.

### Supported Portals

| Portal | Type |
|--------|------|
| StepStone | Job board |
| LinkedIn | Professional network |
| XING | Professional network (DACH) |
| Glassdoor | Job board + reviews |
| Monster | Job board |
| Arbeitsagentur | Federal employment agency |
| Stellenanzeigen.de | Job board |
| Karriere.de | Job board (Handelsblatt) |
| Kimeta | Meta search engine |
| Jobborse.de | Aggregator |

## Quick Start

```bash
# Install dependencies
pip install requests beautifulsoup4 lxml

# Run the crawler
python jobster.py
```

Results are saved to `./results/jobs_YYYY-MM-DD.md`.

## Configuration

Edit `config.json` to customize your search. A default config is created on first run.

```json
{
  "keywords": ["Python Developer", "Data Engineer", "DevOps"],
  "location": {
    "country": "Deutschland",
    "city": "Berlin",
    "radius_km": 50
  },
  "results_dir": "./results",
  "max_results_per_source": 50,
  "request_delay_seconds": 2
}
```

| Option | Description |
|--------|-------------|
| `keywords` | List of job titles or skills to search for |
| `location.country` | Country (used when no city is set) |
| `location.city` | City to center the search on |
| `location.radius_km` | Search radius in kilometers |
| `max_results_per_source` | Max jobs to collect per portal per keyword |
| `request_delay_seconds` | Delay between requests (be respectful to servers) |

## Output

The crawler generates a Markdown file organized by portal, with a summary header and a combined quick-overview list at the end.

```
results/
  jobs_2026-05-04.md
```

Each entry includes the job title (as a clickable link), company name, and source portal.

## Automation

Set up a daily cron job to run the crawler automatically:

```bash
crontab -e
```

```
0 8 * * * cd /path/to/jobster && python3 jobster.py
```

## Requirements

- Python 3.8+
- `requests`
- `beautifulsoup4`
- `lxml`
