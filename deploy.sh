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

echo "==> Restarting web (pulls latest image)..."
az webapp config container set --resource-group "$RG" --name kdesk-web --docker-custom-image-name "$IMAGE" > /dev/null
az webapp restart --resource-group "$RG" --name kdesk-web

echo "==> Restarting celery worker..."
az webapp config container set --resource-group "$RG" --name kdesk-celery --docker-custom-image-name "$IMAGE" > /dev/null
az webapp restart --resource-group "$RG" --name kdesk-celery

echo "==> Restarting celery beat..."
az webapp config container set --resource-group "$RG" --name kdesk-celery-beat --docker-custom-image-name "$IMAGE" > /dev/null
az webapp restart --resource-group "$RG" --name kdesk-celery-beat

echo "==> Verifying all services are Running..."
sleep 30
ALL_OK=true
for APP in kdesk-web kdesk-celery kdesk-celery-beat; do
  STATE=$(az webapp show --resource-group "$RG" --name "$APP" --query "state" -o tsv 2>/dev/null)
  if [ "$STATE" != "Running" ]; then
    echo "  WARNING: $APP is $STATE — starting it..."
    az webapp start --resource-group "$RG" --name "$APP"
    sleep 10
    STATE=$(az webapp show --resource-group "$RG" --name "$APP" --query "state" -o tsv 2>/dev/null)
    echo "  $APP is now: $STATE"
    ALL_OK=false
  else
    echo "  $APP: Running"
  fi
done
if [ "$ALL_OK" = false ]; then
  echo "  One or more services needed a manual start — check logs."
fi

echo ""
echo "Done. kdesk is live at https://kdesk.kramerav.com"
