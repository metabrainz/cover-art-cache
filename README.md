# Cover Art Cache Service

An easily deployable caching proxy service for MusicBrainz Cover Art Archive that downloads and serves actual image files instead of redirects, built with Python Flask and nginx. Build for use in Docker.

## Architecture

This service uses a multi-container architecture for optimal performance and reliability:

- Python Flask Application: Handles redirect resolution, image downloading, and cache management
- nginx Reverse Proxy: Provides efficient static file serving using X-Accel-Redirect
- Redis: Distributed cache index for coordination across multiple processes
- Cache Cleaner Service: Dedicated service for maintaining optimal cache size

## How It Works

1. **Request**: Client requests cover art via nginx
2. **Cache Check**: Python app checks Redis cache index for existing file
3. **Cache Hit**: nginx serves cached image directly using X-Accel-Redirect (super fast)
4. **Cache Miss**: Python app resolves redirect from coverartarchive.org and downloads actual image
5. **Storage**: Image stored locally with deep directory structure, Redis index updated
6. **Response**: nginx serves the newly cached image file
7. **Cleanup**: Dedicated cleaner service monitors cache size and removes oldest files when needed

## Quick Start

1. **Configure and start**:
```bash
git clone <repository>
cd cover-art-cache

# Configure environment (edit .env file if needed)
sudo mkdir -p /opt/cover-art-cache
sudo chmod 777 /opt/cover-art-cache

# Start services
docker compose up -d
```

2. **Test the service**:
```bash
./test.sh
```

3. **Access cover art**:
```bash
curl -I http://localhost:8080/release/63b3a8ca-26f2-4e2b-b867-647a6ec2bebd/front
# Returns: HTTP/1.1 200 OK with actual image data
```

## API Endpoints

### Release Cover Art

- `GET /release/{mbid}/` - All cover art for a release
- `GET /release/{mbid}/front` - Front cover art
- `GET /release/{mbid}/back` - Back cover art  
- `GET /release/{mbid}/{image-id}` - Specific cover art by ID
- `GET /release/{mbid}/front-{size}` - Front cover at specific size (250, 500, 1200)
- `GET /release/{mbid}/back-{size}` - Back cover at specific size
- `GET /release/{mbid}/{image-id}-{size}` - Specific cover at specific size

### Release Group Cover Art

- `GET /release-group/{mbid}/` - All cover art for a release group  
- `GET /release-group/{mbid}/front` - Front cover art
- `GET /release-group/{mbid}/front-{size}` - Front cover at specific size

### Monitoring

- `GET /health` - Health check endpoint
- `GET /cache-status` - Cache statistics and status

## Response Headers

The service adds helpful headers to responses:

- `X-Cache-Status`: `HIT` (served from cache) or `MISS` (newly downloaded)
- `X-Served-By`: `nginx-cache` (indicates nginx file serving)
- `Content-Type`: Proper MIME type for the image format
- `Cache-Control`: Long-term caching headers for browsers
- `X-Content-Type-Options`: Security header

## Configuration

The service is configured using environment variables defined in a `.env` file:

```bash
# Cover Art Cache Configuration
COVER_ART_CACHE_DIR=/opt/cover-art-cache         # Cache directory on host filesystem
COVER_ART_CACHE_MAX_SIZE=100                     # Maximum cache size in MB
COVER_ART_CACHE_CLEANUP_INTERVAL=300             # Cleanup check interval in seconds
```

### Environment Variables

- `COVER_ART_CACHE_DIR`: Directory on host filesystem for cached images (default: `/opt/cover-art-cache`)
- `COVER_ART_CACHE_MAX_SIZE`: Maximum total cache size in MB (default: `100`)
- `COVER_ART_CACHE_CLEANUP_INTERVAL`: How often cleaner checks cache size in seconds (default: `300`)

### Setup

1. **Configure environment**:
```bash
# Edit .env file with your preferred settings
nano .env

# Create cache directory
sudo mkdir -p $COVER_ART_CACHE_DIR
sudo chmod 777 $COVER_ART_CACHE_DIR
```

2. **Start services**:
```bash
docker compose up -d
```

### Docker Volumes

- `nginx_cache`: nginx proxy cache
- `nginx_logs`: nginx access and error logs  
- `redis_data`: Redis persistent data storage
- `${COVER_ART_CACHE_DIR}`: Configurable local filesystem cache (bind mounted from host)

## Cache Management

### File System Cache

Cover art images are cached locally on the host filesystem at the location specified by `COVER_ART_CACHE_DIR` for easy inspection and management:

```bash
# View cached files
find $COVER_ART_CACHE_DIR -type f -exec ls -lh {} \;

# Check cache directory size  
du -sh $COVER_ART_CACHE_DIR

# Clear cache if needed (will be repopulated by Redis index)
sudo rm -rf $COVER_ART_CACHE_DIR/*
```

### Distributed Cache Index

The system uses Redis to maintain a distributed cache index that tracks:
- File locations and sizes
- Download timestamps
- Cache usage statistics
- Cleanup coordination across multiple processes

### Automatic Cleanup

The dedicated cleaner service:
- Monitors cache size every `COVER_ART_CACHE_CLEANUP_INTERVAL` seconds  
- Triggers cleanup when cache size exceeds the cleanup threshold `COVER_ART_CACHE_MAX_SIZE`
- Removes oldest files first until cache reaches the clean to threshold. 

The cache uses a deep directory structure (`$COVER_ART_CACHE_DIR/release/x/xx/xxx/filename.jpg`) for optimal filesystem performance.

### Customizing Cache Location

To use a different cache directory:

1. **Update .env file**:
```bash
COVER_ART_CACHE_DIR=/custom/cache/path
COVER_ART_CACHE_MAX_SIZE=200
COVER_ART_CACHE_CLEANUP_INTERVAL=600
```

2. **Create directory and restart**:
```bash
sudo mkdir -p /custom/cache/path
sudo chmod 777 /custom/cache/path
docker compose down
docker compose up -d
```

## Development

### Local Development

1. **Install dependencies**:
```bash
pip install -r requirements.txt
```

2. **Run the Python app**:
```bash
# Start Redis first
docker run -d --name redis -p 6379:6379 redis:7-alpine

# Run Flask app with uWSGI
uwsgi --ini uwsgi.ini
```

3. **Run nginx separately** or use Docker Compose for full stack testing.

### Testing

```bash
# Basic functionality test
./test.sh

# Load testing
./validate.sh

# Check specific endpoint
curl -v http://localhost:8080/release/76df3457-6cda-33eb-8e9a-044b5e15ffdd/front
```

### Monitoring

- **nginx logs**: `docker compose logs nginx`
- **Application logs**: `docker compose logs app`
- **Cleaner logs**: `docker compose logs cleaner`
- **Redis logs**: `docker compose logs redis`
- **Cache status**: `curl http://localhost:8080/cache-status`

### Health Checks

```bash
# Check service health
curl http://localhost:8080/health

# Check individual containers
docker compose ps
docker compose logs nginx
docker compose logs app
docker compose logs cleaner
docker compose logs redis
```

# AI Disclosure

Github's Copilot AI was used in part to create this project.

