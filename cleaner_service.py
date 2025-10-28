#!/usr/bin/env python3
"""
Cache Cleanup Service

A dedicated service that periodically checks the cache size and removes
oldest files when the cache exceeds 90% of the configured limit.
"""

import os
import time
import logging
import redis
import json
from pathlib import Path
from typing import List, Dict
import signal
import sys

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger('cleanup_service')

# Configuration
CACHE_DIR = Path("/var/cache/nginx/images")
MAX_CACHE_SIZE = int(os.environ.get('COVER_ART_CACHE_MAX_SIZE', '100')) * 1024 * 1024  # Convert MB to bytes
CLEANUP_INTERVAL = int(os.environ.get('COVER_ART_CACHE_CLEANUP_INTERVAL', '300'))  # Default 5 minutes
CLEANUP_THRESHOLD = 0.95  # Start cleanup when cache is 90% full
CLEANUP_TARGET = 0.9    # Clean down to 80% of max size

# Redis connection
try:
    redis_client = redis.Redis(host='redis', port=6379, db=0, decode_responses=True)
    redis_client.ping()
    logger.info("Connected to Redis cache index")
except Exception as e:
    logger.error(f"Failed to connect to Redis: {e}")
    sys.exit(1)

# Redis keys (must match app.py)
CACHE_ITEMS_KEY = "cache:items"
TOTAL_BYTES_KEY = "cache:total_bytes"
CLEANUP_LOCK_KEY = "cache:cleanup_lock"

