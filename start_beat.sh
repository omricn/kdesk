#!/bin/bash
set -e
# Minimal HTTP server so Azure startup probe succeeds (probe expects port 8000)
python3 -c "
from http.server import HTTPServer, BaseHTTPRequestHandler
class H(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b'ok')
    def log_message(self, *a): pass
HTTPServer(('', 8000), H).serve_forever()
" &
exec celery -A kdesk beat -l info
