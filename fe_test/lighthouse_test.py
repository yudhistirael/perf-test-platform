#!/usr/bin/env python3
"""Frontend Performance Test — Playwright real browser metrics."""
import time
from urllib.parse import urljoin, urlparse


class LighthouseTest:
    def __init__(self, base_url: str, email: str = None, password: str = None, pre_discovered_pages: list = None):
        self.base_url = base_url.rstrip('/')
        self.email = email
        self.password = password
        self.pre_discovered_pages = pre_discovered_pages or []

    async def run(self):
        """Run frontend performance tests on discovered pages."""
        results = {}
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return {'_error': 'Playwright not installed'}

        async with async_playwright() as p:
            try:
                browser = await p.chromium.launch(
                    headless=True,
                    executable_path='/usr/bin/google-chrome',
                    args=['--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage']
                )
            except Exception as e:
                return {'_error': f'Browser launch failed: {e}'}

            context = await browser.new_context(
                viewport={'width': 1366, 'height': 768},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36'
            )
            page = await context.new_page()

            # Login if credentials provided
            if self.email and self.password:
                await self._login(page)

            # Use pre-discovered pages from scanner (if available)
            if self.pre_discovered_pages:
                pages_to_test = self.pre_discovered_pages
            else:
                pages_to_test = await self._discover_pages(page)

            # Test each page
            for page_info in pages_to_test:
                url = urljoin(self.base_url + '/', page_info['path'].lstrip('/'))
                try:
                    metrics = await self._test_page(page, url, page_info['name'])
                    results[page_info['path']] = metrics
                except Exception as e:
                    results[page_info['path']] = {'name': page_info['name'], 'url': url, 'error': str(e)}

            await browser.close()

        return results

    async def _login(self, page):
        """Attempt login via discovered login form."""
        login_paths = ['/login', '/signin', '/auth/login', '/account/login']
        for path in login_paths:
            url = urljoin(self.base_url + '/', path.lstrip('/'))
            try:
                resp = await page.goto(url, timeout=20000, wait_until='domcontentloaded')
                if not resp or resp.status >= 400:
                    continue
                email_input = await page.query_selector(
                    'input[type="email"],input[name="email"],input[name="username"],input[placeholder*="email" i]'
                )
                pass_input = await page.query_selector('input[type="password"],input[name="password"]')
                if email_input and pass_input:
                    await email_input.fill(self.email)
                    await pass_input.fill(self.password)
                    submit = await page.query_selector(
                        'button[type="submit"],input[type="submit"],button:has-text("Login"),button:has-text("Sign in")'
                    )
                    if submit:
                        await submit.click()
                        try:
                            await page.wait_for_load_state('networkidle', timeout=10000)
                        except Exception:
                            pass
                        return True
            except Exception:
                continue
        return False

    async def _discover_pages(self, page):
        """Find pages via sitemap or in-page navigation links."""
        pages = [{'path': '/', 'name': 'Homepage'}]
        seen = {'/'}

        # Try sitemap.xml
        try:
            resp = await page.goto(urljoin(self.base_url, '/sitemap.xml'), timeout=10000)
            if resp and resp.status == 200:
                import re
                content = await page.content()
                urls = re.findall(r'<loc>(.*?)</loc>', content)
                for u in urls[:12]:
                    path = urlparse(u).path
                    if path and path not in seen:
                        seen.add(path)
                        name = path.strip('/').split('/')[-1].replace('-', ' ').title() or 'Page'
                        pages.append({'path': path, 'name': name})
        except Exception:
            pass

        # Fallback: crawl nav links from homepage
        if len(pages) <= 1:
            try:
                await page.goto(self.base_url, timeout=20000, wait_until='domcontentloaded')
                links = await page.eval_on_selector_all(
                    'a[href]',
                    '''els => els.map(e => e.getAttribute('href')).filter(h => h)'''
                )
                base_netloc = urlparse(self.base_url).netloc
                for href in links:
                    parsed = urlparse(urljoin(self.base_url, href))
                    if parsed.netloc and parsed.netloc != base_netloc:
                        continue
                    path = parsed.path
                    if path and path not in seen and not path.endswith(('.png', '.jpg', '.css', '.js', '.svg', '.ico')):
                        seen.add(path)
                        name = path.strip('/').split('/')[-1].replace('-', ' ').title() or 'Page'
                        pages.append({'path': path, 'name': name})
                    if len(pages) >= 10:
                        break
            except Exception:
                pass

        return pages

    async def _test_page(self, page, url, name):
        """Measure real performance metrics for one page."""
        metrics = {
            'name': name, 'url': url,
            'load_time_ms': 0, 'dom_ready_ms': 0, 'ttfb_ms': 0,
            'lcp_ms': 0, 'cls': 0, 'fcp_ms': 0,
            'total_requests': 0, 'total_size_kb': 0, 'errors': 0,
            'status_code': 0, 'scores': {},
        }

        def on_response(response):
            metrics['total_requests'] += 1

        def on_request_failed(request):
            metrics['errors'] += 1

        page.on('response', on_response)
        page.on('requestfailed', on_request_failed)

        start = time.perf_counter()
        try:
            resp = await page.goto(url, wait_until='load', timeout=30000)
            metrics['load_time_ms'] = round((time.perf_counter() - start) * 1000, 2)
            if resp:
                metrics['status_code'] = resp.status

            perf_data = await page.evaluate('''() => {
                const nav = performance.getEntriesByType('navigation')[0] || {};
                const paint = performance.getEntriesByType('paint');
                const fcp = paint.find(e => e.name === 'first-contentful-paint');
                const resources = performance.getEntriesByType('resource');
                let totalSize = (nav.transferSize || 0);
                for (const r of resources) totalSize += (r.transferSize || 0);
                return {
                    dom_ready: nav.domContentLoadedEventEnd || 0,
                    ttfb: nav.responseStart || 0,
                    fcp: fcp ? fcp.startTime : 0,
                    total_size: totalSize,
                };
            }''')
            metrics['dom_ready_ms'] = round(perf_data.get('dom_ready', 0), 2)
            metrics['ttfb_ms'] = round(perf_data.get('ttfb', 0), 2)
            metrics['fcp_ms'] = round(perf_data.get('fcp', 0), 2)
            metrics['total_size_kb'] = round(perf_data.get('total_size', 0) / 1024, 2)

            # LCP
            lcp = await page.evaluate('''() => new Promise(resolve => {
                new PerformanceObserver(list => {
                    const entries = list.getEntries();
                    resolve(entries.length ? entries[entries.length-1].startTime : 0);
                }).observe({type:'largest-contentful-paint', buffered:true});
                setTimeout(() => resolve(0), 3000);
            })''')
            metrics['lcp_ms'] = round(lcp, 2) if isinstance(lcp, (int, float)) else 0

            # CLS
            cls = await page.evaluate('''() => new Promise(resolve => {
                let cls = 0;
                new PerformanceObserver(list => {
                    for (const e of list.getEntries()) if (!e.hadRecentInput) cls += e.value;
                    resolve(cls);
                }).observe({type:'layout-shift', buffered:true});
                setTimeout(() => resolve(cls), 2000);
            })''')
            metrics['cls'] = round(cls, 4) if isinstance(cls, (int, float)) else 0

        except Exception as e:
            metrics['error'] = str(e)

        page.remove_listener('response', on_response)
        page.remove_listener('requestfailed', on_request_failed)

        metrics['scores'] = {
            'fcp': self._score(metrics['fcp_ms'], 1800, 3000),
            'lcp': self._score(metrics['lcp_ms'], 2500, 4000),
            'cls': self._score_cls(metrics['cls']),
            'load': self._score(metrics['load_time_ms'], 3000, 5000),
            'ttfb': self._score(metrics['ttfb_ms'], 800, 1800),
        }
        return metrics

    @staticmethod
    def _score(ms, good, ok):
        if ms <= 0: return 'unknown'
        if ms < good: return 'good'
        if ms < ok: return 'needs_improvement'
        return 'poor'

    @staticmethod
    def _score_cls(cls):
        if cls < 0.1: return 'good'
        if cls < 0.25: return 'needs_improvement'
        return 'poor'
