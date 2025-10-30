#!/usr/bin/env python3
"""
Cover Art Cache Service

A Python service that resolves coverartarchive.org redirects and caches actual images locally,
working with nginx for efficient file serving.
"""

import json
from datetime import datetime
import logging
from pathlib import Path
import sys
import time
from urllib.parse import urlparse

from flask import Flask, Response, request, jsonify
import redis
import requests

#TODO: Check to see about bitmmap's concern: what happens if two processes try to download/cache the same file concurrently. 


from .cache import (
    CACHE_DIR, MAX_CACHE_SIZE, COVERART_BASE_URL, MBID_PATTERN,
    REDIS_CACHE_ITEMS_KEY, REDIS_TOTAL_BYTES_KEY,
    CACHE_TYPE_RELEASE, CACHE_TYPE_RELEASE_GROUP,
    validate_mbid, get_cache_subdir, format_bytes_mb, get_cache_usage_percent, DistributedCacheIndex, CacheItem,
    startup_cache_scan, check_cache_directory_writable
)

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

# Check cache directory exists and is writable
logger.info("Checking cache directory...")
if not check_cache_directory_writable():
    logger.error(f"Cache directory {CACHE_DIR} is not writable - service cannot start")
    sys.exit(1)

# Redis connection for distributed cache index
redis_client = redis.Redis(host='redis', port=6379, db=0, decode_responses=True)
redis_client.ping()
logger.info("Connected to Redis for distributed cache index")

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
        
        # Use shared function for cache subdirectory
        cache_subdir = get_cache_subdir(cache_type, mbid)
        cache_subdir.mkdir(parents=True, exist_ok=True)
        return cache_subdir / f"{cache_key}{extension}"
    except PermissionError as e:
        logger.error(f"Permission denied creating cache directory {cache_subdir}: {e}")
        raise
    except Exception as e:
        logger.error(f"Error creating cache path for {cache_type}/{cache_key}: {e}")
        raise



def get_cache_size() -> int:
    """Get the current cache size in bytes from the index."""
    return cache_index.get_total_bytes()

# Note: Cache cleanup is now handled by a separate cleanup service
# to avoid race conditions in multi-process environments

def download_image(url: str, cache_path: Path, cache_type: str, mbid: str, cache_key: str) -> bool:
    """Download an image from URL and save it to cache_path, adding to cache index."""
    try:
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
        
        cache_item = CacheItem(
            cache_type=cache_type,
            mbid=mbid,
            cache_key=cache_key,
            file_path=cache_path,
            downloaded_at=time.time(),
            size_bytes=downloaded_bytes
        )
        cache_index.add_item(cache_item)
        
        return True
        
    except Exception as e:
        logger.error(f"Error downloading image from {url}: {e}")
        cache_path.unlink(missing_ok=True)
        return False

def resolve_redirect(coverart_url: str) -> str:
    """Resolve the redirect from coverartarchive.org to get the actual image URL."""
    try:
        response = requests.head(coverart_url, timeout=30, allow_redirects=False)
        
        if response.status_code in (301, 302, 307, 308):
            location = response.headers.get('Location')
            if location:
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
            "cache_size_mb": format_bytes_mb(cache_size),
            "cache_limit_mb": format_bytes_mb(MAX_CACHE_SIZE),
            "cache_usage_percent": get_cache_usage_percent(cache_size),
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
            #logger.info(f"Cache hit for {coverart_url}")
            # Use X-Accel-Redirect for efficient nginx file serving
            headers = {
                "X-Accel-Redirect": "/cache/" + cache_path.relative_to(CACHE_DIR),
                "Content-Type": get_content_type(cache_path),
                "Cache-Control": "public, max-age=31536000",  # 1 year
                "X-Cache-Status": "HIT"
            }
            return Response("", headers=headers)
    
    # Cache miss - need to download the image
    #logger.info(f"Cache miss for {coverart_url}")
    
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
            temp = "/cache/" + cache_path.relative_to(CACHE_DIR)
            print(temp)
            headers = {
                "X-Accel-Redirect": "/cache/" + cache_path.relative_to(CACHE_DIR),
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

@app.route("/")
def index():
    return "bruh!"

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
            #logger.info(f"Cache hit for {coverart_url}")
            # Use X-Accel-Redirect for efficient nginx file serving
            headers = {
                "X-Accel-Redirect": "/cache/" + cache_path.relative_to(CACHE_DIR),
                "Content-Type": get_content_type(cache_path),
                "Cache-Control": "public, max-age=31536000",  # 1 year
                "X-Cache-Status": "HIT"
            }
            return Response("", headers=headers)
    
    # Cache miss - need to download the image
    #logger.info(f"Cache miss for {coverart_url}")
    
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
            headers = {
                "X-Accel-Redirect": "/cache/" + cache_path.relative_to(CACHE_DIR),
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

# Check if cache index exists, scan if needed
logger.info("Checking cache index in Redis...")
scan_success = startup_cache_scan(redis_client)
if not scan_success:
    logger.error("Failed to initialize cache index - service cannot start")
    sys.exit(1)

logger.info("Cache index ready - service starting...")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
