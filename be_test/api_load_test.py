#!/usr/bin/env python3
"""Backend Performance Test — Dynamic API load testing."""
import asyncio
import time
import httpx
from statistics import mean, stdev, median
from urllib.parse import urljoin
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scanner.api_scanner import APIScanner


class APILoadTest:
    def __init__(self, base_url: str, email: str = None, password: str = None, max_endpoints: int = 15, requests_per_endpoint: int = 15):
        self.base_url = base_url.rstrip('/')
        self.email = email
        self.password = password
        self.max_endpoints = max_endpoints
        self.requests_per_endpoint = requests_per_endpoint

        self.token = None
        self.endpoints = []

    async def run(self):
        """Run load tests on discovered endpoints."""
        # Step 1: Discover endpoints dynamically
        scanner = APIScanner(self.base_url)
        self.endpoints = await scanner.discover()
        
        if not self.endpoints:
            return {}

        # Step 2: Authenticate if credentials provided
        if self.email and self.password:
            await self._authenticate()

        # Step 3: Test each endpoint
        results = {}
        for i, ep in enumerate(self.endpoints[:self.max_endpoints]):
            result = await self._test_endpoint(ep['path'], ep['method'], self.requests_per_endpoint)
            results[f"{ep['method']} {ep['path']}"] = result

        print(f"    [✓] Load tested {len(results)} endpoints")
        return results

    async def _authenticate(self):
        """Authenticate and get token."""
        login_endpoints = [
            {'path': '/api/auth/login', 'method': 'POST'},
            {'path': '/api/login', 'method': 'POST'},
            {'path': '/auth/login', 'method': 'POST'},
            {'path': '/login', 'method': 'POST'},
        ]
        
        async with httpx.AsyncClient(verify=False, timeout=httpx.Timeout(15)) as client:
            for ep in login_endpoints:
                try:
                    url = urljoin(self.base_url, ep['path'])
                    payload = {'email': self.email, 'password': self.password}
                    r = await client.post(url, json=payload)
                    
                    if r.status_code in (200, 201):
                        data = r.json()
                        for key in ('token', 'access_token', 'accessToken', 'jwt', 'data'):
                            if key in data:
                                if isinstance(data[key], dict) and 'token' in data[key]:
                                    self.token = data[key]['token']
                                elif isinstance(data[key], str):
                                    self.token = data[key]
                                if self.token:
                                    return
                except Exception:
                    continue

    async def _test_endpoint(self, path: str, method: str = 'GET', num_requests: int = 15, concurrent_users: int = 1):
        """Test endpoint with configurable concurrency and request count."""
        result = {
            'path': path,
            'method': method,
            'latencies': [],
            'errors': 0,
            'success': 0,
            'status_codes': {},
        }

        headers = {}
        if self.token:
            headers['Authorization'] = f'Bearer {self.token}'

        semaphore = asyncio.Semaphore(concurrent_users)

        async def single_request(client):
            async with semaphore:
                start = time.perf_counter()
                try:
                    # Fix: ensure path appends to base_url, not replaces it
                    # If path starts with /, remove it so it appends
                    clean_path = path.lstrip('/') if path.startswith('/') else path
                    url = self.base_url.rstrip('/') + '/' + clean_path
                    if method == 'GET':
                        r = await client.get(url, headers=headers)
                    elif method == 'POST':
                        r = await client.post(url, headers=headers, json={})
                    elif method == 'PUT':
                        r = await client.put(url, headers=headers, json={})
                    elif method == 'DELETE':
                        r = await client.delete(url, headers=headers)
                    elif method == 'PATCH':
                        r = await client.patch(url, headers=headers, json={})
                    else:
                        r = await client.request(method, url, headers=headers)
                    latency = (time.perf_counter() - start) * 1000
                    return latency, r.status_code, None
                except asyncio.TimeoutError:
                    return 10000, None, 'timeout'
                except Exception as e:
                    return 10000, None, str(e)

        async with httpx.AsyncClient(verify=False, timeout=httpx.Timeout(10)) as client:
            tasks = [single_request(client) for _ in range(num_requests)]
            responses = await asyncio.gather(*tasks)

        for latency, status, err in responses:
            result['latencies'].append(latency)
            if err:
                result['errors'] += 1
            else:
                result['status_codes'][status] = result['status_codes'].get(status, 0) + 1
                if status < 400:
                    result['success'] += 1
                else:
                    result['errors'] += 1

        if result['latencies']:
            result['avg_latency_ms'] = round(mean(result['latencies']), 2)
            result['min_latency_ms'] = round(min(result['latencies']), 2)
            result['max_latency_ms'] = round(max(result['latencies']), 2)
            result['median_latency_ms'] = round(median(result['latencies']), 2)
            if len(result['latencies']) > 1:
                result['stdev_latency_ms'] = round(stdev(result['latencies']), 2)
            sorted_l = sorted(result['latencies'])
            n = len(sorted_l)
            result['p50_latency_ms'] = round(sorted_l[int(n * 0.50)], 2)
            result['p75_latency_ms'] = round(sorted_l[int(n * 0.75)], 2)
            result['p90_latency_ms'] = round(sorted_l[int(n * 0.90)], 2)
            result['p95_latency_ms'] = round(sorted_l[int(n * 0.95)], 2)
            result['p99_latency_ms'] = round(sorted_l[min(int(n * 0.99), n-1)], 2)

        result['error_rate'] = round((result['errors'] / num_requests) * 100, 2)
        result['total_requests'] = num_requests
        result['concurrent_users'] = concurrent_users
        result['throughput_rps'] = round(num_requests / (sum(result['latencies']) / 1000 / concurrent_users), 2) if result['latencies'] else 0
        result['latencies'] = []
        return result
