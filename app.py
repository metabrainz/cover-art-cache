#!/usr/bin/env python3
"""
Cover Art Cache Service

A Python service that resolves coverartarchive.org redirects and caches actual images locally,
working with nginx for efficient file serving.
"""

import os
import requests
from pathlib import Path
from flask import Flask, Response, request, jsonify
import logging
from urllib.parse import urlparse
import re
from functools import wraps
import time
from datetime import datetime
from dataclasses import dataclass, field
from typing import Dict, List, Optional
import json
import redis

# Configure logging
import sys
import os

# Force unbuffered output
os.environ['PYTHONUNBUFFERED'] = '1'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout,
    force=True
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Log startup
logger.info("Cover Art Cache Service starting up...")

# Add error handlers
@app.errorhandler(500)
def handle_internal_error(e):
    logger.error(f"Internal server error: {e}")
    return jsonify({"error": "Internal server error"}), 500

@app.errorhandler(Exception)
def handle_exception(e):
    logger.error(f"Unhandled exception: {e}", exc_info=True)
    return jsonify({"error": "Internal server error"}), 500

# Configuration
CACHE_DIR = Path("/var/cache/nginx/images")
COVERART_BASE_URL = "https://coverartarchive.org"
MAX_CACHE_SIZE = int(os.environ.get('COVER_ART_CACHE_MAX_SIZE', '100')) * 1024 * 1024  # Convert MB to bytes

# Ensure cache directory exists
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# MBID regex pattern
MBID_PATTERN = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')

# Cache Index System
@dataclass
class CacheItem:
    """Represents a cached file with metadata."""
    cache_type: str  # "release" or "release-group"
    mbid: str
    cache_key: str
    file_path: Path
    downloaded_at: float  # timestamp from time.time()
    size_bytes: int

# Redis connection for distributed cache index
redis_client = redis.Redis(host='redis', port=6379, db=0, decode_responses=True)
redis_client.ping()
logger.info("Connected to Redis for distributed cache index")

class DistributedCacheIndex:
    """Redis-based cache index that works across multiple processes."""
    
    def __init__(self, redis_client):
        self.redis = redis_client
        self.cache_items_key = "cache:items"
        self.total_bytes_key = "cache:total_bytes"
        
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

# Initialize distributed cache index
cache_index = DistributedCacheIndex(redis_client)
logger.info("Using distributed Redis cache index")

def generate_cache_key(mbid: str, path: str = "", size: str = "") -> str:
    """Generate a cache key based on MBID and path components."""
    if path:
        if size:
            return f"{mbid}_{path}_{size}"
        else:
            return f"{mbid}_{path}"
    else:
        return mbid

def get_cache_path(cache_type: str, cache_key: str, extension: str = '') -> Path:
    """Get the cache file path for a given cache key in the appropriate subdirectory.
    
    Creates a deep subdirectory structure based on the first 3 characters of the MBID
    to distribute files and avoid having too many files in a single directory.
    
    Structure: cache_type/X/XX/XXX/ where X, XX, XXX are progressive prefixes of MBID
    Example: MBID ebe78b00-... creates path: release/e/eb/ebe/ebe78b00_front.jpg
    """
    try:
        # Extract MBID from cache_key (it's the first part before any underscore)
        mbid = cache_key.split('_')[0]
        
        # Create progressive subdirectory structure: e/eb/ebe/
        char1 = mbid[0]      # First character: "e"
        char2 = mbid[:2]     # First two characters: "eb" 
        char3 = mbid[:3]     # First three characters: "ebe"
        
        # Create deep cache subdirectory (release/e/eb/ebe/ or release-group/e/eb/ebe/)
        cache_subdir = CACHE_DIR / cache_type / char1 / char2 / char3
        cache_subdir.mkdir(parents=True, exist_ok=True)
        return cache_subdir / f"{cache_key}{extension}"
    except PermissionError as e:
        logger.error(f"Permission denied creating cache directory {cache_subdir}: {e}")
        raise
    except Exception as e:
        logger.error(f"Error creating cache path for {cache_type}/{cache_key}: {e}")
        raise

