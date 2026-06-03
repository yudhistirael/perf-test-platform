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

    async def _test_endpoint(self, path: str, method: str = 'GET', num_requests: int = 15):
        """Test endpoint with configurable request count."""
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

        async with httpx.AsyncClient(verify=False, timeout=httpx.Timeout(30)) as client:
            for _ in range(num_requests):
                start = time.perf_counter()
                try:
                    url = urljoin(self.base_url, path)
                    
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
                    result['latencies'].append(latency)
                    
                    # Track status codes
                    status = r.status_code
                    result['status_codes'][status] = result['status_codes'].get(status, 0) + 1
                    
                    if status < 400:
                        result['success'] += 1
                    else:
                        result['errors'] += 1

                except asyncio.TimeoutError:
                    result['latencies'].append(30000)
                    result['errors'] += 1
                except Exception as e:
                    result['latencies'].append(30000)
                    result['errors'] += 1

        # Calculate statistics
        if result['latencies']:
            result['avg_latency_ms'] = round(mean(result['latencies']), 2)
            result['min_latency_ms'] = round(min(result['latencies']), 2)
            result['max_latency_ms'] = round(max(result['latencies']), 2)
            result['median_latency_ms'] = round(median(result['latencies']), 2)
            
            if len(result['latencies']) > 1:
                result['stdev_latency_ms'] = round(stdev(result['latencies']), 2)
            
            sorted_latencies = sorted(result['latencies'])
            result['p50_latency_ms'] = round(sorted_latencies[int(len(sorted_latencies) * 0.50)], 2)
            result['p75_latency_ms'] = round(sorted_latencies[int(len(sorted_latencies) * 0.75)], 2)
            result['p90_latency_ms'] = round(sorted_latencies[int(len(sorted_latencies) * 0.90)], 2)
            result['p95_latency_ms'] = round(sorted_latencies[int(len(sorted_latencies) * 0.95)], 2)
            result['p99_latency_ms'] = round(sorted_latencies[int(len(sorted_latencies) * 0.99)], 2)

        result['error_rate'] = round((result['errors'] / num_requests) * 100, 2)
        result['total_requests'] = num_requests
        result['latencies'] = []  # Don't include raw list in results

        return result
