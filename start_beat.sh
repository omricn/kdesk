#!/bin/bash
set -e
exec celery -A kdesk beat -l info
