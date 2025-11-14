#!/usr/bin/env python3
"""
Cover Art Cache Service

A Python service that resolves coverartarchive.org redirects and caches actual images locally,
working with nginx for efficient file serving.
"""

import logging
from pathlib import Path
import sys
from urllib.parse import urlparse

from flask import Flask, Response, request, jsonify
import requests

#TODO: Check to see about bitmmap's concern: what happens if two processes try to download/cache the same file concurrently. 

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout,
    force=True
)

logger = logging.getLogger(__name__)

app = Flask(__name__)

# Configuration
CACHE_DIR = Path("/cover-art-cache")
COVERART_BASE_URL = "https://coverartarchive.org"


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


# Log startup
logger.info("Cover Art Cache Service starting up...")

# Add error handlers
@app.errorhandler(404)
def handle_not_found(e):
    return jsonify({"error": "Not found"}), 404

@app.errorhandler(500)
def handle_internal_error(e):
    logger.error(f"Internal server error: {e}")
    return jsonify({"error": "Internal server error"}), 500

@app.errorhandler(Exception)
def handle_exception(e):
    # Don't log 404 errors as exceptions
    if hasattr(e, 'code') and e.code == 404:
        return jsonify({"error": "Not found"}), 404
    logger.error(f"Unhandled exception: {e}", exc_info=True)
    return jsonify({"error": "Internal server error"}), 500

# Check cache directory exists and is writable
logger.info("Checking cache directory...")
if not check_cache_directory_writable():
    logger.error(f"Cache directory {CACHE_DIR} is not writable - service cannot start")
    sys.exit(1)

def get_cache_subdir(mbid: str) -> Path:
    """Generate cache subdirectory path for deep directory structure."""
    char1 = mbid[0]
    char2 = mbid[1:3]
    char3 = mbid[0:3]
    return CACHE_DIR / char1 / char2 / char3

def get_cache_path_for_url(url_path: str) -> Path:
    """Get the cache file path for a given URL path.
    
    Creates a deep subdirectory structure based on the first 3 characters of the MBID
    to distribute files and avoid having too many files in a single directory.
    
    Structure: X/XX/XXX/ where X, XX, XXX are progressive prefixes of MBID
    Example: URL path "release/abc12345-6789-..../front" creates: a/ab/abc/release_abc12345-6789-..._front
    """
    # Split the URL path to extract the MBID (second component after release/release-group)
    parts = url_path.strip('/').split('/')
    if len(parts) < 2 or not parts[1]:
        raise ValueError("Invalid URL path")
    
    mbid = parts[1]
    
    # Create a safe filename from the full path (replace / with _)
    safe_filename = url_path.strip('/').replace('/', '_')
    
    # Get the cache subdirectory path (don't create it yet)
    cache_subdir = get_cache_subdir(mbid)
    
    return cache_subdir / safe_filename

def download_item(url: str, cache_path: Path) -> bool:
    """Download an image from URL and save it to cache_path."""
    try:
        response = requests.get(url, stream=True, timeout=30)
        
        if response.status_code != 200:
            logger.warning(f"Failed to download image: HTTP {response.status_code}")
            return False
        
        # Ensure parent directory exists
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Write file
        with open(cache_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        
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
        elif response.status_code == 400:
            raise BadRequest(response.text)
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
    """Get cache status information."""
    try:
        return jsonify({
            "status": "running",
            "cache_dir": str(CACHE_DIR),
        })
    except Exception as e:
        logger.error(f"Error getting cache status: {e}")
        return jsonify({"status": "error", "error": str(e)}), 500

def handle_coverart_request(url_path: str):
    """Handle cover art requests with caching.
    
    Args:
        url_path: The full path including release/release-group and the rest
    """
    # Build the coverartarchive.org URL
    coverart_url = f"{COVERART_BASE_URL}/{url_path}"
    
    # Get cache path - try to find existing cached file with any extension
    base_cache_path = get_cache_path_for_url(url_path)
    
    # Check if we already have this cached (try common extensions)
    for ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.pdf', '']:
        cache_path = Path(str(base_cache_path) + ext)
        if cache_path.exists():
            # Use X-Accel-Redirect for efficient nginx file serving
            headers = {
                "X-Accel-Redirect": str(cache_path),
                "Content-Type": get_content_type(cache_path),
                "Cache-Control": "public, max-age=31536000",  # 1 year
                "X-Cache-Status": "HIT"
            }
            return Response("", headers=headers)
    
    # Cache miss - need to download the image
    try:
        # Resolve the redirect to get actual image URL
        image_url = resolve_redirect(coverart_url)
        
        # Determine file extension from the image URL
        parsed_url = urlparse(image_url)
        path_parts = Path(parsed_url.path)
        extension = path_parts.suffix or ''
        
        cache_path = Path(str(base_cache_path) + extension)
        
        # Download and cache the image
        success = download_item(image_url, cache_path)
        
        if success:
            # Use X-Accel-Redirect for efficient nginx file serving
            headers = {
                "X-Accel-Redirect": str(cache_path),
                "Content-Type": get_content_type(cache_path),
                "Cache-Control": "public, max-age=31536000",  # 1 year
                "X-Cache-Status": "MISS"
            }
            return Response("", headers=headers)
        else:
            return jsonify({"error": "Failed to download image"}), 502
            
    except FileNotFoundError:
        return jsonify({"error": "Cover art not found"}), 404
    except ValueError as e:
        # Handle bad request errors (400)
        return jsonify({"error": str(e)}), 400
    except ConnectionError:
        return jsonify({"error": "Failed to connect to coverartarchive.org"}), 502
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return jsonify({"error": "Internal server error"}), 500

@app.route("/")
def index():
    return "bruh!"

# Catch-all routes for release and release-group
@app.route("/release/<path:url_path>")
def get_release(url_path):
    """Handle all release cover art requests."""
    return handle_coverart_request(f"release/{url_path}")

@app.route("/release-group/<path:url_path>")
def get_release_group(url_path):
    """Handle all release-group cover art requests."""
    return handle_coverart_request(f"release-group/{url_path}")

logger.info("Cover Art Cache Service ready...")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
