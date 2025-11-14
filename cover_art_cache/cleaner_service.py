#!/usr/bin/env python3
"""
Cache Cleanup Service

A dedicated service that periodically checks disk space and removes
oldest files when the volume usage exceeds the configured threshold.
"""

import os
import shutil
import time
import logging
import heapq
from pathlib import Path
from typing import List, Tuple
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
CACHE_DIR = Path("/cover-art-cache")
CLEANUP_INTERVAL = int(os.environ.get('COVER_ART_CACHE_CLEANUP_INTERVAL', '300'))  # Default 5 minutes
MAX_SIZE_MB = int(os.environ.get('COVER_ART_CACHE_MAX_SIZE_MB', '10000'))  # Default 10GB
CLEAN_TO_MB = int(os.environ.get('COVER_ART_CACHE_CLEAN_TO_MB', '8000'))  # Default 8GB

class CacheCleanupService:
    """Service that manages cache cleanup operations based on disk usage."""
    
    def __init__(self):
        self.running = True
        
        # Set up signal handlers for graceful shutdown
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully."""
        logger.info(f"Received signal {signum}, shutting down...")
        self.running = False
    
    def get_cache_size(self) -> int:
        """Get total size of cached files in bytes.
        
        Returns:
            Total size of cache in bytes
        """
        try:
            total_size = 0
            for file_path in CACHE_DIR.rglob('*'):
                if file_path.is_file():
                    try:
                        total_size += file_path.stat().st_size
                    except Exception:
                        continue
            return total_size
        except Exception as e:
            logger.error(f"Error calculating cache size: {e}")
            return 0
    
    def cleanup_cache(self) -> None:
        """Perform cache cleanup by removing oldest files based on cache size."""
        # Check current cache size
        cache_size_bytes = self.get_cache_size()
        cache_size_mb = cache_size_bytes / 1024 / 1024
        
        logger.info(f"Cache size: {cache_size_mb:.0f}MB")
        
        # Check if cleanup is needed
        max_size_bytes = MAX_SIZE_MB * 1024 * 1024
        if cache_size_bytes < max_size_bytes:
            return
        
        logger.info(f"Cache size {cache_size_mb:.0f}MB exceeds threshold {MAX_SIZE_MB}MB, starting cleanup")
        
        # Calculate bytes to free
        target_size_bytes = CLEAN_TO_MB * 1024 * 1024
        bytes_to_free = cache_size_bytes - target_size_bytes
        
        logger.info(f"Need to free {bytes_to_free / 1024 / 1024:.0f}MB to reach {CLEAN_TO_MB}MB")
        
        # Scan files and collect only what we need to delete
        # We'll keep a max-heap of the oldest files until we have enough to meet our target
        files_to_delete = []  # max-heap of (-mtime, file_path, file_size) - negative for max-heap
        accumulated_size = 0
        scanned_count = 0
        
        # We'll keep files until we've accumulated enough space, plus a 20% buffer
        target_accumulated = int(bytes_to_free * 1.2)
        
        try:
            logger.info(f"Scanning cache directory: {CACHE_DIR}")
            
            for file_path in CACHE_DIR.rglob('*'):
                if not file_path.is_file():
                    continue
                    
                try:
                    stat = file_path.stat()
                    scanned_count += 1
                    
                    if accumulated_size < target_accumulated:
                        # Still accumulating - add to heap
                        heapq.heappush(files_to_delete, (-stat.st_mtime, file_path, stat.st_size))
                        accumulated_size += stat.st_size
                    else:
                        # We have enough accumulated - only add if this file is older than the newest in heap
                        if files_to_delete and -stat.st_mtime > files_to_delete[0][0]:
                            # This file is older, replace the newest file in heap
                            removed_mtime, removed_path, removed_size = heapq.heappop(files_to_delete)
                            heapq.heappush(files_to_delete, (-stat.st_mtime, file_path, stat.st_size))
                            accumulated_size = accumulated_size - removed_size + stat.st_size
                    
                    # Log progress every 10000 files
                    if scanned_count % 10000 == 0:
                        logger.info(f"Scanned {scanned_count} files, tracking {len(files_to_delete)} for deletion...")
                        
                except Exception as e:
                    logger.warning(f"Error getting stats for {file_path}: {e}")
                    continue
            
            logger.info(f"Scanned {scanned_count} total files, selected {len(files_to_delete)} oldest files for deletion")
            
        except Exception as e:
            logger.error(f"Error during file scanning: {e}")
            return
        
        # Delete files (they're already in the right order - oldest first due to max-heap)
        # Convert to min-heap to pop oldest first
        min_heap = [(-mtime, path, size) for mtime, path, size in files_to_delete]
        heapq.heapify(min_heap)
        
        freed_bytes = 0
        removed_count = 0
        
        logger.info("Starting file deletion...")
        while min_heap and freed_bytes < bytes_to_free:
            neg_mtime, file_path, file_size = heapq.heappop(min_heap)
            
            try:
                file_path.unlink()
                freed_bytes += file_size
                removed_count += 1
                
                if removed_count % 1000 == 0:
                    logger.info(f"Removed {removed_count} files, freed {freed_bytes / 1024 / 1024:.0f}MB so far...")
                    
            except Exception as e:
                logger.warning(f"Failed to remove {file_path}: {e}")
        
        # Get final cache size
        final_cache_size = self.get_cache_size()
        final_cache_mb = final_cache_size / 1024 / 1024
        
        logger.info(f"Cleanup complete: removed {removed_count} files, freed {freed_bytes / 1024 / 1024:.0f}MB")
        logger.info(f"Final cache size: {final_cache_mb:.0f}MB")
    
    def run(self) -> None:
        """Main service loop."""
        logger.info(f"Cache cleanup service started (PID: {os.getpid()})")
        logger.info(f"Cache directory: {CACHE_DIR}")
        logger.info(f"Max cache size threshold: {MAX_SIZE_MB}MB")
        logger.info(f"Cleanup target: {CLEAN_TO_MB}MB")
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
