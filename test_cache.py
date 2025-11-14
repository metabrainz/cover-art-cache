#!/usr/bin/env python3
"""
Cache Load Test

Sequential test of 20k release MBIDs to populate cache and test cleaner service.
"""

import os
import re
import time
import requests
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Configuration
VIRTUAL_HOST = os.environ.get('VIRTUAL_HOST', 'localhost')
BASE_URL = f"https://{VIRTUAL_HOST}"
MBID_FILE = "release_mbids.txt"

def load_mbids():
    """Load and parse MBIDs from release_mbids.txt file."""
    mbids = []
    
    mbid_file = Path(MBID_FILE)
    if not mbid_file.exists():
        raise FileNotFoundError(f"MBID file not found: {MBID_FILE}")
    
    with open(mbid_file, 'r') as f:
        for line in f:
            line = line.strip()
            
            # Skip empty lines and comments
            if not line or line.startswith('#'):
                continue
            
            # Remove quotes if present and strip whitespace
            mbid = line.strip('"\'').strip()
            mbids.append(mbid)
    
    return mbids

def test_sequential_requests():
    """Request each MBID sequentially and report progress."""
    mbids = load_mbids()
    print(f"Loaded {len(mbids)} MBIDs")
    print(f"Testing against {BASE_URL}")
    print(f"Starting sequential requests...\n")
    
    success_count = 0
    error_count = 0
    not_found_count = 0
    start_time = time.time()
    
    for i, mbid in enumerate(mbids, 1):
        url = f"{BASE_URL}/release/{mbid}/front"
        
        try:
            response = requests.get(url, timeout=30)
            
            if response.status_code == 200:
                success_count += 1
                status = "✓"
            elif response.status_code == 404:
                not_found_count += 1
                status = "✗ 404"
            else:
                error_count += 1
                status = f"✗ {response.status_code}"
                
        except Exception as e:
            error_count += 1
            status = f"✗ ERROR: {e}"
        
        # Progress report every 100 requests
        if i % 100 == 0:
            elapsed = time.time() - start_time
            rate = i / elapsed if elapsed > 0 else 0
            print(f"Progress: {i}/{len(mbids)} ({i*100/len(mbids):.1f}%) - "
                  f"Success: {success_count}, 404: {not_found_count}, Errors: {error_count} - "
                  f"Rate: {rate:.1f} req/sec")
        
    # Final report
    elapsed = time.time() - start_time
    print(f"\n{'='*70}")
    print(f"Test complete!")
    print(f"Total requests: {len(mbids)}")
    print(f"Successful: {success_count}")
    print(f"Not found (404): {not_found_count}")
    print(f"Errors: {error_count}")
    print(f"Time elapsed: {elapsed:.1f} seconds")
    print(f"Average rate: {len(mbids)/elapsed:.1f} requests/second")
    print(f"{'='*70}")

if __name__ == "__main__":
    test_sequential_requests()
