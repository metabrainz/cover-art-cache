"""
Common definitions and constants for the Cover Art Cache Service.

This module contains shared constants used by both the main application
and the cache cleaner service to avoid duplication.
"""

from dataclasses import dataclass
import logging
import json
import os
import re
from pathlib import Path
from typing import Dict, List, Optional

# Configuration
import os
CACHE_DIR = Path(os.environ.get("COVER_ART_CACHE_DIR", "/cover-art-cache"))
MAX_CACHE_SIZE = int(os.environ.get('COVER_ART_CACHE_MAX_SIZE', '100')) * 1024 * 1024  # Convert MB to bytes
CLEANUP_INTERVAL = int(os.environ.get('COVER_ART_CACHE_CLEANUP_INTERVAL', '300'))  # Default 5 minutes

# Cache cleanup thresholds
CLEANUP_THRESHOLD = 0.95  # Start cleanup when cache is 95% full
CLEANUP_TARGET = 0.90     # Clean down to 90% of max size

# Cover Art Archive configuration
COVERART_BASE_URL = "https://coverartarchive.org"

# MBID validation pattern
MBID_PATTERN = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')

# Redis keys for distributed cache index
REDIS_CACHE_ITEMS_KEY = "cache:items"
REDIS_TOTAL_BYTES_KEY = "cache:total_bytes"
REDIS_CLEANUP_LOCK_KEY = "cache:cleanup_lock"

# Cache types
CACHE_TYPE_RELEASE = "release"
CACHE_TYPE_RELEASE_GROUP = "release-group"

@dataclass
class CacheItem:
    """Represents a cached file with metadata."""
    cache_type: str  # "release" or "release-group"
    mbid: str
    cache_key: str
    file_path: Path
    downloaded_at: float  # timestamp from time.time()
    size_bytes: int

logger = logging.getLogger(__name__)

def validate_mbid(mbid: str) -> bool:
    """Validate MBID format."""
    return bool(MBID_PATTERN.match(mbid))

def get_cache_subdir(cache_type: str, mbid: str) -> Path:
    """Generate cache subdirectory path for deep directory structure."""
    char1 = mbid[0]
    char2 = mbid[1:3]
    char3 = mbid[0:3]
    return CACHE_DIR / cache_type / char1 / char2 / char3

def format_bytes_mb(bytes_value: int) -> float:
    """Convert bytes to MB with 2 decimal places."""
    return round(bytes_value / 1024 / 1024, 2)

def get_cache_usage_percent(current_bytes: int) -> float:
    """Calculate cache usage percentage."""
    if MAX_CACHE_SIZE <= 0:
        return 0.0
    return round((current_bytes / MAX_CACHE_SIZE) * 100, 1)


def check_cache_directory_writable() -> bool:
    """
    Check if the cache directory exists and is writable.
    
    Returns:
        bool: True if cache directory is writable, False otherwise
    """
    try:
        # Check if cache directory exists, create if not
        if not CACHE_DIR.exists():
            logger.info(f"Cache directory {CACHE_DIR} does not exist, creating...")
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
        
        # Test writability by creating a temporary test file
        test_file = CACHE_DIR / ".write_test"
        
        try:
            # Try to write to the directory
            with open(test_file, 'w') as f:
                f.write("test")
            
            # Try to read back the file
            with open(test_file, 'r') as f:
                content = f.read()
            
            # Clean up test file
            test_file.unlink()
            
            if content == "test":
                logger.info(f"Cache directory {CACHE_DIR} is writable")
                return True
            else:
                logger.error(f"Cache directory {CACHE_DIR} write test failed - content mismatch")
                return False
                
        except Exception as e:
            logger.error(f"Cache directory {CACHE_DIR} is not writable: {e}")
            # Clean up test file if it exists
            if test_file.exists():
                try:
                    test_file.unlink()
                except:
                    pass
            return False
            
    except Exception as e:
        logger.error(f"Error checking cache directory {CACHE_DIR}: {e}")
        return False


