#!/bin/bash
set -e
exec celery -A kdesk worker -l info --concurrency 2
