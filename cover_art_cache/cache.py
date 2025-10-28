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

# Cache configuration
CACHE_DIR = Path("/var/cache/nginx/images")
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
