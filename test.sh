#!/bin/bash

# Test script for Cover Art Cache Service

BASE_URL="http://localhost:8080"
TEST_DATA_FILE="test_data.json"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Check if test data file exists
if [ ! -f "$TEST_DATA_FILE" ]; then
    echo -e "${RED}Error: $TEST_DATA_FILE not found${NC}"
    exit 1
fi

# Check if jq is available
if ! command -v jq &> /dev/null; then
    echo -e "${RED}Error: jq is required but not installed${NC}"
    exit 1
fi

echo "Testing Cover Art Cache Service..."
echo "================================="
echo -e "Using test data from: ${BLUE}$TEST_DATA_FILE${NC}"

# Test health endpoint
echo -e "\n${YELLOW}Testing health endpoint...${NC}"
response=$(curl -s -o /dev/null -w "%{http_code}" "$BASE_URL/health")
if [ "$response" = "200" ]; then
    echo -e "${GREEN}✓ Health check passed${NC}"
else
    echo -e "${RED}✗ Health check failed (HTTP $response)${NC}"
fi

# Test cache status
echo -e "\n${YELLOW}Testing cache status endpoint...${NC}"
response=$(curl -s -o /dev/null -w "%{http_code}" "$BASE_URL/cache-status")
if [ "$response" = "200" ]; then
    echo -e "${GREEN}✓ Cache status accessible${NC}"
    curl -s "$BASE_URL/cache-status"
else
    echo -e "${RED}✗ Cache status failed (HTTP $response)${NC}"
fi

# Function to test a single MBID
test_release() {
    local mbid=$1
    local expected_code=$2
    local type=${3:-"release"}
    
    echo -e "\n${BLUE}Testing $type: $mbid (expecting HTTP $expected_code)${NC}"
    
    # Test front cover
    echo -e "Testing: $BASE_URL/$type/$mbid/front"
    response=$(curl -s -D - -o /dev/null "$BASE_URL/$type/$mbid/front" | grep -E "(HTTP/|Content-Type:|X-Cache-Status:)")
    http_code=$(echo "$response" | head -1 | cut -d' ' -f2)
    content_type=$(echo "$response" | grep "Content-Type:" | cut -d' ' -f2 || echo "none")
    cache_status=$(echo "$response" | grep "X-Cache-Status:" | cut -d' ' -f2 || echo "none")

    if [ "$http_code" = "$expected_code" ]; then
        echo -e "${GREEN}✓ Front cover returned expected HTTP $http_code${NC}"
        if [ "$http_code" = "200" ]; then
            echo -e "  Content-Type: $content_type"
            echo -e "  Cache Status: $cache_status"
            
            # Test if we get an actual image by checking content type
            if [[ "$content_type" == image/* ]]; then
                echo -e "${GREEN}✓ Response is an image${NC}"
            else
                echo -e "${RED}✗ Response is not an image (Content-Type: $content_type)${NC}"
            fi
        fi
    else
        echo -e "${RED}✗ Front cover failed - expected HTTP $expected_code, got $http_code${NC}"
    fi
    
    # If successful, test cache hit (second request should be faster)
    if [ "$http_code" = "200" ]; then
        echo -e "Testing cache hit (second request)..."
        start_time=$(date +%s%N)
        response=$(curl -s -D - -o /dev/null "$BASE_URL/$type/$mbid/front" | grep -E "(HTTP/|X-Cache-Status:)")
        end_time=$(date +%s%N)
        duration=$(( (end_time - start_time) / 1000000 ))  # Convert to milliseconds

        http_code=$(echo "$response" | head -1 | cut -d' ' -f2)
        cache_status=$(echo "$response" | grep "X-Cache-Status:" | cut -d' ' -f2 || echo "none")

        if [ "$http_code" = "200" ]; then
            echo -e "${GREEN}✓ Second request successful (${duration}ms)${NC}"
            echo -e "  Cache Status: $cache_status"
            
            if [ "$cache_status" = "HIT" ]; then
                echo -e "${GREEN}✓ Cache hit confirmed${NC}"
            else
                echo -e "${YELLOW}! Cache status: $cache_status${NC}"
            fi
        fi
    fi
}

# Test releases from test data
echo -e "\n${YELLOW}Testing Release MBIDs...${NC}"
jq -r '.release | to_entries[] | "\(.key):\(.value)"' "$TEST_DATA_FILE" | while IFS=: read -r mbid expected_code; do
    if [[ "$mbid" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]]; then
        test_release "$mbid" "$expected_code" "release"
    fi
done

# Test release groups from test data
echo -e "\n${YELLOW}Testing Release Group MBIDs...${NC}"
jq -r '.["release-group"] | to_entries[] | "\(.key):\(.value)"' "$TEST_DATA_FILE" | while IFS=: read -r mbid expected_code; do
    if [[ "$mbid" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]]; then
        test_release "$mbid" "$expected_code" "release-group"
    fi
done

# Test invalid MBID
echo -e "\n${YELLOW}Testing invalid MBID...${NC}"
response=$(curl -s -o /dev/null -w "%{http_code}" "$BASE_URL/release/invalid-mbid/front")
if [ "$response" = "404" ]; then
    echo -e "${GREEN}✓ Invalid MBID correctly rejected${NC}"
else
    echo -e "${RED}✗ Invalid MBID not handled correctly (HTTP $response)${NC}"
fi

# Test sized covers with a successful MBID
echo -e "\n${YELLOW}Testing sized covers...${NC}"
# Get the first 200 status release from test data
first_success_mbid=$(jq -r '.release | to_entries[] | select(.value == 200) | .key' "$TEST_DATA_FILE" | head -n 1)

if [ -n "$first_success_mbid" ]; then
    echo -e "Using MBID: $first_success_mbid"
    
    # Test 250px front cover
    echo -e "Testing: $BASE_URL/release/$first_success_mbid/front-250"
    response=$(curl -s -D - -o /dev/null "$BASE_URL/release/$first_success_mbid/front-250" | grep -E "(HTTP/|Content-Type:|X-Cache-Status:)")
    http_code=$(echo "$response" | head -1 | cut -d' ' -f2)
    content_type=$(echo "$response" | grep "Content-Type:" | cut -d' ' -f2 || echo "none")
    cache_status=$(echo "$response" | grep "X-Cache-Status:" | cut -d' ' -f2 || echo "none")

    if [ "$http_code" = "200" ]; then
        echo -e "${GREEN}✓ 250px front cover returned successfully${NC}"
        echo -e "  Content-Type: $content_type"
        echo -e "  Cache Status: $cache_status"
        
        if [[ "$content_type" == image/* ]]; then
            echo -e "${GREEN}✓ Response is an image${NC}"
        else
            echo -e "${RED}✗ Response is not an image (Content-Type: $content_type)${NC}"
        fi
    else
        echo -e "${YELLOW}! 250px front cover returned HTTP $http_code${NC}"
    fi
else
    echo -e "${RED}✗ No successful MBIDs found in test data${NC}"
fi

echo -e "\n${YELLOW}Testing complete!${NC}"
echo "Note: First requests may be slower as images are downloaded and cached."

# Summary
echo -e "\n${BLUE}Cache Directory Structure:${NC}"
echo -e "Optimized cache structure with deep MBID-based subdirectories:"
echo -e "  /var/cache/nginx/images/release/X/XX/XXX/     - Release cover art"
echo -e "  /var/cache/nginx/images/release-group/X/XX/XXX/ - Release group cover art"
echo -e "  Example: MBID ebe78b00-... → /var/cache/nginx/images/release/e/eb/ebe/"
