#!/usr/bin/env python3
"""Reactive API Scanner — discovers ALL endpoints dynamically via browser intercept."""
import re
import time
from urllib.parse import urljoin, urlparse
from collections import defaultdict


class APIScanner:
    def __init__(self, base_url: str, token: str = None):
        self.base_url = base_url.rstrip('/')
        self.token = token
        self.endpoints = []
        self._seen = set()

    def _add(self, path, method='GET', source='auto'):
        path = path.strip()
        if not path or len(path) > 500:
            return
        # Skip wildcards and query string templates
        if '*' in path or '$' in path or '{' in path:
            return
        # Skip Cloudflare & CDN challenge paths
        if any(x in path for x in ('/cdn-cgi/', '/cf-', '/__cf', '/challenge-platform', '/turnstile')):
            return
        # Skip static assets
        filename = path.split('/')[-1]
        ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
        if ext in ('js','css','png','jpg','jpeg','gif','svg','ico','woff','woff2','ttf','eot','map','pdf','mp4','mp3','zip','webp','avif'):
            return
        # Skip extremely long paths (likely tokens/hashes)
        if len(path) > 200:
            return
        key = f"{method}:{path}"
        if key in self._seen:
            return
        self._seen.add(key)
        self.endpoints.append({'path': path, 'method': method.upper(), 'source': source})

    async def discover(self):
        """Full reactive discovery using headless browser + network intercept."""
        await self._browser_scan()
        await self._try_openapi()
        await self._try_robots()
        await self._try_sitemap()
        return self.endpoints

    async def _browser_scan(self):
        """Open page in Playwright, intercept ALL network requests, crawl all links/forms."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return

        base_netloc = urlparse(self.base_url).netloc

        async with async_playwright() as p:
            try:
                browser = await p.chromium.launch(
                    headless=True,
                    executable_path='/usr/bin/google-chrome',
                    args=['--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage']
                )
            except Exception:
                return

            context = await browser.new_context(
                viewport={'width': 1366, 'height': 768},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36'
            )
            page = await context.new_page()

            # Intercept ALL network requests — ANY domain, ANY path
            captured_urls = []
            def on_request(request):
                url = request.url
                parsed = urlparse(url)
                if parsed.netloc == base_netloc or parsed.netloc == '' or parsed.netloc.startswith('api.') or parsed.netloc.startswith(base_netloc.split('.')[0]):
                    captured_urls.append({'url': url, 'method': request.method, 'source': 'network'})
            
            page.on('request', on_request)

            # Step 1: Load main page
            try:
                resp = await page.goto(self.base_url, timeout=30000, wait_until='domcontentloaded')
                if resp:
                    self._add(urlparse(self.base_url).path or '/', 'GET', 'homepage')
            except Exception:
                pass

            # Step 2: Extract endpoints from page source (HTML)
            await self._extract_from_html(page)

            # Step 3: Extract endpoints from JS (inline + linked)
            await self._extract_from_js(page)

            # Step 4: Crawl all internal links (depth 2)
            await self._crawl_links(page, base_netloc, depth=2)

            # Step 5: Add all captured network requests
            for cap in captured_urls:
                parsed = urlparse(cap['url'])
                if parsed.netloc == base_netloc:
                    self._add(parsed.path, cap['method'], cap['source'])

            # Step 6: Trigger XHR/API calls by interacting with page
            await self._trigger_interactions(page, captured_urls, base_netloc)

            await browser.close()

    async def _extract_from_html(self, page):
        """Extract ALL endpoints from HTML: forms, data attributes, href, action, etc."""
        try:
            html = await page.content()
        except Exception:
            return

        # Form actions
        forms = re.findall(r'<form[^>]*action=["\']([^"\']+)["\']', html, re.IGNORECASE)
        for f in forms:
            self._add(f, 'POST', 'form')

        # data-url, data-href, data-api, data-endpoint, data-source, data-src, data-fetch, etc.
        data_attrs = re.findall(r'data-(?:url|href|api|endpoint|source|src|fetch|action|link|page|route|load)[="\'\s]+["\']?([^"\'\s>]+)', html, re.IGNORECASE)
        for d in data_attrs:
            if d.startswith('/'):
                self._add(d, 'GET', 'data-attr')

        # AJAX/endpoint patterns in inline scripts
        api_patterns = re.findall(r'(?:fetch|axios|ajax|post|get|put|delete|patch|request|call)\s*\(\s*["`\']([^"\'`\s]+)["`\']', html, re.IGNORECASE)
        for a in api_patterns:
            if a.startswith('/') or a.startswith('http'):
                self._add(a, 'GET', 'inline-script')

        # JSON config objects
        json_urls = re.findall(r'["\'](?:url|endpoint|api|base_url|baseUrl)["\']?\s*:\s*["\']([^"\']+)["\']', html, re.IGNORECASE)
        for j in json_urls:
            if j.startswith('/'):
                self._add(j, 'GET', 'config')

    async def _extract_from_js(self, page):
        """Fetch and parse all JS files for endpoint patterns."""
        try:
            js_urls = await page.eval_on_selector_all('script[src]', 'els => els.map(e => e.src)')
        except Exception:
            js_urls = []

        import httpx
        async with httpx.AsyncClient(verify=False, timeout=httpx.Timeout(10)) as client:
            for js_url in js_urls[:20]:  # Cap at 20 JS files
                try:
                    r = await client.get(js_url)
                    if r.status_code != 200:
                        continue
                    content = r.text
                    # Find ALL URL-like strings
                    url_patterns = re.findall(r'["\'`]((?:/|https?://)[a-zA-Z0-9_/.-]{3,})["\'`]', content)
                    # Also find route definitions (React Router, Vue Router, etc.)
                    routes = re.findall(r'path\s*:\s*["\']([^"\']+)["\']', content, re.IGNORECASE)
                    for u in url_patterns + routes:
                        if u.startswith('http'):
                            parsed = urlparse(u)
                            if parsed.netloc == urlparse(self.base_url).netloc:
                                self._add(parsed.path, 'GET', 'js-bundle')
                        elif u.startswith('/'):
                            self._add(u, 'GET', 'js-bundle')
                except Exception:
                    continue

    async def _crawl_links(self, page, base_netloc, depth=2):
        """Crawl internal links to discover more endpoints."""
        if depth <= 0:
            return
        try:
            links = await page.eval_on_selector_all('a[href]', 'els => els.map(e => e.getAttribute("href"))')
        except Exception:
            links = []

        internal = set()
        for href in links:
            if not href:
                continue
            parsed = urlparse(urljoin(self.base_url, href))
            if parsed.netloc == base_netloc and parsed.path:
                internal.add(parsed.path)

        # Visit up to 10 internal links
        for path in list(internal)[:10]:
            url = urljoin(self.base_url, path)
            try:
                resp = await page.goto(url, timeout=20000, wait_until='domcontentloaded')
                if resp and resp.status < 400:
                    self._add(path, 'GET', 'crawl')
                    # Extract more endpoints from this page
                    await self._extract_from_html(page)
            except Exception:
                continue

    async def _trigger_interactions(self, page, captured_urls, base_netloc):
        """Click buttons, fill forms, trigger XHR to discover hidden endpoints."""
        try:
            await page.goto(self.base_url, timeout=20000, wait_until='domcontentloaded')
        except Exception:
            return

        # Click buttons/links that might trigger AJAX
        buttons = await page.query_selector_all('button, [role="button"], [onclick], [data-toggle], [data-action]')
        for btn in buttons[:15]:
            try:
                before_count = len(captured_urls)
                await btn.click(timeout=3000)
                await page.wait_for_timeout(1000)
                # Any new network requests triggered?
                for cap in captured_urls[before_count:]:
                    parsed = urlparse(cap['url'])
                    if parsed.netloc == base_netloc:
                        self._add(parsed.path, cap['method'], 'interaction')
            except Exception:
                continue

    async def _try_openapi(self):
        """Try OpenAPI/Swagger specs."""
        import httpx
        paths = ['/openapi.json', '/openapi/v1.json', '/openapi/v2.json', '/api-docs', '/api/v1/spec', '/swagger.json', '/v1/openapi.json', '/v2/openapi.json']
        async with httpx.AsyncClient(verify=False, timeout=httpx.Timeout(10)) as client:
            for path in paths:
                try:
                    r = await client.get(urljoin(self.base_url, path))
                    if r.status_code == 200:
                        data = r.json()
                        if isinstance(data, dict) and 'paths' in data:
                            for p, methods in data['paths'].items():
                                for m in methods:
                                    if m in ('get','post','put','delete','patch'):
                                        self._add(p, m.upper(), 'openapi')
                except Exception:
                    continue

    async def _try_robots(self):
        """Extract endpoints from robots.txt."""
        import httpx
        try:
            async with httpx.AsyncClient(verify=False, timeout=httpx.Timeout(10)) as client:
                r = await client.get(urljoin(self.base_url, '/robots.txt'))
                if r.status_code == 200:
                    paths = re.findall(r'(?:Allow|Disallow)\s*:\s*(/\S+)', r.text)
                    for p in paths:
                        self._add(p, 'GET', 'robots.txt')
        except Exception:
            pass

    async def _try_sitemap(self):
        """Extract endpoints from sitemap.xml."""
        import httpx
        try:
            async with httpx.AsyncClient(verify=False, timeout=httpx.Timeout(10)) as client:
                r = await client.get(urljoin(self.base_url, '/sitemap.xml'))
                if r.status_code == 200:
                    urls = re.findall(r'<loc>(.*?)</loc>', r.text)
                    for u in urls[:50]:
                        parsed = urlparse(u)
                        if parsed.netloc == urlparse(self.base_url).netloc:
                            self._add(parsed.path, 'GET', 'sitemap')
        except Exception:
            pass
