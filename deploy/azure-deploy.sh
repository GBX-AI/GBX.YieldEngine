#!/bin/bash
# Manual deploy script for YieldEngine
# Usage: bash deploy/azure-deploy.sh [TAG]
# Default tag: latest

set -e

TAG=${1:-latest}
RESOURCE_GROUP="YieldEngine"
BACKEND_IMAGE="ghcr.io/gbx-ai/yieldengine-backend"
FRONTEND_IMAGE="ghcr.io/gbx-ai/yieldengine-frontend"

echo "Deploying with tag: $TAG"

echo "Updating backend..."
az containerapp update \
  --name yield-engine-api \
  --resource-group $RESOURCE_GROUP \
  --image "$BACKEND_IMAGE:$TAG"

echo "Updating frontend..."
az containerapp update \
  --name yield-engine-web \
  --resource-group $RESOURCE_GROUP \
  --image "$FRONTEND_IMAGE:$TAG"

echo ""
echo "Deployment complete."
echo "Backend:  https://yield-engine-api.whiteocean-b818a22a.centralindia.azurecontainerapps.io"
echo "Frontend: https://yield-engine-web.whiteocean-b818a22a.centralindia.azurecontainerapps.io"
