# CI/CD setup — auto-deploy on merge to `main`

`.github/workflows/deploy.yml` builds the Docker image, pushes it to Azure
Container Registry, and restarts the three Web Apps **on every merge to `main`** —
the automated replacement for running `bash deploy.sh` from a workstation. This
is the piece that makes "approve + merge a fix from your phone → it ships" work,
and removes the "Docker Desktop must be running on my laptop" fragility.

## One-time setup (must be done by a human with Azure + GitHub admin)

The workflow authenticates to Azure with a **service principal** stored as a
single GitHub repo secret. `deploy.sh` keeps working as a manual fallback until
this is in place, so there's no rush and no risk.

### 1. Create a service principal scoped to the resource group

Run once (as someone with Owner/UAA on the `kdesk-prod` resource group):

```bash
# Contributor on the resource group (build/restart the web apps)
az ad sp create-for-rbac \
  --name "kdesk-github-deploy" \
  --role Contributor \
  --scopes /subscriptions/<SUB_ID>/resourceGroups/kdesk-prod \
  --sdk-auth
```

Copy the entire JSON blob it prints (it looks like `{"clientId": "...", "clientSecret": "...", ...}`).

Then grant that same principal push rights on the registry:

```bash
# AcrPush lets it push the built image
az role assignment create \
  --assignee <clientId-from-the-JSON> \
  --role AcrPush \
  --scope $(az acr show --name kdeskregistry --query id -o tsv)
```

### 2. Add the secret to GitHub

Repo → **Settings → Secrets and variables → Actions → New repository secret**:
- **Name:** `AZURE_CREDENTIALS`
- **Value:** the full JSON blob from step 1

(Registry name, resource group, and app names are non-secret and live in the workflow's `env:` block.)

### 3. (Recommended) Protect `main` so deploys go through review

Repo → **Settings → Branches → Add rule** for `main`: require a pull request
before merging, and require 1 approval. Then the flow is: agent/you open a PR →
you approve + merge from the GitHub mobile app → this workflow deploys. Without
the rule, a direct push to `main` also deploys — still fine, just less gated.

## How it runs

- **Trigger:** any push/merge to `main`, or a manual run from the Actions tab
  (also available in the GitHub mobile app under the repo's Actions).
- **Steps:** Azure login → ACR login → `docker build` → `docker push` →
  `az webapp config container set` + `restart` for `kdesk-web`, `kdesk-celery`,
  `kdesk-celery-beat` → verify each is `Running`.
- **Migrations / periodic tasks** still apply on container start via
  `start_web.sh` (`migrate` + `register_periodic_tasks`), exactly as with `deploy.sh`.

## Cutover notes

- The workflow only becomes active once it's on `main` (push-triggered workflows
  run from the default branch). **Set up `AZURE_CREDENTIALS` before merging** the
  branch that introduces this file — the merge that lands it will itself trigger
  the first automated deploy.
- If the secret isn't set yet, the workflow run fails harmlessly (nothing
  deploys) and `deploy.sh` remains available as the manual path.
- `deploy.sh` can stay in the repo as a break-glass manual deploy.

## Rollback

Every deploy tags the image with the commit SHA (`kdeskregistry.azurecr.io/kdesk:<sha>`)
in addition to `latest`. To roll back to a previous release, point the apps at the
prior SHA and restart them:

```bash
az webapp config container set -g kdesk-prod -n kdesk-web \
  --container-image-name kdeskregistry.azurecr.io/kdesk:<previous-sha> > /dev/null
az webapp restart -g kdesk-prod -n kdesk-web
# repeat for kdesk-celery and kdesk-celery-beat
```

Find the SHA to roll back to from the git history on `main` (each merge commit's
SHA is the tag). The image for that SHA remains in ACR, so no rebuild is needed.
