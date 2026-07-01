#!/bin/bash
# Script to build and push Podman image to registry
#
# Usage: ./build-and-push.sh <version>
# Example: ./build-and-push.sh 1.0

set -euo pipefail

REGISTRY="zot.jo6r.xyz"
IMAGE_NAME="kaufland/jirimodels-offer-stock-updater"
REGISTRY_USER="zot"

if [ -z "${1:-}" ]; then
	echo "Error: Version is required"
	echo "Usage: $0 <version>"
	echo "Example: $0 1.0"
	exit 1
fi

VERSION="$1"
FULL_IMAGE_NAME="${REGISTRY}/${IMAGE_NAME}:${VERSION}"

if [ -z "${REGISTRY_PASSWORD:-}" ]; then
	echo -n "Enter registry password for ${REGISTRY_USER}@${REGISTRY}: "
	read -rs REGISTRY_PASSWORD
	echo ""
else
	echo "Using REGISTRY_PASSWORD from environment"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "Building Podman image..."
echo "Registry: ${REGISTRY}"
echo "Image: ${FULL_IMAGE_NAME}"
echo "Project root: ${PROJECT_ROOT}"

podman build --network=host \
	-f "${SCRIPT_DIR}/Dockerfile" \
	-t "${IMAGE_NAME}:${VERSION}" \
	-t "${FULL_IMAGE_NAME}" \
	"${PROJECT_ROOT}"

echo ""
echo "Image built successfully"
echo ""

echo "Logging in to registry ${REGISTRY}..."
echo "${REGISTRY_PASSWORD}" | podman login "${REGISTRY}" --username "${REGISTRY_USER}" --password-stdin

echo "Pushing to registry ${REGISTRY}..."
podman push "${FULL_IMAGE_NAME}"

echo ""
echo "Image pushed successfully to ${REGISTRY}"
echo "  ${FULL_IMAGE_NAME}"