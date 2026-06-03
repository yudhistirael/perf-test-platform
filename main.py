#!/usr/bin/env python3
import asyncio
import sys
import json
from datetime import datetime
from pathlib import Path
from scanner.api_scanner import APIScanner
from fe_test.lighthouse_test import LighthouseTest
from be_test.api_load_test import APILoadTest
from reports.report_generator import ReportGenerator

class PerformanceTestPlatform:
    def __init__(self, base_url: str, email: str, password: str):
        self.base_url = base_url
        self.email = email
        self.password = password
        self.results = {}
        self.endpoints = []
        
    async def run(self):
        print(f"[*] Starting Performance Test Platform")
        print(f"[*] Target: {self.base_url}")
        print(f"[*] Time: {datetime.now().isoformat()}\n")
        
        # Step 1: Scan APIs
        print("[1/4] Scanning API endpoints...")
        scanner = APIScanner(self.base_url, self.email, self.password)
        self.endpoints = await scanner.discover_endpoints()
        print(f"[✓] Found {len(self.endpoints)} endpoints\n")
        
        # Step 2: Run FE tests (Lighthouse)
        print("[2/4] Running Frontend Performance Tests...")
        fe_test = LighthouseTest(self.base_url, self.email, self.password)
        fe_results = await fe_test.run()
        self.results['frontend'] = fe_results
        print(f"[✓] Frontend tests complete\n")
        
        # Step 3: Run BE tests (API Load)
        print("[3/4] Running Backend Performance Tests...")
        be_test = APILoadTest(self.base_url, self.email, self.password, self.endpoints)
        be_results = await be_test.run()
        self.results['backend'] = be_results
        print(f"[✓] Backend tests complete\n")
        
        # Step 4: Generate report
        print("[4/4] Generating Report...")
        generator = ReportGenerator(self.base_url, self.results)
        report_path = generator.generate()
        print(f"[✓] Report saved to: {report_path}\n")
        
        print("[✓] All tests complete!")
        return report_path

async def main():
    if len(sys.argv) < 4:
        print("Usage: python main.py <base_url> <email> <password>")
        print("Example: python main.py http://localhost:8080 user@example.com password123")
        sys.exit(1)
    
    base_url = sys.argv[1]
    email = sys.argv[2]
    password = sys.argv[3]
    
    platform = PerformanceTestPlatform(base_url, email, password)
    report_path = await platform.run()
    
    print(f"\n📊 Open report in browser: {Path(report_path).as_uri()}")

if __name__ == "__main__":
    asyncio.run(main())