def scan_cache_at_startup() -> None:
    """Scan the cache directory at startup to populate the cache index."""
    logger.info("Scanning cache directory to build index...")
    start_time = time.time()
    files_scanned = 0
    
    try:
        # Clear existing Redis index
        cache_index.redis.delete(cache_index.cache_items_key, cache_index.total_bytes_key)
        logger.info("Cleared existing Redis cache index")
        
        # Scan both release and release-group directories
        for cache_type in ["release", "release-group"]:
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
                    if not MBID_PATTERN.match(mbid):
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
                    
                    # Add to index
                    cache_index.add_item(cache_item)
                    files_scanned += 1
                    
                except Exception as e:
                    logger.warning(f"Error processing cache file {file_path}: {e}")
                    continue
        
        scan_time = time.time() - start_time
        total_mb = cache_index.get_total_bytes() / 1024 / 1024
        logger.info(f"Cache scan complete: {files_scanned} files, {total_mb:.2f}MB total, took {scan_time:.2f}s")
        logger.info("Cache index populated in Redis for distributed access")
        
    except Exception as e:
        logger.error(f"Error during cache scan: {e}")

def get_cache_size() -> int:
    """Get the current cache size in bytes from the index."""
    return cache_index.get_total_bytes()

# Note: Cache cleanup is now handled by a separate cleanup service
# to avoid race conditions in multi-process environments

