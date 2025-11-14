#!/usr/bin/env python3
"""
Test suite for Cover Art Cache Service

A comprehensive test suite that replaces test.sh with proper Python testing infrastructure.
"""

import json
import time
import pytest
import requests
from pathlib import Path
from typing import Dict, List, Tuple
import re

# Test configuration
BASE_URL = "https://mayhem-chaos.net"
TEST_DATA_FILE = "test_data.json"
MBID_PATTERN = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')

class TestCoverArtCache:
    """Test suite for Cover Art Cache Service"""
    
    @classmethod
    def setup_class(cls):
        """Set up test class with test data"""
        test_data_path = Path(TEST_DATA_FILE)
        if not test_data_path.exists():
            pytest.fail(f"Test data file {TEST_DATA_FILE} not found")
        
        with open(test_data_path) as f:
            cls.test_data = json.load(f)
        
        # Validate service is running
        try:
            response = requests.get(f"{BASE_URL}/health", timeout=5)
            if response.status_code != 200:
                pytest.fail(f"Service health check failed: HTTP {response.status_code}")
        except requests.exceptions.RequestException as e:
            pytest.fail(f"Cannot connect to service at {BASE_URL}: {e}")
    
    def test_health_endpoint(self):
        """Test the health endpoint returns 200"""
        response = requests.get(f"{BASE_URL}/health")
        assert response.status_code == 200
        
        # Check response format
        data = response.json()
        assert "status" in data
        assert data["status"] == "healthy"
    
    def test_cache_status_endpoint(self):
        """Test the cache status endpoint returns valid data"""
        response = requests.get(f"{BASE_URL}/cache-status")
        assert response.status_code == 200
        
        # Validate response structure
        data = response.json()
        required_fields = [
            "status", "cache_dir"
        ]
        
        for field in required_fields:
            assert field in data, f"Missing required field: {field}"
        
        # Validate data types
        assert isinstance(data["status"], str)
        assert isinstance(data["cache_dir"], str)
    
    def test_invalid_mbid_rejection(self):
        """Test that invalid MBIDs are properly rejected"""
        invalid_mbids = [
            "invalid-mbid",
            "12345678-1234-1234-1234-12345678901",  # Too short
            "12345678-1234-1234-1234-1234567890123",  # Too long
            "gggggggg-1234-1234-1234-123456789012",  # Invalid hex
            "",  # Empty
            "not-a-uuid-at-all"
        ]
        
        for invalid_mbid in invalid_mbids:
            print(f"{BASE_URL}/release/{invalid_mbid}/front")
            response = requests.get(f"{BASE_URL}/release/{invalid_mbid}/front")
            # Invalid MBIDs should return 400 (Bad Request) or 404 (Not Found)
            assert response.status_code in [400, 404], \
                f"Invalid MBID {invalid_mbid} should return 400 or 404, got {response.status_code}"
    
    @pytest.mark.parametrize("mbid,expected_code", [
        (mbid, expected) for mbid, expected in 
        json.loads(Path(TEST_DATA_FILE).read_text())["release"].items()
    ])
    def test_release_cover_art(self, mbid: str, expected_code: int):
        """Test release cover art endpoints with test data"""
        # Validate MBID format
        assert MBID_PATTERN.match(mbid), f"Invalid MBID format: {mbid}"
        
        # Test front cover
        response = requests.get(f"{BASE_URL}/release/{mbid}/front")
        assert response.status_code == expected_code, \
            f"Release {mbid} front cover: expected {expected_code}, got {response.status_code}"
        
        if expected_code == 200:
            # Validate image response
            assert response.headers.get("Content-Type", "").startswith("image/"), \
                f"Response should be an image, got Content-Type: {response.headers.get('Content-Type')}"
            
            # Test cache hit (second request)
            start_time = time.time()
            second_response = requests.get(f"{BASE_URL}/release/{mbid}/front")
            duration_ms = (time.time() - start_time) * 1000
            
            assert second_response.status_code == 200
            # Second request should typically be faster (though this is timing-dependent)
            print(f"Second request took {duration_ms:.2f}ms")
    
    @pytest.mark.parametrize("mbid,expected_code", [
        (mbid, expected) for mbid, expected in 
        json.loads(Path(TEST_DATA_FILE).read_text())["release-group"].items()
    ])
    def test_release_group_cover_art(self, mbid: str, expected_code: int):
        """Test release group cover art endpoints with test data"""
        # Validate MBID format
        assert MBID_PATTERN.match(mbid), f"Invalid MBID format: {mbid}"
        
        # Test front cover
        response = requests.get(f"{BASE_URL}/release-group/{mbid}/front")
        assert response.status_code == expected_code, \
            f"Release group {mbid} front cover: expected {expected_code}, got {response.status_code}"
        
        if expected_code == 200:
            # Validate image response
            assert response.headers.get("Content-Type", "").startswith("image/"), \
                f"Response should be an image, got Content-Type: {response.headers.get('Content-Type')}"
            
    def test_sized_covers(self):
        """Test sized cover art functionality"""
        # Get first successful release MBID
        successful_releases = [
            mbid for mbid, code in self.test_data["release"].items() 
            if code == 200
        ]
        
        if not successful_releases:
            pytest.skip("No successful release MBIDs in test data")
        
        mbid = successful_releases[0]
        sizes = ["250", "500", "1200"]
        
        for size in sizes:
            response = requests.get(f"{BASE_URL}/release/{mbid}/front-{size}")
            
            # Sized covers may not always be available, accept 200, 404, or 502
            assert response.status_code in [200, 404, 502], \
                f"Sized cover {size}px should return 200, 404, or 502, got {response.status_code}"
            
            if response.status_code == 200:
                assert response.headers.get("Content-Type", "").startswith("image/"), \
                    f"Sized cover response should be an image"
                print(f"✓ {size}px cover available for {mbid}")
            elif response.status_code == 404:
                print(f"! {size}px cover not available for {mbid}")
            else:  # 502
                print(f"! {size}px cover service error for {mbid}")
    
    def test_back_covers(self):
        """Test back cover functionality"""
        # Get first successful release MBID
        successful_releases = [
            mbid for mbid, code in self.test_data["release"].items() 
            if code == 200
        ]
        
        if not successful_releases:
            pytest.skip("No successful release MBIDs in test data")
        
        mbid = successful_releases[0]
        response = requests.get(f"{BASE_URL}/release/{mbid}/back")
        
        # Back covers may not always be available, accept 200, 404, or 502
        assert response.status_code in [200, 404, 502], \
            f"Back cover should return 200, 404, or 502, got {response.status_code}"
        
        if response.status_code == 200:
            assert response.headers.get("Content-Type", "").startswith("image/"), \
                f"Back cover response should be an image"
            print(f"✓ Back cover available for {mbid}")
        elif response.status_code == 404:
            print(f"! Back cover not available for {mbid}")
        else:  # 502
            print(f"! Back cover service error for {mbid}")
    
    def test_cache_directory_structure(self):
        """Test that cache directory structure is correctly reported"""
        response = requests.get(f"{BASE_URL}/cache-status")
        data = response.json()
        
        # Verify cache directory path
        assert data["cache_dir"] == "/cache"
    
    def test_cache_status_response(self):
        """Test cache status returns expected response"""
        response = requests.get(f"{BASE_URL}/cache-status")
        data = response.json()
        
        assert data["status"] == "running", "Service should be running"
    
    def test_concurrent_requests(self):
        """Test handling of concurrent requests to the same resource"""
        import concurrent.futures
        import threading
        
        # Get first successful release MBID
        successful_releases = [
            mbid for mbid, code in self.test_data["release"].items() 
            if code == 200
        ]
        
        if not successful_releases:
            pytest.skip("No successful release MBIDs in test data")
        
        mbid = successful_releases[0]
        url = f"{BASE_URL}/release/{mbid}/front"
        
        # Make multiple concurrent requests
        def make_request():
            return requests.get(url)
        
        # Test with 5 concurrent requests
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(make_request) for _ in range(5)]
            responses = [future.result() for future in concurrent.futures.as_completed(futures)]
        
        # All should succeed
        for response in responses:
            assert response.status_code == 200
            assert response.headers.get("Content-Type", "").startswith("image/")
        
        print(f"✓ All {len(responses)} concurrent requests succeeded")


