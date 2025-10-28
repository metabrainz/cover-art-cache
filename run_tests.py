#!/usr/bin/env python3
"""
Simple test runner for Cover Art Cache Service

This script replaces test.sh with a Python-based test runner.
Usage:
    python run_tests.py           # Run all tests
    python run_tests.py --fast    # Skip performance tests
    python run_tests.py --help    # Show help
"""

import sys
import subprocess
import argparse
from pathlib import Path


def check_dependencies():
    """Check if required test dependencies are installed"""
    try:
        import pytest
        import requests
        return True
    except ImportError as e:
        print(f"❌ Missing required test dependency: {e}")
        print("Install with: pip install -r requirements-test.txt")
        return False


def run_tests(args):
    """Run the test suite with pytest"""
    if not check_dependencies():
        return 1
    
    # Build pytest command
    cmd = ["python", "-m", "pytest", "test_cover_art_cache.py"]
    
    if args.verbose:
        cmd.append("-v")
    
    if args.fast:
        cmd.extend(["-m", "not performance"])
    
    if args.coverage:
        cmd.extend(["--cov=cover_art_cache", "--cov-report=term-missing"])
    
    if args.parallel:
        cmd.extend(["-n", "auto"])
    
    # Add any extra arguments
    if args.pytest_args:
        cmd.extend(args.pytest_args)
    
    print(f"🚀 Running: {' '.join(cmd)}")
    print("=" * 60)
    
    # Run tests
    result = subprocess.run(cmd)
    return result.returncode


def main():
    parser = argparse.ArgumentParser(
        description="Test runner for Cover Art Cache Service",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run_tests.py                    # Run all tests
  python run_tests.py --fast             # Skip slow tests
  python run_tests.py --coverage         # Include coverage
  python run_tests.py --parallel         # Run tests in parallel
  python run_tests.py -k test_health     # Run specific test
        """
    )
    
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Skip performance tests"
    )
    
    parser.add_argument(
        "--coverage",
        action="store_true",
        help="Include coverage reporting"
    )
    
    parser.add_argument(
        "--parallel",
        action="store_true",
        help="Run tests in parallel"
    )
    
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Verbose output"
    )
    
    parser.add_argument(
        "pytest_args",
        nargs="*",
        help="Additional arguments to pass to pytest (e.g., -k test_health)"
    )
    
    args = parser.parse_args()
    
    # Check that we're in the right directory
    if not Path("test_cover_art_cache.py").exists():
        print("❌ Error: test_cover_art_cache.py not found in current directory")
        print("Please run this script from the cover-art-cache directory")
        return 1
    
    return run_tests(args)


if __name__ == "__main__":
    sys.exit(main())