def download_image(url: str, cache_path: Path, cache_type: str, mbid: str, cache_key: str) -> bool:
    """Download an image from URL and save it to cache_path, adding to cache index."""
    try:
        logger.info(f"Downloading image from {url}")
        response = requests.get(url, stream=True, timeout=30)
        
        if response.status_code != 200:
            logger.warning(f"Failed to download image: HTTP {response.status_code}")
            return False
        
        # Ensure parent directory exists
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Write file
        downloaded_bytes = 0
        with open(cache_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
                downloaded_bytes += len(chunk)
        
        logger.info(f"Successfully downloaded image to {cache_path} ({downloaded_bytes} bytes)")
        
        # Add to cache index
        cache_item = CacheItem(
            cache_type=cache_type,
            mbid=mbid,
            cache_key=cache_key,
            file_path=cache_path,
            downloaded_at=time.time(),
            size_bytes=downloaded_bytes
        )
        cache_index.add_item(cache_item)
        
        # Note: Cache cleanup is handled by separate cleanup service
        logger.info(f"Added {downloaded_bytes} bytes to cache index (total: {cache_index.get_total_bytes() / 1024 / 1024:.2f}MB)")
        
        return True
        
    except Exception as e:
        logger.error(f"Error downloading image from {url}: {e}")
        cache_path.unlink(missing_ok=True)
        return False

def resolve_redirect(coverart_url: str) -> str:
    """Resolve the redirect from coverartarchive.org to get the actual image URL."""
    try:
        logger.info(f"Resolving redirect for {coverart_url}")
        response = requests.head(coverart_url, timeout=30, allow_redirects=False)
        
        if response.status_code in (301, 302, 307, 308):
            location = response.headers.get('Location')
            if location:
                logger.info(f"Redirect resolved to: {location}")
                return location
            else:
                raise ValueError("No redirect location found")
        elif response.status_code == 404:
            raise FileNotFoundError("Cover art not found")
        else:
            raise RuntimeError(f"Unexpected response from coverartarchive.org: {response.status_code}")
            
    except requests.exceptions.RequestException as e:
        logger.error(f"Error resolving redirect for {coverart_url}: {e}")
        raise ConnectionError("Failed to connect to coverartarchive.org")

def validate_mbid(mbid: str) -> bool:
    """Validate that the MBID format is correct."""
    return bool(MBID_PATTERN.match(mbid))

def get_content_type(file_path: Path) -> str:
    """Get content type based on file extension."""
    suffix = file_path.suffix.lower()
    if suffix == '.jpg' or suffix == '.jpeg':
        return 'image/jpeg'
    elif suffix == '.png':
        return 'image/png'
    elif suffix == '.gif':
        return 'image/gif'
    elif suffix == '.webp':
        return 'image/webp'
    else:
        return 'application/octet-stream'

@app.route("/health")
def health_check():
    """Health check endpoint."""
    logger.debug("Health check endpoint called")
    return jsonify({"status": "healthy", "cache_dir": str(CACHE_DIR)})

@app.route("/cache-status")
def cache_status():
    """Get cache status information using the efficient cache index."""
    try:
        # Get data from cache index
        cached_files = cache_index.get_item_count()
        cache_size = cache_index.get_total_bytes()
        
        # Get breakdown by cache type
        release_items = len(cache_index.get_items_by_type("release"))
        release_group_items = len(cache_index.get_items_by_type("release-group"))
        
        return jsonify({
            "status": "running",
            "cached_files": cached_files,
            "release_files": release_items,
            "release_group_files": release_group_items,
            "cache_size_bytes": cache_size,
            "cache_size_mb": round(cache_size / 1024 / 1024, 2),
            "cache_limit_mb": round(MAX_CACHE_SIZE / 1024 / 1024, 2),
            "cache_usage_percent": round((cache_size / MAX_CACHE_SIZE) * 100, 1) if MAX_CACHE_SIZE > 0 else 0,
            "cache_dir": str(CACHE_DIR),
            "cache_index_type": "redis",
            "redis_available": True
        })
    except Exception as e:
        logger.error(f"Error getting cache status: {e}")
        return jsonify({"status": "error", "error": str(e)}), 500

def handle_coverart_request(mbid: str, path: str = "", size: str = ""):
    """Handle cover art requests with caching."""
    
    # Validate MBID
    if not validate_mbid(mbid):
        return jsonify({"error": "Invalid MBID format"}), 400
    
    # Build the coverartarchive.org URL
    if path:
        if size:
            coverart_url = f"{COVERART_BASE_URL}/release/{mbid}/{path}-{size}"
        else:
            coverart_url = f"{COVERART_BASE_URL}/release/{mbid}/{path}"
    else:
        coverart_url = f"{COVERART_BASE_URL}/release/{mbid}/"
    
    # Generate cache key based on MBID and path components
    cache_key = generate_cache_key(mbid, path, size)
    
    # Check if we already have this image cached
    # Try common image extensions
    for ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']:
        cache_path = get_cache_path("release", cache_key, ext)
        if cache_path.exists():
            logger.info(f"Cache hit for {coverart_url}")
            # Use X-Accel-Redirect for efficient nginx file serving
            cache_file = f"/cache-files/{cache_path.relative_to(CACHE_DIR)}"
            headers = {
                "X-Accel-Redirect": cache_file,
                "Content-Type": get_content_type(cache_path),
                "Cache-Control": "public, max-age=31536000",  # 1 year
                "X-Cache-Status": "HIT"
            }
            return Response("", headers=headers)
    
    # Cache miss - need to download the image
    logger.info(f"Cache miss for {coverart_url}")
    
    try:
        # Resolve the redirect to get actual image URL
        image_url = resolve_redirect(coverart_url)
        
        # Determine file extension from the image URL
        parsed_url = urlparse(image_url)
        path_parts = Path(parsed_url.path)
        extension = path_parts.suffix or '.jpg'  # Default to .jpg if no extension
        
        cache_path = get_cache_path("release", cache_key, extension)
        
        # Download and cache the image
        success = download_image(image_url, cache_path, "release", mbid, cache_key)
        
        if success:
            # Use X-Accel-Redirect for efficient nginx file serving
            cache_file = f"/cache-files/{cache_path.relative_to(CACHE_DIR)}"
            headers = {
                "X-Accel-Redirect": cache_file,
                "Content-Type": get_content_type(cache_path),
                "Cache-Control": "public, max-age=31536000",  # 1 year
                "X-Cache-Status": "MISS"
            }
            return Response("", headers=headers)
        else:
            return jsonify({"error": "Failed to download image"}), 502
            
    except FileNotFoundError:
        return jsonify({"error": "Cover art not found"}), 404
    except ConnectionError:
        return jsonify({"error": "Failed to connect to coverartarchive.org"}), 502
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return jsonify({"error": "Internal server error"}), 500

# Release endpoints
@app.route("/release/<mbid>/")
def get_release_all(mbid):
    """Get all cover art for a release."""
    return handle_coverart_request(mbid)

@app.route("/release/<mbid>/front")
def get_release_front(mbid):
    """Get front cover art for a release."""
    return handle_coverart_request(mbid, "front")

@app.route("/release/<mbid>/back")
def get_release_back(mbid):
    """Get back cover art for a release."""
    return handle_coverart_request(mbid, "back")

@app.route("/release/<mbid>/<image_id>")
def get_release_by_id(mbid, image_id):
    """Get specific cover art by ID for a release."""
    return handle_coverart_request(mbid, image_id)

@app.route("/release/<mbid>/front-<size>")
def get_release_front_sized(mbid, size):
    """Get front cover art with specific size for a release."""
    if size not in ["250", "500", "1200"]:
        return jsonify({"error": "Invalid size. Must be 250, 500, or 1200"}), 400
    return handle_coverart_request(mbid, "front", size)

@app.route("/release/<mbid>/back-<size>")
def get_release_back_sized(mbid, size):
    """Get back cover art with specific size for a release."""
    if size not in ["250", "500", "1200"]:
        return jsonify({"error": "Invalid size. Must be 250, 500, or 1200"}), 400
    return handle_coverart_request(mbid, "back", size)

@app.route("/release/<mbid>/<image_id>-<size>")
def get_release_by_id_sized(mbid, image_id, size):
    """Get specific cover art by ID with specific size for a release."""
    if size not in ["250", "500", "1200"]:
        return jsonify({"error": "Invalid size. Must be 250, 500, or 1200"}), 400
    return handle_coverart_request(mbid, image_id, size)

# Release-group endpoints
@app.route("/release-group/<mbid>/")
def get_release_group_all(mbid):
    """Get all cover art for a release group."""
    return handle_coverart_request_rg(mbid)

@app.route("/release-group/<mbid>/front")
def get_release_group_front(mbid):
    """Get front cover art for a release group."""
    return handle_coverart_request_rg(mbid, "front")

@app.route("/release-group/<mbid>/front-<size>")
def get_release_group_front_sized(mbid, size):
    """Get front cover art with specific size for a release group."""
    if size not in ["250", "500", "1200"]:
        return jsonify({"error": "Invalid size. Must be 250, 500, or 1200"}), 400
    return handle_coverart_request_rg(mbid, "front", size)

def handle_coverart_request_rg(mbid: str, path: str = "", size: str = ""):
    """Handle cover art requests for release groups with caching."""
    
    # Validate MBID
    if not validate_mbid(mbid):
        return jsonify({"error": "Invalid MBID format"}), 400
    
    # Build the coverartarchive.org URL for release groups
    if path:
        if size:
            coverart_url = f"{COVERART_BASE_URL}/release-group/{mbid}/{path}-{size}"
        else:
            coverart_url = f"{COVERART_BASE_URL}/release-group/{mbid}/{path}"
    else:
        coverart_url = f"{COVERART_BASE_URL}/release-group/{mbid}/"
    
    # Generate cache key based on MBID and path components
    cache_key = generate_cache_key(mbid, path, size)
    
    # Check if we already have this image cached
    # Try common image extensions
    for ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']:
        cache_path = get_cache_path("release-group", cache_key, ext)
        if cache_path.exists():
            logger.info(f"Cache hit for {coverart_url}")
            # Use X-Accel-Redirect for efficient nginx file serving
            cache_file = f"/cache-files/{cache_path.relative_to(CACHE_DIR)}"
            headers = {
                "X-Accel-Redirect": cache_file,
                "Content-Type": get_content_type(cache_path),
                "Cache-Control": "public, max-age=31536000",  # 1 year
                "X-Cache-Status": "HIT"
            }
            return Response("", headers=headers)
    
    # Cache miss - need to download the image
    logger.info(f"Cache miss for {coverart_url}")
    
    try:
        # Resolve the redirect to get actual image URL
        image_url = resolve_redirect(coverart_url)
        
        # Determine file extension from the image URL
        parsed_url = urlparse(image_url)
        path_parts = Path(parsed_url.path)
        extension = path_parts.suffix or '.jpg'  # Default to .jpg if no extension
        
        cache_path = get_cache_path("release-group", cache_key, extension)
        
        # Download and cache the image
        success = download_image(image_url, cache_path, "release-group", mbid, cache_key)
        
        if success:
            # Use X-Accel-Redirect for efficient nginx file serving
            cache_file = f"/cache-files/{cache_path.relative_to(CACHE_DIR)}"
            headers = {
                "X-Accel-Redirect": cache_file,
                "Content-Type": get_content_type(cache_path),
                "Cache-Control": "public, max-age=31536000",  # 1 year
                "X-Cache-Status": "MISS"
            }
            return Response("", headers=headers)
        else:
            return jsonify({"error": "Failed to download image"}), 502
            
    except FileNotFoundError:
        return jsonify({"error": "Cover art not found"}), 404
    except ConnectionError:
        return jsonify({"error": "Failed to connect to coverartarchive.org"}), 502
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return jsonify({"error": "Internal server error"}), 500

# Initialize cache index at startup
logger.info("Cover Art Cache Service starting up...")
scan_cache_at_startup()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)