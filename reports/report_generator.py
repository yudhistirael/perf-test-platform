#!/usr/bin/env python3
"""Report Generator — Dynamic performance report with real issue detection."""
import json
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse


class ReportGenerator:
    def __init__(self, base_url: str, results: dict, stack: dict = None, concurrent_users: int = 1):
        self.base_url = base_url
        self.results = results
        self.stack = stack or {}
        self.concurrent_users = concurrent_users
        self.domain = urlparse(base_url).netloc

    async def _get_llm_analysis(self, fe, be, issues, overall):
        """Get 9router LLM analysis of performance results."""
        try:
            import httpx
            import os

            api_key = os.environ.get('ROUTER_API_KEY', '')
            api_base = os.environ.get('ROUTER_API_BASE', 'http://46.250.234.185:20128/v1')
            model = os.environ.get('ROUTER_MODEL', 'kr/claude-sonnet-4.5')
            if not api_key:
                print("ROUTER_API_KEY not set, skipping LLM analysis")
                return None

            # Prepare summary for LLM
            summary = f"""
Performance Test Results for {self.base_url}:
- Overall Grade: {overall['grade']}
- Frontend Pages Tested: {len([v for v in fe.values() if isinstance(v, dict)])}
- Backend Endpoints Tested: {len([v for v in be.values() if isinstance(v, dict)])}
- Critical Issues: {len([i for i in issues if i['severity'] == 'critical'])}
- High Issues: {len([i for i in issues if i['severity'] == 'high'])}

Key Metrics:
- Average FE Load Time: {overall.get('avg_fe_load_time', 'N/A')}ms
- Average BE Latency: {overall.get('avg_be_latency', 'N/A')}ms
- Error Rate: {overall.get('error_rate', 'N/A')}%

Issues Found: {json.dumps([{'type': i.get('type','?'), 'severity': i.get('severity','?'), 'message': i.get('message','')} for i in issues[:5]], indent=2)}

Provide:
1. Ringkasan umum (2-3 kalimat) tentang performa secara keseluruhan
2. Top 3 perbaikan teknis yang dibutuhkan (spesifik dan bisa diimplementasikan)
3. Estimasi dampak jika perbaikan tersebut dilakukan

IMPORTANT: Tulis semua output dalam Bahasa Indonesia. Gunakan format markdown yang rapi.
"""
            
            async with httpx.AsyncClient(timeout=httpx.Timeout(30)) as client:
                r = await client.post(
                    f"{api_base}/chat/completions",
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": summary}],
                        "temperature": 0.7,
                        "max_tokens": 1000,
                        "stream": False,
                    },
                    headers={"Authorization": f"Bearer {api_key}"}
                )
                if r.status_code == 200:
                    data = r.json()
                    analysis = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                    print(f"LLM analysis success: {len(analysis)} chars")
                    return analysis
                else:
                    print(f"LLM HTTP error: {r.status_code} - {r.text[:200]}")
                    return None
        except Exception as e:
            print(f"LLM analysis exception: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
        return None

    def generate(self):
        """Generate HTML report (sync wrapper)."""
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    future = pool.submit(asyncio.run, self._generate_async())
                    return future.result()
            else:
                return loop.run_until_complete(self._generate_async())
        except Exception:
            return asyncio.run(self._generate_async())

    async def _generate_async(self):
        """Generate HTML report."""
        report_dir = Path(__file__).parent.parent / 'reports'
        report_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        report_path = report_dir / f'report_{timestamp}.html'

        fe = self.results.get('frontend', {})
        be = self.results.get('backend', {})
        issues = self._find_issues(fe, be)
        overall = self._calc_overall(fe, be, issues)
        recommendations = self._generate_recommendations(fe, be, issues)

        # Get LLM analysis from 9router
        ai_analysis = await self._get_llm_analysis(fe, be, issues, overall)

        html = self._build_html(fe, be, overall, issues, recommendations, ai_analysis)
        report_path.write_text(html)
        return str(report_path)

    def _find_issues(self, fe, be):
        """Detect real issues from actual test data."""
        issues = []

        # Frontend issues
        for path, data in fe.items():
            if not isinstance(data, dict) or 'scores' not in data:
                continue
            s = data['scores']
            name = data.get('name', path)

            if s.get('fcp') == 'poor':
                val = data.get('fcp_ms', 0)
                issues.append({
                    'severity': 'high',
                    'type': 'frontend',
                    'metric': 'FCP',
                    'page': name,
                    'path': path,
                    'value': f'{val:.0f}ms',
                    'threshold': '<1800ms',
                    'detail': f'First Contentful Paint is slow ({val:.0f}ms). Users see blank screen too long.',
                })
            if s.get('lcp') == 'poor':
                val = data.get('lcp_ms', 0)
                issues.append({
                    'severity': 'high',
                    'type': 'frontend',
                    'metric': 'LCP',
                    'page': name,
                    'path': path,
                    'value': f'{val:.0f}ms',
                    'threshold': '<2500ms',
                    'detail': f'Largest Contentful Paint is slow ({val:.0f}ms). Main content loads too late.',
                })
            if s.get('cls') == 'poor':
                val = data.get('cls', 0)
                issues.append({
                    'severity': 'medium',
                    'type': 'frontend',
                    'metric': 'CLS',
                    'page': name,
                    'path': path,
                    'value': f'{val:.4f}',
                    'threshold': '<0.1',
                    'detail': f'Layout shifts detected ({val:.4f}). Page elements jump during load.',
                })
            if s.get('ttfb') == 'poor':
                val = data.get('ttfb_ms', 0)
                issues.append({
                    'severity': 'medium',
                    'type': 'frontend',
                    'metric': 'TTFB',
                    'page': name,
                    'path': path,
                    'value': f'{val:.0f}ms',
                    'threshold': '<800ms',
                    'detail': f'Server responds slowly ({val:.0f}ms). Backend or CDN is slow.',
                })
            if data.get('errors', 0) > 0:
                issues.append({
                    'severity': 'medium',
                    'type': 'frontend',
                    'metric': 'Resource Errors',
                    'page': name,
                    'path': path,
                    'value': str(data['errors']),
                    'threshold': '0',
                    'detail': f'{data["errors"]} resource requests failed (CSS/JS/images not loading).',
                })
            if data.get('load_time_ms', 0) > 5000:
                issues.append({
                    'severity': 'high',
                    'type': 'frontend',
                    'metric': 'Load Time',
                    'page': name,
                    'path': path,
                    'value': f'{data["load_time_ms"]:.0f}ms',
                    'threshold': '<3000ms',
                    'detail': f'Page takes {data["load_time_ms"]/1000:.1f}s to load. Users will leave.',
                })

        # Backend issues
        for endpoint, data in be.items():
            if not isinstance(data, dict) or 'avg_latency_ms' not in data:
                continue
            avg = data['avg_latency_ms']
            err = data.get('error_rate', 0)
            p99 = data.get('p99_latency_ms', 0)

            if avg > 3000:
                issues.append({
                    'severity': 'critical',
                    'type': 'backend',
                    'metric': 'High Latency',
                    'endpoint': endpoint,
                    'value': f'{avg:.0f}ms',
                    'threshold': '<1000ms',
                    'detail': f'Average response time is {avg:.0f}ms. API is very slow.',
                })
            elif avg > 1000:
                issues.append({
                    'severity': 'medium',
                    'type': 'backend',
                    'metric': 'Slow Response',
                    'endpoint': endpoint,
                    'value': f'{avg:.0f}ms',
                    'threshold': '<1000ms',
                    'detail': f'Average response time is {avg:.0f}ms. Should be under 1s.',
                })

            if err > 5:
                issues.append({
                    'severity': 'critical',
                    'type': 'backend',
                    'metric': 'High Error Rate',
                    'endpoint': endpoint,
                    'value': f'{err:.1f}%',
                    'threshold': '<1%',
                    'detail': f'{err:.1f}% of requests failed. Server errors or timeouts.',
                })
            elif err > 0:
                issues.append({
                    'severity': 'low',
                    'type': 'backend',
                    'metric': 'Error Rate',
                    'endpoint': endpoint,
                    'value': f'{err:.1f}%',
                    'threshold': '0%',
                    'detail': f'{err:.1f}% of requests failed. Intermittent errors detected.',
                })

            if p99 > 5000:
                issues.append({
                    'severity': 'high',
                    'type': 'backend',
                    'metric': 'P99 Latency',
                    'endpoint': endpoint,
                    'value': f'{p99:.0f}ms',
                    'threshold': '<5000ms',
                    'detail': f'Worst-case responses (P99) take {p99/1000:.1f}s. Users experience severe delays.',
                })

        return sorted(issues, key=lambda x: {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}.get(x['severity'], 4))

    def _calc_overall(self, fe, be, issues):
        """Calculate overall score based on real findings."""
        score = 100
        for issue in issues:
            if issue['severity'] == 'critical': score -= 20
            elif issue['severity'] == 'high': score -= 12
            elif issue['severity'] == 'medium': score -= 6
            elif issue['severity'] == 'low': score -= 2
        score = max(score, 0)
        grade = 'A' if score >= 90 else 'B' if score >= 80 else 'C' if score >= 70 else 'D' if score >= 60 else 'F'
        return {'score': score, 'grade': grade}

    def _generate_recommendations(self, fe, be, issues):
        """Generate actionable recommendations from issues."""
        recs = []
        has_fe_slow = any(i['metric'] in ('FCP', 'LCP', 'Load Time') and i['type'] == 'frontend' for i in issues)
        has_be_slow = any(i['metric'] in ('High Latency', 'Slow Response') and i['type'] == 'backend' for i in issues)
        has_cls = any(i['metric'] == 'CLS' for i in issues)
        has_errors = any(i['metric'] in ('High Error Rate', 'Error Rate') for i in issues)
        has_ttfb = any(i['metric'] == 'TTFB' for i in issues)

        if has_fe_slow:
            recs.append({
                'category': 'Frontend',
                'priority': 'High',
                'title': 'Optimize Page Load Speed',
                'actions': [
                    'Enable gzip/brotli compression on server',
                    'Minify and bundle CSS/JS assets',
                    'Add lazy loading for images below the fold',
                    'Use a CDN for static assets',
                    'Implement code splitting and tree shaking',
                ]
            })
        if has_cls:
            recs.append({
                'category': 'Frontend',
                'priority': 'Medium',
                'title': 'Fix Layout Shifts',
                'actions': [
                    'Set explicit width/height on all images',
                    'Reserve space for dynamic content (ads, embeds)',
                    'Load fonts with font-display: swap',
                    'Avoid inserting content above existing content',
                ]
            })
        if has_ttfb or has_be_slow:
            recs.append({
                'category': 'Backend',
                'priority': 'High',
                'title': 'Improve Server Response Time',
                'actions': [
                    'Add Redis/Memcached caching for frequent queries',
                    'Optimize slow database queries (add indexes)',
                    'Implement connection pooling',
                    'Use a reverse proxy (Nginx/Varnish)',
                    'Consider horizontal scaling if load is high',
                ]
            })
        if has_errors:
            recs.append({
                'category': 'Backend',
                'priority': 'Critical',
                'title': 'Fix Server Errors',
                'actions': [
                    'Check server logs for 5xx errors',
                    'Add error monitoring (Sentry, Datadog)',
                    'Implement retry logic with backoff',
                    'Check for memory leaks or resource exhaustion',
                    'Add health check endpoint',
                ]
            })
        if not recs:
            recs.append({
                'category': 'General',
                'priority': 'Info',
                'title': 'Performance Looks Good',
                'actions': [
                    'Set up continuous monitoring to track regressions',
                    'Test with higher load to find breaking points',
                    'Test from different geographic locations',
                ]
            })
        return recs

    def _score_color(self, score):
        if score in ('good',): return '#10b981'
        if score in ('needs_improvement',): return '#f59e0b'
        if score in ('unknown',): return '#64748b'
        return '#ef4444'

    def _build_html(self, fe, be, overall, issues, recommendations, ai_analysis=None):
        fe_rows = []
        for path, data in fe.items():
            if not isinstance(data, dict) or 'scores' not in data:
                continue
            s = data['scores']
            fe_rows.append(f'''<tr>
                <td><code>{path}</code></td>
                <td>{data.get('name','')}</td>
                <td>{data.get('status_code','-')}</td>
                <td style="color:{self._score_color(s.get('ttfb','unknown'))}">{data.get('ttfb_ms','-')}ms</td>
                <td style="color:{self._score_color(s.get('fcp','unknown'))}">{data.get('fcp_ms','-')}ms</td>
                <td style="color:{self._score_color(s.get('lcp','unknown'))}">{data.get('lcp_ms','-')}ms</td>
                <td style="color:{self._score_color(s.get('cls','unknown'))}">{data.get('cls','-')}</td>
                <td style="color:{self._score_color(s.get('load','unknown'))}">{data.get('load_time_ms','-')}ms</td>
                <td>{data.get('total_requests',0)}</td>
                <td>{data.get('total_size_kb',0):.1f}KB</td>
                <td>{data.get('errors',0)}</td>
            </tr>''')

        be_rows = []
        for endpoint, data in be.items():
            if not isinstance(data, dict) or 'avg_latency_ms' not in data:
                continue
            avg = data['avg_latency_ms']
            er = data.get('error_rate', 0)
            lat_color = '#10b981' if avg < 1000 else '#f59e0b' if avg < 3000 else '#ef4444'
            err_color = '#10b981' if er < 1 else '#f59e0b' if er < 5 else '#ef4444'
            be_rows.append(f'''<tr>
                <td><code>{endpoint}</code></td>
                <td style="color:{lat_color}">{avg:.0f}ms</td>
                <td>{data.get('min_latency_ms','-')}ms</td>
                <td>{data.get('max_latency_ms','-')}ms</td>
                <td>{data.get('median_latency_ms','-')}ms</td>
                <td>{data.get('p75_latency_ms','-')}ms</td>
                <td>{data.get('p90_latency_ms','-')}ms</td>
                <td>{data.get('p95_latency_ms','-')}ms</td>
                <td>{data.get('p99_latency_ms','-')}ms</td>
                <td style="color:{err_color}">{er}%</td>
                <td>{data.get('success',0)}/{data.get('total_requests',0)}</td>
            </tr>''')

        # Build stack section
        stack_html = ''
        if self.stack:
            badges = []
            for key, val in self.stack.items():
                if val:
                    badges.append(f'<span style="display:inline-block;background:#3b82f6;color:#fff;padding:0.25rem 0.75rem;border-radius:20px;font-size:0.75rem;margin-right:0.5rem;margin-bottom:0.25rem">{key}: {val}</span>')
            if badges:
                stack_html = f'''<div style="background:#1e293b;padding:1rem;border-radius:8px;margin-bottom:1.5rem">
                    <h3 style="color:#e2e8f0;margin-bottom:0.5rem;font-size:0.9rem;text-transform:uppercase">📊 Detected Stack</h3>
                    <div>{''.join(badges)}</div>
                </div>'''
        
        # Concurrent users info
        concurrent_info = f'<p style="color:#94a3b8;font-size:0.85rem">Concurrent Users Simulated: <strong>{self.concurrent_users}</strong></p>' if self.concurrent_users > 1 else ''
        
        grade_color = '#10b981' if overall['grade'] in ('A','B') else '#f59e0b' if overall['grade'] == 'C' else '#ef4444'

        issues_html = ''
        for i in issues:
            sev_color = '#ef4444' if i['severity'] in ('critical','high') else '#f59e0b' if i['severity'] == 'medium' else '#64748b'
            sev_bg = '#ef444422' if i['severity'] in ('critical','high') else '#f59e0b22' if i['severity'] == 'medium' else '#64748b22'
            label = i.get('page', i.get('endpoint', ''))
            issues_html += f'''<div style="background:{sev_bg};border-left:4px solid {sev_color};padding:1rem;border-radius:8px;margin-bottom:0.75rem">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.5rem">
                    <span style="color:{sev_color};font-weight:700;font-size:0.8rem;text-transform:uppercase">{i['severity']}</span>
                    <span style="color:#94a3b8;font-size:0.8rem">{i['metric']}: {i['value']} (target: {i['threshold']})</span>
                </div>
                <div style="color:#e2e8f0;font-size:0.9rem">{i['detail']}</div>
                <div style="color:#64748b;font-size:0.8rem;margin-top:0.3rem">{label}</div>
            </div>'''

        recs_html = ''
        for rec in recommendations:
            pri_color = '#ef4444' if rec['priority'] == 'Critical' else '#f59e0b' if rec['priority'] == 'High' else '#3b82f6'
            actions_html = ''.join(f'<li>{a}</li>' for a in rec['actions'])
            recs_html += f'''<div style="background:#1e293b;border-radius:8px;padding:1rem;margin-bottom:1rem;border:1px solid #334155">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.5rem">
                    <strong style="color:#f1f5f9">{rec['title']}</strong>
                    <span style="color:{pri_color};font-size:0.8rem;font-weight:600">{rec['priority']}</span>
                </div>
                <ul style="padding-left:1.5rem;color:#94a3b8;font-size:0.85rem">{actions_html}</ul>
            </div>'''

        if not issues_html:
            issues_html = '<div style="color:#10b981;text-align:center;padding:1rem">✅ No critical issues detected</div>'

        return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Performance Report — {self.domain}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:#0a0f1e;color:#e2e8f0;padding:2rem;min-height:100vh}}
.container{{max-width:1400px;margin:0 auto}}
h1{{font-size:2rem;font-weight:800;margin-bottom:0.4rem;color:#f1f5f9;letter-spacing:-0.02em}}
.subtitle{{color:#64748b;margin-bottom:2.5rem;font-size:0.9rem;display:flex;align-items:center;gap:0.5rem}}
.subtitle code{{background:#1e293b;padding:0.2rem 0.6rem;border-radius:6px;color:#94a3b8;font-size:0.85rem}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:1rem;margin-bottom:2rem}}
.card{{background:linear-gradient(135deg,#1e293b,#162032);border-radius:16px;padding:1.5rem;text-align:center;border:1px solid #334155;transition:transform 0.2s;position:relative;overflow:hidden}}
.card::before{{content:'';position:absolute;top:0;left:0;right:0;height:3px;background:linear-gradient(90deg,#3b82f6,#8b5cf6)}}
.card h3{{font-size:0.7rem;color:#64748b;margin-bottom:0.75rem;text-transform:uppercase;letter-spacing:0.08em;font-weight:600}}
.card .value{{font-size:2.2rem;font-weight:800;line-height:1}}
.card .sublabel{{font-size:0.7rem;color:#475569;margin-top:0.4rem}}
.section{{background:#111827;border-radius:16px;padding:1.75rem;margin-bottom:1.5rem;border:1px solid #1e293b}}
.section h2{{font-size:1rem;font-weight:700;margin-bottom:1.25rem;color:#f1f5f9;display:flex;align-items:center;gap:0.5rem;padding-bottom:0.75rem;border-bottom:1px solid #1e293b;letter-spacing:-0.01em}}
.ai-box{{background:#0d1424;padding:1.5rem;border-radius:12px;border-left:4px solid #3b82f6;font-size:0.88rem;line-height:1.8;color:#cbd5e1}}
.ai-box h2,.ai-box h3{{color:#93c5fd;margin:1rem 0 0.4rem;font-size:0.9rem}}
.ai-box strong{{color:#e2e8f0}}
.ai-box ul,.ai-box ol{{padding-left:1.25rem;margin:0.5rem 0}}
.ai-box li{{margin-bottom:0.3rem}}
table{{width:100%;border-collapse:collapse;font-size:0.78rem}}
th{{text-align:left;padding:0.7rem 0.8rem;background:#1e293b;color:#64748b;font-weight:600;font-size:0.7rem;text-transform:uppercase;letter-spacing:0.05em;position:sticky;top:0}}
th:first-child{{border-radius:8px 0 0 8px}}
th:last-child{{border-radius:0 8px 8px 0}}
td{{padding:0.65rem 0.8rem;border-bottom:1px solid #1a2235;color:#cbd5e1}}
tr:last-child td{{border-bottom:none}}
tr:hover td{{background:#1e293b}}
code{{background:#1e293b;padding:0.15rem 0.4rem;border-radius:5px;font-size:0.78rem;color:#93c5fd;font-family:"Fira Code","Cascadia Code",monospace}}
.badge{{display:inline-flex;align-items:center;padding:0.2rem 0.6rem;border-radius:9999px;font-size:0.68rem;font-weight:700;letter-spacing:0.03em}}
.good{{background:#10b98115;color:#34d399;border:1px solid #10b98133}}
.needs_improvement{{background:#f59e0b15;color:#fbbf24;border:1px solid #f59e0b33}}
.poor{{background:#ef444415;color:#f87171;border:1px solid #ef444433}}
.unknown{{background:#64748b15;color:#94a3b8;border:1px solid #64748b33}}
.issue-card{{border-radius:10px;padding:1rem 1.25rem;margin-bottom:0.75rem;display:flex;align-items:flex-start;gap:0.75rem}}
.issue-dot{{width:10px;height:10px;border-radius:50%;margin-top:4px;flex-shrink:0}}
.sev-label{{font-size:0.65rem;font-weight:700;text-transform:uppercase;letter-spacing:0.06em;padding:0.15rem 0.5rem;border-radius:4px;margin-left:auto;flex-shrink:0}}
.rec-card{{background:#0f172a;border:1px solid #1e293b;border-radius:10px;padding:1rem 1.25rem;margin-bottom:0.75rem}}
.rec-card h4{{color:#93c5fd;font-size:0.85rem;margin-bottom:0.3rem}}
.rec-card p{{color:#94a3b8;font-size:0.82rem;line-height:1.6}}
.timestamp{{text-align:center;color:#334155;font-size:0.75rem;margin-top:2.5rem;padding-top:1.5rem;border-top:1px solid #1e293b}}
.table-wrap{{overflow-x:auto;border-radius:10px}}
.legend{{margin-top:1rem;font-size:0.72rem;color:#475569;display:flex;gap:0.75rem;flex-wrap:wrap;align-items:center}}
.legend strong{{color:#64748b}}
</style>
</head>
<body>
<div class="container">
<h1>📊 Performance Test Report</h1>
<p class="subtitle">Target: <code>{self.base_url}</code> · Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
{stack_html}
{concurrent_info}
<div style="text-align:right;margin-bottom:1rem">
    <button onclick="window.print()" style="background:#3b82f6;color:#fff;border:none;padding:0.5rem 1.2rem;border-radius:8px;cursor:pointer;font-size:0.85rem">🖨️ Print / Save PDF</button>
</div>
<div class="grid">
<div class="card"><h3>Overall Grade</h3><div class="value" style="color:{grade_color}">{overall['grade']}</div></div>
<div class="card"><h3>Score</h3><div class="value">{overall['score']}/100</div></div>
<div class="card"><h3>Issues</h3><div class="value" style="color:{'#ef4444' if len(issues) > 3 else '#f59e0b' if len(issues) > 0 else '#10b981'}">{len(issues)}</div></div>
<div class="card"><h3>Pages Tested</h3><div class="value">{len(fe)}</div></div>
<div class="card"><h3>APIs Tested</h3><div class="value">{len(be)}</div></div>
</div>

<div class="section">
<h2>🔍 Issues Found ({len(issues)})</h2>
{issues_html}
</div>

<div class="section">
<h2>🤖 AI Performance Analysis</h2>
<div style="background:#0f172a;padding:1.5rem;border-radius:12px;border-left:4px solid #3b82f6;white-space:pre-wrap;font-size:0.9rem;line-height:1.6;color:#e2e8f0">
{ai_analysis or "AI analysis unavailable - LLM service error"}
</div>
</div>

<div class="section">
<h2>💡 Recommendations</h2>
{recs_html}
</div>

<div class="section">
<h2>🖥️ Frontend Performance</h2>
<div style="overflow-x:auto">
<table>
<thead><tr><th>Path</th><th>Page</th><th>Status</th><th>TTFB</th><th>FCP</th><th>LCP</th><th>CLS</th><th>Load Time</th><th>Requests</th><th>Size</th><th>Errors</th></tr></thead>
<tbody>{"".join(fe_rows) or "<tr><td colspan=11 style='text-align:center;color:#64748b'>No frontend data collected</td></tr>"}</tbody>
</table>
</div>
<div style="margin-top:1rem;font-size:0.75rem;color:#64748b">
<span class="badge good">Good</span> · <span class="badge needs_improvement">Needs Improvement</span> · <span class="badge poor">Poor</span><br>
<strong>FCP</strong> &lt;1.8s · <strong>LCP</strong> &lt;2.5s · <strong>CLS</strong> &lt;0.1 · <strong>TTFB</strong> &lt;800ms · <strong>Load</strong> &lt;3s
</div>
</div>

<div class="section">
<h2>⚙️ Backend Performance</h2>
<div style="overflow-x:auto">
<table>
<thead><tr><th>Endpoint</th><th>Avg</th><th>Min</th><th>Max</th><th>Median</th><th>P75</th><th>P90</th><th>P95</th><th>P99</th><th>Err%</th><th>Success</th></tr></thead>
<tbody>{"".join(be_rows) or "<tr><td colspan=10 style='text-align:center;color:#64748b'>No backend data collected</td></tr>"}</tbody>
</table>
</div>
<div style="margin-top:1rem;font-size:0.75rem;color:#64748b">
<strong>Avg</strong> &lt;1s · <strong>P95</strong> &lt;2s · <strong>P99</strong> &lt;5s · <strong>Error%</strong> &lt;1%
</div>
</div>

<p class="timestamp">Generated by Performance Test Platform</p>
</div>
</body>
</html>'''
