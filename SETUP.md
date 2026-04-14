# Kdesk — Setup Guide

## Step 1: Azure App Registration (do this first)

You need to create an "App Registration" in your Azure portal so Kdesk can read emails
and sync users. This is a one-time setup.

1. Go to https://portal.azure.com → **Azure Active Directory** → **App registrations** → **New registration**
2. Name it **Kdesk**, leave everything else as default, click **Register**
3. Copy the **Application (client) ID** → this is your `AZURE_CLIENT_ID`
4. Copy the **Directory (tenant) ID** → this is your `AZURE_TENANT_ID`

### Create a client secret
5. In the app, go to **Certificates & secrets** → **New client secret**
6. Give it a description (e.g. "Kdesk") and set an expiry (24 months recommended)
7. Copy the **Value** immediately (it won't show again) → this is your `AZURE_CLIENT_SECRET`

### Add a redirect URI (for SSO login)
8. Go to **Authentication** → **Add a platform** → **Web**
9. Set the redirect URI to:
   - For local use: `http://localhost:8000/auth/callback/`
   - For production: `http://your-server-ip:8000/auth/callback/`
   - You can add both at the same time
10. Under **Implicit grant and hybrid flows**, check **ID tokens**
11. Click **Save**

### Grant API permissions
12. Go to **API permissions** → **Add a permission** → **Microsoft Graph**
13. First add **Delegated permissions** (used for SSO login):
    - `User.Read`          (read the signed-in user's profile)
    - `openid`, `profile`, `email` (standard SSO claims — may already be listed)
14. Then add **Application permissions** (used for background tasks):
    - `Mail.Read`            (read the servicedesk mailbox)
    - `Mail.Send`            (send notification emails)
    - `User.Read.All`        (read user profiles for sync)
    - `GroupMember.Read.All` (check IT group membership + sync KramerLicensedUsers)
15. Click **Grant admin consent for [your org]** — this is important!

---

## Step 2: Prepare the server

Make sure your Windows Server has:
- **Docker Desktop** installed (download from https://www.docker.com/products/docker-desktop/)
- At least 4 GB RAM free
- A static internal IP or hostname

---

## Step 3: Configure Kdesk

1. Copy `.env.example` to `.env`:
   ```
   copy .env.example .env
   ```

2. Edit `.env` with a text editor and fill in:
   - `SECRET_KEY` — generate a random string (e.g. 50 random characters)
   - `DB_PASSWORD` — set a strong password for the database
   - `AZURE_TENANT_ID` — from Step 1
   - `AZURE_CLIENT_ID` — from Step 1
   - `AZURE_CLIENT_SECRET` — from Step 1
   - `ALLOWED_HOSTS` — the IP or hostname of your server (e.g. `192.168.1.100`)

---

## Step 4: Start the application

Open a terminal/PowerShell in the `kdesk` folder and run:

```bash
docker compose up -d --build
```

This will:
- Build the application image
- Start PostgreSQL, Redis, the web app, and the background workers
- Run database migrations automatically

Wait ~1 minute, then open your browser at: **http://your-server-ip:8000**

---

## Step 5: First-time setup

Run these commands once to create your admin account and seed the default settings:

```bash
# Create the first admin user (you will be prompted for a password)
docker compose exec web python manage.py createsuperuser --email your.email@kramerav.com

# Seed SLA policies and register scheduled tasks
docker compose exec web python manage.py setup_kdesk
```

---

## Step 6: Verify everything works

1. **Log in** at http://your-server-ip:8000 with the admin account you just created
2. **Test email polling**: Send an email to servicedesk@kramerav.com and wait up to 5 minutes — a ticket should appear automatically
3. **Test user sync**: Go to Admin Panel (/admin/) → Users — after ~1 hour the KramerLicensedUsers group will be synced. To trigger it immediately:
   ```bash
   docker compose exec web python manage.py shell -c "from integrations.user_sync import sync_users; sync_users()"
   ```

---

## Daily operations

| Task | Command |
|---|---|
| Start Kdesk | `docker compose up -d` |
| Stop Kdesk | `docker compose down` |
| View logs | `docker compose logs -f web` |
| Update Kdesk (after code changes) | `docker compose up -d --build` |
| Backup the database | `docker compose exec db pg_dump -U kdesk kdesk > backup.sql` |

---

## Accessing the admin panel

Go to **/admin/** and log in with your superuser account.
From there you can manage users, view email logs, adjust SLA policies, and more.

---

## Port / firewall

By default Kdesk runs on port **8000**. You can change this in `docker-compose.yml` under `web.ports`.
Make sure your Windows Firewall allows inbound traffic on that port from your internal network.
