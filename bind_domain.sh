#!/bin/bash
# Run this AFTER DNS records are in place and propagated.
# Binds kdesk.kramerav.com to the App Service and provisions a free managed TLS cert.
set -e

AZ="powershell.exe -Command '& '"'"'C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin\az.cmd'"'"
RG="kdesk-prod"
APP="kdesk-web"
HOSTNAME="kdesk.kramerav.com"

echo "==> Binding custom hostname..."
"$AZ" webapp config hostname add --resource-group $RG --webapp-name $APP --hostname $HOSTNAME

echo "==> Creating managed TLS certificate..."
THUMBPRINT=$("$AZ" webapp config ssl create --resource-group $RG --name $APP --hostname $HOSTNAME --query thumbprint --output tsv)

echo "==> Binding TLS certificate..."
"$AZ" webapp config ssl bind --resource-group $RG --name $APP --ssl-type SNI --certificate-thumbprint "$THUMBPRINT"

echo ""
echo "Done. kdesk is live at https://$HOSTNAME"
