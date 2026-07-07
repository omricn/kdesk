# Running Kdesk Locally (Demo Mode)

**Stack:** Django · PostgreSQL · Redis · Celery

## Prerequisites
- [Docker Desktop](https://www.docker.com/products/docker-desktop/)

## Quick start

```bash
git clone https://github.com/omricn/kdesk.git
cd kdesk

cp .env.example .env          # DEMO_MODE=True by default

docker compose up -d

# First run only — apply DB migrations
docker compose exec web python manage.py migrate

# Open in browser
open http://localhost:8000/demo-login/
```

You'll be logged in instantly as a demo admin — no Azure account needed.

## What works in demo mode
- Full ticket lifecycle (create, assign, resolve, SLA timers)
- Knowledge base, change management, task tracking, budget module
- AI ticket summaries (add `ANTHROPIC_API_KEY` or `GROQ_API_KEY` to `.env`)

## What's mocked
- Azure SSO → replaced by `/demo-login/` auto-login
- Microsoft Graph API → calls are silently skipped (no user sync, no email polling)