class DistributedCacheIndex:
    """Redis-based cache index that works across multiple processes."""
    
    def __init__(self, redis_client):
        self.redis = redis_client
        self.cache_items_key = REDIS_CACHE_ITEMS_KEY
        self.total_bytes_key = REDIS_TOTAL_BYTES_KEY
        
    def add_item(self, item: CacheItem) -> None:
        """Add a cache item to the distributed index."""
        try:
            # Store cache item as JSON
            item_data = {
                "cache_type": item.cache_type,
                "mbid": item.mbid,
                "cache_key": item.cache_key,
                "file_path": str(item.file_path),
                "downloaded_at": item.downloaded_at,
                "size_bytes": item.size_bytes
            }
            
            # Get old item size if it exists
            old_item_json = self.redis.hget(self.cache_items_key, item.cache_key)
            old_size = 0
            if old_item_json:
                old_item = json.loads(old_item_json)
                old_size = old_item.get("size_bytes", 0)
            
            # Update item and total bytes atomically
            with self.redis.pipeline() as pipe:
                pipe.hset(self.cache_items_key, item.cache_key, json.dumps(item_data))
                pipe.incrby(self.total_bytes_key, item.size_bytes - old_size)
                pipe.execute()
            
            logger.debug(f"Added cache item to Redis: {item.cache_key}")
            
        except Exception as e:
            logger.error(f"Error adding item to Redis cache index: {e}")
    
    def remove_item(self, cache_key: str) -> Optional[dict]:
        """Remove a cache item from the distributed index."""
        try:
            # Get item data before removing
            item_json = self.redis.hget(self.cache_items_key, cache_key)
            if not item_json:
                return None
                
            item_data = json.loads(item_json)
            
            # Remove item and update total bytes atomically
            with self.redis.pipeline() as pipe:
                pipe.hdel(self.cache_items_key, cache_key)
                pipe.decrby(self.total_bytes_key, item_data["size_bytes"])
                pipe.execute()
            
            logger.debug(f"Removed cache item from Redis: {cache_key}")
            return item_data
            
        except Exception as e:
            logger.error(f"Error removing item from Redis cache index: {e}")
            return None
    
    def get_total_bytes(self) -> int:
        """Get total bytes used by cached files."""
        try:
            total = self.redis.get(self.total_bytes_key)
            return int(total) if total else 0
        except Exception as e:
            logger.error(f"Error getting total bytes from Redis: {e}")
            return 0
    
    def get_item_count(self) -> int:
        """Get total number of cached files."""
        try:
            return self.redis.hlen(self.cache_items_key)
        except Exception as e:
            logger.error(f"Error getting item count from Redis: {e}")
            return 0
    
    def get_items_by_type(self, cache_type: str) -> List[dict]:
        """Get all items of a specific cache type."""
        try:
            all_items = self.redis.hgetall(self.cache_items_key)
            items = []
            for cache_key, item_json in all_items.items():
                item_data = json.loads(item_json)
                if item_data.get("cache_type") == cache_type:
                    items.append(item_data)
            return items
        except Exception as e:
            logger.error(f"Error getting items by type from Redis: {e}")
            return []

    def scan_cache_directory(self, progress_callback=None) -> tuple[int, int]:
        """
        Scan the cache directory and build a new cache index.
        
        Args:
            progress_callback: Optional callback function called with (files_scanned, total_mb) for progress reporting
        
        Returns:
            tuple of (files_scanned, total_bytes)
        """
        import time
        
        logger.info("Scanning cache directory to build index...")
        start_time = time.time()
        files_scanned = 0
        total_bytes = 0
        
        # Temporary keys for building new index
        temp_cache_items_key = f"{self.cache_items_key}:temp"
        temp_total_bytes_key = f"{self.total_bytes_key}:temp"
        
        try:
            # Clear any existing temporary keys
            self.redis.delete(temp_cache_items_key, temp_total_bytes_key)
            logger.info("Cleared existing temporary Redis keys")
            
            # Initialize temporary total bytes to 0
            self.redis.set(temp_total_bytes_key, 0)
            
            # Initialize temporary cache index
            temp_cache_index = DistributedCacheIndex(self.redis)
            temp_cache_index.cache_items_key = temp_cache_items_key
            temp_cache_index.total_bytes_key = temp_total_bytes_key
            
            # Scan both release and release-group directories
            for cache_type in [CACHE_TYPE_RELEASE, CACHE_TYPE_RELEASE_GROUP]:
                cache_type_dir = CACHE_DIR / cache_type
                if not cache_type_dir.exists():
                    continue
                    
                # Recursively find all files in the cache type directory
                for file_path in cache_type_dir.rglob('*'):
                    if not file_path.is_file():
                        continue
                        
                    try:
                        # Extract cache key from filename (remove extension)
                        cache_key = file_path.stem
                        
                        # Extract MBID from cache key (first part before underscore)
                        mbid = cache_key.split('_')[0]
                        
                        # Validate MBID format
                        if not validate_mbid(mbid):
                            logger.warning(f"Invalid MBID format in cache file: {file_path}")
                            continue
                        
                        # Get file stats
                        stat = file_path.stat()
                        
                        # Create cache item
                        cache_item = CacheItem(
                            cache_type=cache_type,
                            mbid=mbid,
                            cache_key=cache_key,
                            file_path=file_path,
                            downloaded_at=stat.st_mtime,  # Use modification time as download time
                            size_bytes=stat.st_size
                        )
                        
                        # Add to temporary index
                        temp_cache_index.add_item(cache_item)
                        files_scanned += 1
                        total_bytes += stat.st_size
                        
                        # Call progress callback every 1000 files
                        if progress_callback and files_scanned % 1000 == 0:
                            progress_callback(files_scanned, total_bytes / 1024 / 1024)
                        
                    except Exception as e:
                        logger.warning(f"Error processing cache file {file_path}: {e}")
                        continue
            
            scan_time = time.time() - start_time
            total_mb = total_bytes / 1024 / 1024
            logger.info(f"Cache scan complete: {files_scanned} files, {total_mb:.2f}MB total, took {scan_time:.2f}s")
            
            # Ensure temporary total bytes reflects the final scan result
            self.redis.set(temp_total_bytes_key, total_bytes)
            
            return files_scanned, total_bytes
            
        except Exception as e:
            logger.error(f"Error during cache scan: {e}")
            # Clean up temporary keys on error
            self.redis.delete(temp_cache_items_key, temp_total_bytes_key)
            raise

    def atomic_index_swap(self) -> bool:
        """
        Atomically swap the temporary cache index with the live index.
        
        Returns:
            bool: True if swap was successful, False otherwise
        """
        temp_cache_items_key = f"{self.cache_items_key}:temp"
        temp_total_bytes_key = f"{self.total_bytes_key}:temp"
        
        try:
            # Use Redis pipeline for atomic operations
            pipe = self.redis.pipeline()
            
            # Check if temporary total bytes key exists (this is always created during scan)
            if not self.redis.exists(temp_total_bytes_key):
                logger.error("Temporary cache index not found - run scan first")
                return False
            
            # For empty cache, we need to handle the case where temp keys exist but are empty
            temp_total_bytes = self.redis.get(temp_total_bytes_key) or "0"
            temp_item_count = self.redis.hlen(temp_cache_items_key) if self.redis.exists(temp_cache_items_key) else 0
            
            logger.info(f"Swapping cache index: {temp_item_count} items, {temp_total_bytes} bytes")
            
            # Delete existing live keys first to avoid rename conflicts
            pipe.delete(self.cache_items_key)
            pipe.delete(self.total_bytes_key)
            
            # Set the total bytes (works for both empty and non-empty caches)
            pipe.set(self.total_bytes_key, temp_total_bytes)
            
            # Only rename the items hash if it exists and has data
            if temp_item_count > 0:
                pipe.rename(temp_cache_items_key, self.cache_items_key)
            else:
                # For empty cache, just ensure the key exists but is empty
                pipe.delete(self.cache_items_key)  # Ensure it's truly empty
            
            # Clean up temporary keys
            pipe.delete(temp_cache_items_key)
            pipe.delete(temp_total_bytes_key)
            
            # Execute pipeline atomically
            pipe.execute()
            
            logger.info("Successfully swapped cache index atomically")
            return True
            
        except Exception as e:
            logger.error(f"Error during atomic index swap: {e}")
            # Clean up temporary keys on error
            try:
                self.redis.delete(temp_cache_items_key, temp_total_bytes_key)
            except:
                pass
            return False

    def exists(self) -> bool:
        """
        Check if cache index data exists in Redis.
        
        Returns:
            bool: True if cache index exists and has data, False otherwise
        """
        try:
            # Check if both keys exist and have data
            items_exist = self.redis.exists(self.cache_items_key)
            bytes_exist = self.redis.exists(self.total_bytes_key)
            
            if not items_exist or not bytes_exist:
                return False
            
            # Check if items hash has any data
            items_count = self.redis.hlen(self.cache_items_key)
            
            return items_count > 0
            
        except Exception as e:
            logger.error(f"Error checking cache index existence: {e}")
            return False


def startup_cache_scan(redis_client) -> bool:
    """
    Perform cache scan at startup if needed.
    
    Args:
        redis_client: Redis client instance
    
    Returns:
        bool: True if scan was successful or not needed, False if scan failed
    """
    try:
        # Initialize cache index
        cache_index = DistributedCacheIndex(redis_client)
        
        # Check if cache index already exists
        if cache_index.exists():
            logger.info("Cache index already exists in Redis - skipping scan")
            return True
        
        logger.info("Cache index not found in Redis - performing startup scan...")
        
        # Perform the scan
        files_scanned, total_bytes = cache_index.scan_cache_directory()
        
        # Perform atomic swap
        success = cache_index.atomic_index_swap()
        
        if success:
            total_mb = total_bytes / 1024 / 1024
            logger.info(f"Startup cache scan completed: {files_scanned} files, {total_mb:.2f}MB")
            return True
        else:
            logger.error("Failed to swap cache index during startup scan")
            return False
            
    except Exception as e:
        logger.error(f"Error during startup cache scan: {e}")
        return False
