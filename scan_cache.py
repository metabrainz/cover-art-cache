#!/usr/bin/env python3
"""
Cache Rescan Script

This script rescans the entire cache directory and atomically updates the Redis index.
It's designed to handle millions of files efficiently without disrupting the running service.

Usage:
    python rescan_cache.py [--dry-run] [--progress] [--quiet]
"""

import argparse
import logging
import sys
import time
from pathlib import Path

import redis

# Add the cover_art_cache package to path
sys.path.insert(0, str(Path(__file__).parent))

from cover_art_cache.cache import DistributedCacheIndex

def setup_logging(quiet: bool = False, verbose: bool = False):
    """Set up logging configuration."""
    if quiet:
        level = logging.WARNING
    elif verbose:
        level = logging.DEBUG
    else:
        level = logging.INFO
    
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        stream=sys.stdout
    )

def progress_reporter(files_scanned: int, total_mb: float):
    """Progress callback for reporting scan progress."""
    print(f"\rScanned {files_scanned:,} files ({total_mb:.1f} MB)", end='', flush=True)

def main():
    parser = argparse.ArgumentParser(
        description="Rescan cache directory and update Redis index atomically",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python rescan_cache.py                    # Full rescan and update
  python rescan_cache.py --dry-run          # Scan only, don't update Redis
  python rescan_cache.py --progress         # Show progress during scan
  python rescan_cache.py --quiet            # Minimal output
        """
    )
    
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan the cache but don't update Redis index"
    )
    
    parser.add_argument(
        "--progress",
        action="store_true",
        help="Show progress during scanning"
    )
    
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Minimal output (warnings and errors only)"
    )
    
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Verbose output for debugging"
    )
    
    parser.add_argument(
        "--redis-host",
        default="redis",
        help="Redis hostname (default: redis)"
    )
    
    parser.add_argument(
        "--redis-port",
        type=int,
        default=6379,
        help="Redis port (default: 6379)"
    )
    
    args = parser.parse_args()
    
    # Set up logging
    setup_logging(quiet=args.quiet, verbose=args.verbose)
    logger = logging.getLogger(__name__)
    
    if not args.quiet:
        print("🔍 Cover Art Cache Rescan Tool")
        print("=" * 40)
    
    # Connect to Redis
    try:
        redis_client = redis.Redis(
            host=args.redis_host, 
            port=args.redis_port, 
            db=0, 
            decode_responses=True
        )
        redis_client.ping()
        logger.info(f"Connected to Redis at {args.redis_host}:{args.redis_port}")
    except Exception as e:
        logger.error(f"Failed to connect to Redis: {e}")
        return 1
    
    # Set up progress callback
    progress_callback = progress_reporter if args.progress else None
    
    try:
        # Initialize cache index
        cache_index = DistributedCacheIndex(redis_client)
        
        # Phase 1: Scan cache directory
        if not args.quiet:
            print("📁 Scanning cache directory...")
        
        start_time = time.time()
        files_scanned, total_bytes = cache_index.scan_cache_directory(progress_callback)
        scan_time = time.time() - start_time
        
        if args.progress:
            print()  # New line after progress output
        
        total_mb = total_bytes / 1024 / 1024
        
        if not args.quiet:
            print(f"✅ Scan complete:")
            print(f"   📊 Files: {files_scanned:,}")
            print(f"   💾 Size: {total_mb:.2f} MB")
            print(f"   ⏱️  Time: {scan_time:.2f} seconds")
            print(f"   🚀 Rate: {files_scanned/scan_time:.0f} files/sec")
        
        if args.dry_run:
            if not args.quiet:
                print("🔄 Dry run mode - not updating Redis index")
            logger.info("Dry run complete - temporary index will be cleaned up")
            return 0
        
        # Phase 2: Atomic swap
        if not args.quiet:
            print("🔄 Swapping cache index atomically...")
        
        swap_start = time.time()
        success = cache_index.atomic_index_swap()
        swap_time = time.time() - swap_start
        
        if success:
            if not args.quiet:
                print(f"✅ Index updated successfully in {swap_time:.3f}s")
                print("🎉 Cache rescan complete!")
            logger.info("Cache rescan and index update completed successfully")
            return 0
        else:
            logger.error("Failed to swap cache index")
            return 1
            
    except KeyboardInterrupt:
        logger.warning("Rescan interrupted by user")
        if not args.quiet:
            print("\n⚠️  Rescan interrupted - cleaning up...")
        return 130
    except Exception as e:
        logger.error(f"Unexpected error during rescan: {e}", exc_info=args.verbose)
        return 1

if __name__ == "__main__":
    sys.exit(main())