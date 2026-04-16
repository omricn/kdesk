#!/bin/bash
# Kdesk — deploy to Azure
# Usage: bash deploy.sh
set -e

REGISTRY="kdeskregistry.azurecr.io"
IMAGE="$REGISTRY/kdesk:latest"
RG="kdesk-prod"

echo "==> Logging in to Azure Container Registry..."
az acr login --name kdeskregistry

echo "==> Building Docker image..."
docker build -t "$IMAGE" .

echo "==> Pushing image to registry..."
docker push "$IMAGE"

echo "==> Restarting web..."
az webapp restart --resource-group "$RG" --name kdesk-web

echo "==> Restarting celery worker..."
az webapp restart --resource-group "$RG" --name kdesk-celery

echo "==> Restarting celery beat..."
az webapp restart --resource-group "$RG" --name kdesk-celery-beat

echo ""
echo "Done. kdesk is live at https://kdesk.kramerav.com"
