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
CACHE_DIR = Path(os.environ.get("COVER_ART_CACHE_DIR", "/cover-art-cache"))
CLEANUP_INTERVAL = int(os.environ.get('COVER_ART_CACHE_CLEANUP_INTERVAL', '300'))  # Default 5 minutes
MAX_VOLUME_PERCENT = int(os.environ.get('COVER_ART_CACHE_MAX_VOLUME_PERCENT', '80'))
CLEAN_TO_PERCENT = int(os.environ.get('COVER_ART_CACHE_CLEAN_TO_PERCENT', '75'))

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
    
    def get_volume_usage(self) -> Tuple[int, int, float]:
        """Get disk usage statistics for the cache volume.
        
        Returns:
            Tuple of (used_bytes, total_bytes, usage_percent)
        """
        try:
            stat = shutil.disk_usage(CACHE_DIR)
            usage_percent = (stat.used / stat.total) * 100
            return stat.used, stat.total, usage_percent
        except Exception as e:
            logger.error(f"Error getting disk usage: {e}")
            return 0, 0, 0.0
    
    def cleanup_cache(self) -> None:
        """Perform cache cleanup by removing oldest files based on disk usage."""
        # Check current disk usage
        used_bytes, total_bytes, usage_percent = self.get_volume_usage()
        
        logger.info(f"Volume usage: {usage_percent:.1f}% ({used_bytes / 1024 / 1024:.0f}MB / {total_bytes / 1024 / 1024:.0f}MB)")
        
        # Check if cleanup is needed
        if usage_percent < MAX_VOLUME_PERCENT:
            return
        
        logger.info(f"Volume usage {usage_percent:.1f}% exceeds threshold {MAX_VOLUME_PERCENT}%, starting cleanup")
        
        # Calculate target usage in bytes
        target_usage_bytes = int((CLEAN_TO_PERCENT / 100) * total_bytes)
        bytes_to_free = used_bytes - target_usage_bytes
        
        logger.info(f"Need to free {bytes_to_free / 1024 / 1024:.0f}MB to reach {CLEAN_TO_PERCENT}% usage")
        
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
        
        # Get final disk usage
        final_used, final_total, final_percent = self.get_volume_usage()
        
        logger.info(f"Cleanup complete: removed {removed_count} files, freed {freed_bytes / 1024 / 1024:.0f}MB")
        logger.info(f"Final volume usage: {final_percent:.1f}%")
    
    def run(self) -> None:
        """Main service loop."""
        logger.info(f"Cache cleanup service started (PID: {os.getpid()})")
        logger.info(f"Cache directory: {CACHE_DIR}")
        logger.info(f"Max volume usage threshold: {MAX_VOLUME_PERCENT}%")
        logger.info(f"Cleanup target: {CLEAN_TO_PERCENT}%")
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