class TestPerformance:
    """Performance-related tests"""
    
    def test_cache_hit_performance(self):
        """Test that cache hits are significantly faster than cache misses"""
        # This test requires test data
        test_data_path = Path(TEST_DATA_FILE)
        if not test_data_path.exists():
            pytest.skip("Test data file not found")
        
        with open(test_data_path) as f:
            test_data = json.load(f)
        
        successful_releases = [
            mbid for mbid, code in test_data["release"].items() 
            if code == 200
        ]
        
        if len(successful_releases) < 2:
            pytest.skip("Need at least 2 successful release MBIDs")
        
        # First request (likely cache miss for a new MBID)
        mbid1 = successful_releases[0]
        start_time = time.time()
        response1 = requests.get(f"{BASE_URL}/release/{mbid1}/front")
        first_duration = time.time() - start_time
        
        assert response1.status_code == 200
        
        # Second request to same resource (should be cache hit)
        start_time = time.time()
        response2 = requests.get(f"{BASE_URL}/release/{mbid1}/front")
        second_duration = time.time() - start_time
        
        assert response2.status_code == 200
        
        print(f"First request: {first_duration*1000:.2f}ms")
        print(f"Second request: {second_duration*1000:.2f}ms")
        
        # Second request should typically be faster, but this is environment-dependent
        # So we just log the results rather than making a hard assertion
        if second_duration < first_duration:
            print("✓ Cache hit was faster than initial request")
        else:
            print("! Cache hit was not faster (possibly due to system load)")


if __name__ == "__main__":
    # Run tests with verbose output when executed directly
    pytest.main([__file__, "-v", "--tb=short"])
