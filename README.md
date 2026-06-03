# ⚡ Performance Test Platform

Automated Frontend + Backend Performance Testing — zero-config, runs from any URL.

Feed it a website URL, it auto-discovers every page and API endpoint, runs real browser tests and parallel load tests, then generates an HTML report with a color-coded scorecard (A-F grade), detected issues, fix recommendations, and an AI-powered analysis via LLM.

## Features

- 🔍 **Auto-discovery** — reactive scanner finds all pages and APIs via browser intercept, form crawls, JS analysis, sitemap, robots.txt, OpenAPI spec
- 🖥️ **Frontend performance** — Playwright real browser metrics (FCP, LCP, CLS, TTFB, load time, resource errors) per page
- ⚙️ **Backend load testing** — parallel requests with configurable concurrency, full latency histogram (p50/p75/p90/p95/p99), error rate per endpoint
- 👥 **Concurrent user simulation** — simulate 1-500 simultaneous users per request
- 🤖 **AI analysis** — 9router LLM generates executive summary + technical fix recommendations
- 📊 **HTML report** — color-coded metrics (green/yellow/red), A-F grading, severity-based issue detection
- 🌐 **Web UI** — dark-themed dashboard at `http://localhost:8099` with real-time progress polling

## Requirements

- Python 3.9+
- Google Chrome (for Playwright frontend tests)

## Quick Start

```bash
git clone https://github.com/yudhistirael/perf-test-platform.git
cd perf-test-platform
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium

# Optional: add LLM API key for AI analysis in reports
cp .env.example .env
# Edit .env with your API key

python3 web_server.py
```

Open **http://localhost:8099** in your browser.

## Configuration

| Field | Default | Description |
|---|---|---|
| Target URL | — | Website to test (required) |
| Email / Password | — | Optional login credentials (auto-login before testing) |
| Max Pages (FE) | 10 | Frontend pages to test |
| Max Endpoints (BE) | 15 | Backend endpoints to load test |
| Requests / Endpoint | 15 | Number of requests per endpoint |
| Concurrent Users | 1 | Simultaneous requests per endpoint |

## .env (optional)

```
ROUTER_API_KEY=your-api-key-here
ROUTER_API_BASE=http://your-llm-proxy:port/v1
ROUTER_MODEL=your-model-name
```

Without `.env`, the report still works — just skips the AI analysis section.

## Project Structure

```
perf-test-platform/
├── web_server.py              # FastAPI web server + test orchestrator
├── scanner/api_scanner.py     # Reactive endpoint/page auto-discovery
├── fe_test/lighthouse_test.py # Frontend performance (Playwright)
├── be_test/api_load_test.py   # Backend load testing (httpx)
├── reports/report_generator.py # HTML report + 9router LLM analysis
├── templates/                 # Jinja2 HTML templates (index, job, reports)
├── reports/                   # Generated HTML reports (gitignored)
├── requirements.txt
├── .env.example               # Template for API credentials
└── README.md
```

## License

MIT
