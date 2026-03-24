#!/bin/bash
# One-time Azure setup for YieldEngine
# Prerequisites: az login, GHCR_PAT env var set
#
# Azure resources already exist:
#   Resource Group: YieldEngine (Central India)
#   Container Apps: yield-engine-api, yield-engine-web
#   Environment: yield-engine-env

set -e

RESOURCE_GROUP="YieldEngine"
GHCR_PAT="${GHCR_PAT:?Set GHCR_PAT environment variable}"

echo "=== Switching Container Apps from ACR to GHCR ==="

# Remove old ACR registry
echo "Removing old ACR registry..."
az containerapp registry remove \
  --name yield-engine-api \
  --resource-group $RESOURCE_GROUP \
  --server ca3f6149c21eacr.azurecr.io 2>/dev/null || true

az containerapp registry remove \
  --name yield-engine-web \
  --resource-group $RESOURCE_GROUP \
  --server ca3f6149c21eacr.azurecr.io 2>/dev/null || true

# Set GHCR registry
echo "Setting GHCR registry on backend..."
az containerapp registry set \
  --name yield-engine-api \
  --resource-group $RESOURCE_GROUP \
  --server ghcr.io \
  --username gbx-ai \
  --password "$GHCR_PAT"

echo "Setting GHCR registry on frontend..."
az containerapp registry set \
  --name yield-engine-web \
  --resource-group $RESOURCE_GROUP \
  --server ghcr.io \
  --username gbx-ai \
  --password "$GHCR_PAT"

echo ""
echo "=== Done ==="
echo "Backend:  https://yield-engine-api.whiteocean-b818a22a.centralindia.azurecontainerapps.io"
echo "Frontend: https://yield-engine-web.whiteocean-b818a22a.centralindia.azurecontainerapps.io"
echo ""
echo "Next steps:"
echo "  1. Set GitHub secrets on GBX-AI/GBX.YieldEngine:"
echo "     - AZURE_CREDENTIALS (service principal JSON)"
echo "     - GHCR_PAT (GitHub PAT with read:packages)"
echo "     - BACKEND_FQDN (yield-engine-api.whiteocean-b818a22a.centralindia.azurecontainerapps.io)"
echo "  2. Push to main to trigger deployment"
