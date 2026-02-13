#!/bin/bash
# Script to build and push Docker image to registry
#
# Usage: ./build-and-push.sh <version>
# Example: ./build-and-push.sh 1.0

set -e  # Exit on error

# Configuration
REGISTRY="zot.jo6r.xyz"
IMAGE_NAME="shoptet-unit-mapper"
REGISTRY_USER="zot"

# Check if version is provided
if [ -z "$1" ]; then
    echo "Error: Version is required"
    echo "Usage: $0 <version>"
    echo "Example: $0 1.0"
    exit 1
fi

VERSION="$1"
FULL_IMAGE_NAME="${REGISTRY}/${IMAGE_NAME}:${VERSION}"

# Check if registry password is set in environment
if [ -z "$REGISTRY_PASSWORD" ]; then
    # Prompt for registry password
    echo -n "Enter registry password for ${REGISTRY_USER}@${REGISTRY}: "
    read -s REGISTRY_PASSWORD
    echo ""
else
    echo "Using REGISTRY_PASSWORD from environment"
fi

# Get the project root directory (parent of services)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "Building Docker image..."
echo "Registry: ${REGISTRY}"
echo "Image: ${FULL_IMAGE_NAME}"
echo "Project root: ${PROJECT_ROOT}"

# Build the image
docker build \
    -f "${SCRIPT_DIR}/Dockerfile" \
    -t "${IMAGE_NAME}:${VERSION}" \
    -t "${FULL_IMAGE_NAME}" \
    "${PROJECT_ROOT}"

echo ""
echo "✓ Image built successfully"
echo ""

# Login to registry
echo "Logging in to registry ${REGISTRY}..."
echo "${REGISTRY_PASSWORD}" | docker login "${REGISTRY}" --username "${REGISTRY_USER}" --password-stdin

# Push to registry
echo "Pushing to registry ${REGISTRY}..."
docker push "${FULL_IMAGE_NAME}"

echo ""
echo "✓ Image pushed successfully to ${REGISTRY}"
echo "  ${FULL_IMAGE_NAME}"