class CacheCleanupService:
    """Service that manages cache cleanup operations."""
    
    def __init__(self):
        self.running = True
        self.redis = redis_client
        
        # Set up signal handlers for graceful shutdown
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully."""
        logger.info(f"Received signal {signum}, shutting down...")
        self.running = False
    
    def get_total_bytes(self) -> int:
        """Get total cache size from Redis."""
        try:
            total = self.redis.get(TOTAL_BYTES_KEY)
            return int(total) if total else 0
        except Exception as e:
            logger.error(f"Error getting total bytes from Redis: {e}")
            return 0
    
    def get_oldest_items(self, count: int) -> List[Dict]:
        """Get the oldest cache items by download time."""
        try:
            # Get all items and sort by download time
            all_items = self.redis.hgetall(CACHE_ITEMS_KEY)
            items_with_time = []
            
            for cache_key, item_json in all_items.items():
                try:
                    item_data = json.loads(item_json)
                    items_with_time.append((item_data["downloaded_at"], cache_key, item_data))
                except json.JSONDecodeError as e:
                    logger.warning(f"Failed to parse item data for {cache_key}: {e}")
                    continue
            
            # Sort by download time and return oldest
            items_with_time.sort(key=lambda x: x[0])
            return [item_data for _, _, item_data in items_with_time[:count]]
            
        except Exception as e:
            logger.error(f"Error getting oldest items from Redis: {e}")
            return []
    
    def acquire_cleanup_lock(self, timeout: int = 300) -> bool:
        """Acquire a distributed lock for cleanup operations."""
        try:
            return self.redis.set(CLEANUP_LOCK_KEY, f"cleanup_service_{os.getpid()}", nx=True, ex=timeout)
        except Exception as e:
            logger.error(f"Error acquiring cleanup lock: {e}")
            return False
    
    def release_cleanup_lock(self) -> None:
        """Release the distributed cleanup lock."""
        try:
            self.redis.delete(CLEANUP_LOCK_KEY)
        except Exception as e:
            logger.error(f"Error releasing cleanup lock: {e}")
    
    def remove_cache_item(self, cache_key: str) -> int:
        """Remove a cache item from Redis and return its size."""
        try:
            # Get item data before removing
            item_json = self.redis.hget(CACHE_ITEMS_KEY, cache_key)
            if not item_json:
                return 0
                
            item_data = json.loads(item_json)
            size_bytes = item_data.get("size_bytes", 0)
            
            # Remove item and update total bytes atomically
            with self.redis.pipeline() as pipe:
                pipe.hdel(CACHE_ITEMS_KEY, cache_key)
                pipe.decrby(TOTAL_BYTES_KEY, size_bytes)
                pipe.execute()
            
            logger.debug(f"Removed cache item from Redis: {cache_key}")
            return size_bytes
            
        except Exception as e:
            logger.error(f"Error removing item from Redis cache index: {e}")
            return 0
    
    def cleanup_cache(self) -> None:
        """Perform cache cleanup by removing oldest files."""
        current_size = self.get_total_bytes()
        cleanup_threshold_size = int(MAX_CACHE_SIZE * CLEANUP_THRESHOLD)
        
        if current_size <= cleanup_threshold_size:
            logger.debug(f"Cache size ({current_size / 1024 / 1024:.2f}MB) below cleanup threshold ({cleanup_threshold_size / 1024 / 1024:.2f}MB)")
            return
        
        # Try to acquire cleanup lock
        if not self.acquire_cleanup_lock(timeout=300):
            logger.info("Another process is already cleaning cache, skipping")
            return
        
        try:
            # Double-check size after acquiring lock
            current_size = self.get_total_bytes()
            if current_size <= cleanup_threshold_size:
                logger.info("Cache was cleaned by another process while waiting for lock")
                return
            
            # Calculate target size and bytes to free
            target_size = int(MAX_CACHE_SIZE * CLEANUP_TARGET)
            bytes_to_free = current_size - target_size
            
            logger.info(f"Starting cache cleanup: current {current_size / 1024 / 1024:.2f}MB, target {target_size / 1024 / 1024:.2f}MB")
            logger.info(f"Need to free {bytes_to_free / 1024 / 1024:.2f}MB")
            
            # Get oldest items
            oldest_items = self.get_oldest_items(1000)  # Get up to 1000 oldest items
            
            freed_bytes = 0
            removed_count = 0
            
            for item_data in oldest_items:
                if freed_bytes >= bytes_to_free:
                    break
                
                try:
                    file_path = Path(item_data["file_path"])
                    cache_key = item_data["cache_key"]
                    
                    # Remove the physical file
                    if file_path.exists():
                        file_path.unlink()
                        logger.debug(f"Removed cache file: {file_path}")
                    else:
                        logger.debug(f"Cache file already missing: {file_path}")
                    
                    # Remove from Redis index
                    item_size = self.remove_cache_item(cache_key)
                    if item_size > 0:
                        freed_bytes += item_size
                        removed_count += 1
                    
                except Exception as e:
                    logger.warning(f"Failed to remove cache file {item_data.get('file_path', 'unknown')}: {e}")
                    # Still try to remove from index
                    self.remove_cache_item(item_data.get('cache_key', ''))
            
            final_size = self.get_total_bytes()
            logger.info(f"Cache cleanup complete: removed {removed_count} files, freed {freed_bytes / 1024 / 1024:.2f}MB")
            logger.info(f"New cache size: {final_size / 1024 / 1024:.2f}MB ({final_size / MAX_CACHE_SIZE * 100:.1f}% of limit)")
            
        finally:
            self.release_cleanup_lock()
    
    def run(self) -> None:
        """Main service loop."""
        logger.info(f"Cache cleanup service started (PID: {os.getpid()})")
        logger.info(f"Cache limit: {MAX_CACHE_SIZE / 1024 / 1024:.2f}MB")
        logger.info(f"Cleanup threshold: {CLEANUP_THRESHOLD * 100:.0f}%")
        logger.info(f"Cleanup target: {CLEANUP_TARGET * 100:.0f}%")
        logger.info(f"Check interval: {CLEANUP_INTERVAL} seconds")
        
        while self.running:
            try:
                self.cleanup_cache()
            except Exception as e:
                logger.error(f"Error during cleanup cycle: {e}")
            
            # Sleep in small increments to allow for graceful shutdown
            for _ in range(CLEANUP_INTERVAL):
                if not self.running:
                    break
                time.sleep(1)
        
        logger.info("Cache cleanup service stopped")

if __name__ == "__main__":
    service = CacheCleanupService()
    try:
        service.run()
    except KeyboardInterrupt:
        logger.info("Service interrupted by user")
    except Exception as e:
        logger.error(f"Service failed: {e}")
        sys.exit(1)