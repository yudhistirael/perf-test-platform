#!/usr/bin/env python3
"""Reactive API Scanner — discovers ALL endpoints dynamically via browser intercept."""
import re
import time
from urllib.parse import urljoin, urlparse
from collections import defaultdict


class APIScanner:
    def __init__(self, base_url: str, token: str = None, email: str = None, password: str = None):
        self.base_url = base_url.rstrip('/')
        self.token = token
        self.email = email
        self.password = password
        self.endpoints = []
        self._seen = set()

    def _add(self, path, method='GET', source='auto', full_url=None, host=None):
        path = path.strip()
        if not path or len(path) > 500:
            return
        
        # Normalize path — ensure it starts with / and resolve relative paths
        if not path.startswith('/') and not path.startswith('http'):
            path = '/' + path
        
        # Skip wildcards and query string templates
        if '*' in path or '$' in path or '{' in path or '?' in path:
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
        # Skip phantom /api path with no sub-path
        if path == '/api' or path == '/api/':
            return
        
        # Track detected API host for BE tests
        if host and source in ('network-intercept', 'xhr', 'fetch'):
            if not hasattr(self, 'api_hosts'):
                self.api_hosts = defaultdict(int)
            self.api_hosts[host] += 1
        
        key = f"{method}:{full_url or path}"
        if key in self._seen:
            return
        self._seen.add(key)
        self.endpoints.append({'path': path, 'method': method.upper(), 'source': source, 'full_url': full_url, 'host': host})

    async def discover(self):
        """Full reactive discovery using headless browser + network intercept."""
        # PHASE 0: Detect tech stack FIRST
        await self._detect_stack()
        
        # PHASE 1-4: Full discovery
        await self._browser_scan()
        await self._try_openapi()
        await self._try_robots()
        await self._try_sitemap()
        
        # Build structured result with api_base_url + stack info
        api_base = getattr(self, 'api_base_url', None)
        stack = getattr(self, 'detected_stack', {})
        
        return {
            'endpoints': self.endpoints,
            'api_base_url': api_base,
            'stack': stack,
        }
    
    async def _detect_stack(self):
        """Detect frontend framework, backend, server, WAF, etc."""
        import httpx
        stack = {
            'frontend': None,
            'server': None,
            'waf': None,
            'cdn': None,
            'ui_framework': None,
        }
        
        try:
            async with httpx.AsyncClient(verify=False, timeout=httpx.Timeout(10)) as client:
                r = await client.head(self.base_url)
                headers = {k.lower(): v for k, v in r.headers.items()}
                
                # Detect server
                if 'server' in headers:
                    server = headers['server'].lower()
                    if 'nginx' in server:
                        stack['server'] = 'nginx'
                    elif 'apache' in server:
                        stack['server'] = 'apache'
                    elif 'microsoft-iis' in server:
                        stack['server'] = 'iis'
                
                # Detect WAF/CDN
                if 'x-cdn' in headers:
                    stack['cdn'] = headers['x-cdn']
                if 'imperva' in str(headers).lower():
                    stack['waf'] = 'Imperva'
                elif 'cloudflare' in str(headers).lower():
                    stack['waf'] = 'Cloudflare'
                
                # Get HTML to detect frontend
                r2 = await client.get(self.base_url)
                html = r2.text[:5000]
                
                # Detect React
                if '/assets/' in html and 'react' in html.lower():
                    stack['frontend'] = 'React'
                if 'vite.svg' in html or 'module' in html:
                    stack['build_tool'] = 'Vite'
                
                # Detect Vue
                if 'vue' in html.lower() and '__vue' in html:
                    stack['frontend'] = 'Vue'
                
                # Detect Angular
                if 'ng-app' in html or 'ng-version' in html:
                    stack['frontend'] = 'Angular'
                
                # Detect Svelte
                if 'svelte' in html.lower():
                    stack['frontend'] = 'Svelte'
                
                # Detect UI frameworks
                if 'flowbite' in html.lower():
                    stack['ui_framework'] = 'Flowbite'
                elif 'bootstrap' in html.lower():
                    stack['ui_framework'] = 'Bootstrap'
                elif 'tailwind' in html.lower() or 'tailwindcss' in html.lower():
                    stack['ui_framework'] = 'Tailwind'
                elif 'material' in html.lower():
                    stack['ui_framework'] = 'Material Design'
                
                # Detect backend / API type from env.js + headers
                try:
                    env_r = await client.get(urljoin(self.base_url, '/env.js'))
                    if env_r.status_code == 200:
                        env_txt = env_r.text
                        if 'graphql' in env_txt.lower():
                            stack['api_type'] = 'GraphQL'
                        elif '/gateway/' in env_txt or '/api/' in env_txt or '/v1' in env_txt or '/v2' in env_txt:
                            stack['api_type'] = 'REST'
                        # API gateway hint
                        if 'gateway' in env_txt.lower():
                            stack['backend'] = 'API Gateway'
                        if 'wso2' in env_txt.lower():
                            stack['backend'] = 'WSO2 API Gateway'
                except Exception:
                    pass
                
                # Powered-by header
                if 'x-powered-by' in headers:
                    stack['powered_by'] = headers['x-powered-by']
                # ASP.NET
                if 'x-aspnet-version' in headers or 'x-aspnetmvc-version' in headers:
                    stack['backend'] = 'ASP.NET'
        except Exception:
            pass
        
        self.detected_stack = stack
    
    async def _validate_endpoints(self):
        """Quick validation — skip API paths from js-bundle (they exist on different host)."""
        # Don't validate paths sourced from JS bundles — they're API paths on a different host
        # Only validate paths from network crawl, homepage, form, etc.
        skip_sources = {'js-bundle', 'js-api-call', 'env.js'}
        validate = [ep for ep in self.endpoints if ep['source'] not in skip_sources]
        keep = [ep for ep in self.endpoints if ep['source'] in skip_sources]
        
        # Quick validation on non-API paths
        import httpx
        valid = []
        async with httpx.AsyncClient(verify=False, timeout=httpx.Timeout(5)) as client:
            for ep in validate:
                try:
                    url = urljoin(self.base_url, ep['path'])
                    r = await client.head(url, follow_redirects=True)
                    if r.status_code != 404:
                        valid.append(ep)
                except Exception:
                    valid.append(ep)
        
        self.endpoints = keep + valid

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
                method = request.method
                path = parsed.path
                host = parsed.netloc
                
                # Capture EVERYTHING that looks like an API call
                # Look for: /api, /gateway, /graphql, /v1, /v2, /service, /endpoint, /rpc, etc
                is_api_like = any(x in path.lower() for x in ['/api', '/gateway', '/graphql', '/v1', '/v2', '/service', '/endpoint', '/rpc', '/inventory'])
                
                # OR if it's to a different host (API server)
                is_different_host = host != base_netloc
                
                if is_api_like or is_different_host:
                    captured_urls.append({
                        'url': url,
                        'path': path,
                        'method': method,
                        'source': 'network-intercept',
                        'host': host
                    })
            
            page.on('request', on_request)

            # Step 1: Load main page
            try:
                resp = await page.goto(self.base_url, timeout=30000, wait_until='domcontentloaded')
                if resp:
                    self._add(urlparse(self.base_url).path or '/', 'GET', 'homepage')
            except Exception:
                pass

            # Step 1b: LOGIN FIRST if credentials provided
            if self.email and self.password:
                try:
                    email_input = await page.query_selector("input[type=email], input[placeholder*=email], input[name*=email], input[name*=user]")
                    pass_input = await page.query_selector("input[type=password], input[name*=password]")
                    
                    if email_input and pass_input:
                        await email_input.fill(self.email)
                        await pass_input.fill(self.password)
                        
                        # Find and click submit button
                        submit = await page.query_selector("button[type=submit], button:has-text('Login'), button:has-text('Sign in'), button:has-text('login')")
                        if submit:
                            await submit.click()
                        else:
                            await page.keyboard.press("Enter")
                        
                        await page.wait_for_timeout(4000)
                except Exception as e:
                    pass

            # Step 2: Extract endpoints from page source (HTML)
            await self._extract_from_html(page)

            # Step 3: Extract endpoints from JS (inline + linked)
            await self._extract_from_js(page)
            
            # Step 3b: Extract React Router paths from ALL captured JS bundles
            await self._extract_react_routes(captured_urls)
            
            # Step 3c: Parse env.js for API base URL
            await self._parse_env_js()

            # Step 4: Crawl all internal links (depth 2) — THIS TRIGGERS MORE API CALLS
            await self._crawl_links(page, base_netloc, depth=2)

            # Step 5: Add all captured network requests (API calls to any host)
            for cap in captured_urls:
                self._add(cap['path'], cap['method'], 'network-intercept', full_url=cap['url'], host=cap['host'])

            # Auto-detect API host from captured requests — most-called non-base host
            if hasattr(self, 'api_hosts') and self.api_hosts:
                api_host = max(self.api_hosts, key=self.api_hosts.get)
                parsed_base = urlparse(self.base_url)
                if api_host != parsed_base.netloc:
                    self.api_base_url = f"{parsed_base.scheme}://{api_host}"

            # Step 6: Trigger XHR/API calls by interacting with page
            await self._trigger_interactions(page, captured_urls, base_netloc)

            await browser.close()

    async def _extract_react_routes(self, captured_urls):
        """Extract React Router paths from ALL captured JS bundles."""
        import httpx
        base_netloc = urlparse(self.base_url).netloc
        
        # Collect ALL unique JS URLs from network captures
        js_urls = set()
        for cap in captured_urls:
            parsed = urlparse(cap['url'])
            if parsed.path.endswith('.js') and (parsed.netloc == base_netloc or not parsed.netloc):
                js_urls.add(cap['url'])
        
        # Also get JS from DOM
        try:
            dom_scripts = await self._get_dom_scripts  # will be set by caller
        except:
            pass
        
        async with httpx.AsyncClient(verify=False, timeout=httpx.Timeout(15)) as client:
            for js_url in list(js_urls)[:30]:
                try:
                    r = await client.get(js_url)
                    if r.status_code != 200:
                        continue
                    content = r.text
                    
                    # React Router path definitions: path:"/xxx" or path: '/xxx'
                    routes = re.findall(r'path\s*:\s*["\']([^"\']+)["\']', content, re.IGNORECASE)
                    for route in routes:
                        if route.startswith('/') and not route.startswith('/http'):
                            # Skip dynamic params like /:id but keep the base
                            clean = re.sub(r'/:[^/]+', '', route)
                            if clean and clean != '/':
                                self._add(clean, 'GET', 'react-router')
                    
                    # Also extract fetch/axios API calls: fetch("/api/xxx"), axios.get("/api/xxx")
                    api_calls = re.findall(r'(?:fetch|axios|\.get|\.post|\.put|\.delete|\.patch)\s*\(\s*["`\']([^"`\']+)["`\']', content, re.IGNORECASE)
                    for api in api_calls:
                        if api.startswith('/') or api.startswith('http'):
                            self._add(api, 'GET', 'js-api-call')
                except Exception:
                    continue
    
    async def _parse_env_js(self):
        """Parse env.js for API base URL and extract its paths."""
        import httpx
        try:
            async with httpx.AsyncClient(verify=False, timeout=httpx.Timeout(10)) as client:
                r = await client.get(urljoin(self.base_url, '/env.js'))
                if r.status_code != 200:
                    return
                
                content = r.text
                # Extract REACT_APP_API_URL value
                api_match = re.search(r'REACT_APP_API_URL["\']?\s*:\s*["\']([^"\']+)', content)
                if api_match:
                    api_url = api_match.group(1)
                    parsed = urlparse(api_url)
                    # Store API base URL for BE testing
                    if not hasattr(self, 'api_base_url'):
                        self.api_base_url = api_url
                    # Add the API base path
                    if parsed.path:
                        self._add(parsed.path, 'GET', 'env.js')
                
                # Extract all REACT_APP_* URLs
                all_urls = re.findall(r'REACT_APP_\w*URL\w*["\']?\s*:\s*["\']([^"\']+)', content)
                for url in all_urls:
                    parsed = urlparse(url)
                    if parsed.path and parsed.netloc:
                        self._add(parsed.path, 'GET', 'env.js')
        except Exception:
            pass

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
