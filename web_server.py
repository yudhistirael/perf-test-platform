#!/usr/bin/env python3
"""FastAPI web server for Performance Test Platform."""
import asyncio
import json
import uuid
import os
from datetime import datetime
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

# Load .env file
load_dotenv()

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Form
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from scanner.api_scanner import APIScanner
from fe_test.lighthouse_test import LighthouseTest
from be_test.api_load_test import APILoadTest
from reports.report_generator import ReportGenerator

app = FastAPI(title="Performance Test Platform", version="1.0")

REPORTS_DIR = Path(__file__).parent / 'reports'
REPORTS_DIR.mkdir(exist_ok=True)

# Setup templates FIRST
from jinja2 import Environment, FileSystemLoader
TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / 'templates'))

# Track running jobs
jobs = {}
ws_clients = {}  # job_id -> list of websocket connections


async def broadcast(job_id: str, data: dict):
    """Send status update to all connected WebSocket clients for this job."""
    if job_id in ws_clients:
        dead = []
        for ws in ws_clients[job_id]:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            ws_clients[job_id].remove(ws)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Main dashboard page."""
    # List past reports
    reports = sorted(REPORTS_DIR.glob('report_*.html'), reverse=True)[:20]
    report_list = []
    for r in reports:
        report_list.append({
            'filename': r.name,
            'size': f"{r.stat().st_size / 1024:.1f} KB",
            'date': datetime.fromtimestamp(r.stat().st_mtime).strftime('%Y-%m-%d %H:%M'),
        })
    return TEMPLATES.TemplateResponse(request=request, name="index.html", context={"reports": report_list})


@app.post("/api/test")
async def run_test(
    base_url: str = Form(...),
    email: Optional[str] = Form(None),
    password: Optional[str] = Form(None),
    fe_pages_max: int = Form(10),
    be_endpoints_max: int = Form(15),
    be_requests_per_endpoint: int = Form(15),
    concurrent_users: int = Form(1),
):
    """Start a new performance test."""
    job_id = str(uuid.uuid4())[:8]
    jobs[job_id] = {
        'id': job_id,
        'base_url': base_url,
        'email': email,
        'fe_pages_max': fe_pages_max,
        'be_endpoints_max': be_endpoints_max,
        'be_requests_per_endpoint': be_requests_per_endpoint,
        'concurrent_users': concurrent_users,
        'status': 'starting',
        'progress': 0,
        'message': 'Initializing...',
        'started_at': datetime.now().isoformat(),
        'report_path': None,
    }
    ws_clients[job_id] = []

    # Run test in background
    asyncio.create_task(_run_test_async(job_id, base_url, email, password, fe_pages_max, be_endpoints_max, be_requests_per_endpoint, concurrent_users))

    return RedirectResponse(url=f"/job/{job_id}", status_code=302)


@app.get("/api/job/{job_id}")
async def get_job_status(job_id: str):
    """Return current job status for polling."""
    if job_id not in jobs:
        return {"error": "Job not found", "status": "error"}
    return jobs[job_id]


@app.get("/job/{job_id}", response_class=HTMLResponse)
async def job_page(request: Request, job_id: str):
    """Job status page with live WebSocket updates."""
    if job_id not in jobs:
        return HTMLResponse("Job not found", status_code=404)
    return TEMPLATES.TemplateResponse(request=request, name="job.html", context={"job_id": job_id, "job": jobs[job_id]})


@app.websocket("/ws/{job_id}")
async def websocket_endpoint(websocket: WebSocket, job_id: str):
    """WebSocket for real-time job updates."""
    await websocket.accept()
    if job_id not in ws_clients:
        ws_clients[job_id] = []
    ws_clients[job_id].append(websocket)

    # Send current status immediately
    if job_id in jobs:
        await websocket.send_json(jobs[job_id])

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_clients[job_id].remove(websocket)


@app.get("/report/{filename}")
async def view_report(filename: str):
    """Serve a report file."""
    path = REPORTS_DIR / filename
    if path.exists():
        return FileResponse(path, media_type='text/html')
    return HTMLResponse("Report not found", status_code=404)


@app.get("/reports")
async def list_reports(request: Request):
    """List all past reports."""
    reports = sorted(REPORTS_DIR.glob('report_*.html'), reverse=True)
    report_list = []
    for r in reports:
        report_list.append({
            'filename': r.name,
            'size': f"{r.stat().st_size / 1024:.1f} KB",
            'date': datetime.fromtimestamp(r.stat().st_mtime).strftime('%Y-%m-%d %H:%M'),
        })
    return TEMPLATES.TemplateResponse(request=request, name="reports.html", context={"reports": report_list})


async def _run_test_async(job_id: str, base_url: str, email: str, password: str, fe_pages_max: int = 10, be_endpoints_max: int = 15, be_requests_per_endpoint: int = 15, concurrent_users: int = 1):
    """Run full test pipeline with live progress updates."""
    job = jobs[job_id]

    def update(progress, message, status=None):
        job['progress'] = progress
        job['message'] = message
        if status:
            job['status'] = status
        asyncio.create_task(broadcast(job_id, dict(job)))

    try:
        update(5, 'Authenticating...', 'running')
        await asyncio.sleep(0.3)

        # Step 1: Scan
        update(10, 'Scanning API endpoints...')
        scanner = APIScanner(base_url)
        endpoints = await scanner.discover()
        update(25, f'Discovered {len(endpoints)} API endpoints')
        await asyncio.sleep(0.3)

        # Step 2: FE Test — reuse pages discovered by scanner
        update(30, 'Running Frontend Performance Tests...')
        # Build page list from scanner's discovered paths, limited by fe_pages_max
        scanned_pages = []
        seen_paths = {'/'}
        for ep in endpoints:
            path = ep['path']
            if path and path not in seen_paths and not path.startswith(('http',)):
                seen_paths.add(path)
                name = path.strip('/').split('/')[-1].replace('-', ' ').title() or 'Homepage'
                scanned_pages.append({'path': path, 'name': name})
                if len(scanned_pages) >= fe_pages_max:
                    break
        if not scanned_pages:
            scanned_pages = [{'path': '/', 'name': 'Homepage'}]
        fe_test = LighthouseTest(base_url, email, password, pre_discovered_pages=scanned_pages)
        fe_results = await fe_test.run()
        fe_count = sum(1 for v in fe_results.values() if isinstance(v, dict) and 'load_time_ms' in v)
        update(60, f'Frontend: tested {fe_count} pages')
        await asyncio.sleep(0.3)

        # Step 3: BE Test
        update(65, 'Running Backend Load Tests...')
        be_test = APILoadTest(base_url, email, password, be_endpoints_max, be_requests_per_endpoint)
        be_results = {}
        # Reuse endpoints already discovered in Step 1 (don't re-scan)
        be_endpoints = endpoints
        # Authenticate once if credentials provided
        if email and password:
            try:
                await be_test._authenticate()
            except Exception:
                pass
        total_ep = min(len(be_endpoints), be_endpoints_max) or 1
        for i, ep in enumerate(be_endpoints[:be_endpoints_max]):
            pct = 65 + int((i / total_ep) * 18)
            update(pct, f'Backend [{i+1}/{total_ep}]: {ep["method"]} {ep["path"]}')
            try:
                result = await be_test._test_endpoint(ep['path'], ep['method'], be_requests_per_endpoint, concurrent_users)
                be_results[f"{ep['method']} {ep['path']}"] = result
            except Exception as e:
                be_results[f"{ep['method']} {ep['path']}"] = {'error': str(e)}
        be_count = sum(1 for v in be_results.values() if isinstance(v, dict) and 'avg_latency_ms' in v)
        update(85, f'Backend: load tested {be_count} endpoints')
        await asyncio.sleep(0.3)

        # Step 4: Generate Report
        update(90, 'Generating performance report...')
        results = {'frontend': fe_results, 'backend': be_results}
        generator = ReportGenerator(base_url, results)
        report_path = generator.generate()
        job['report_path'] = report_path

        update(100, 'Test complete!', 'done')

    except Exception as e:
        update(job['progress'], f'Error: {str(e)}', 'error')


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8099)
