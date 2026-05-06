"""
Middleware that allows the employee portal to be embedded inside Microsoft Teams.

For /portal/* routes only:
- Removes Django's X-Frame-Options: DENY header
- Adds a Content-Security-Policy frame-ancestors directive that whitelists
  all Teams origins (web, desktop, mobile, Office integrations)

All other routes are unaffected and stay behind X-Frame-Options: DENY.
"""


_TEAMS_ORIGINS = " ".join([
    "'self'",
    "https://teams.microsoft.com",
    "https://*.teams.microsoft.com",
    "https://*.skype.com",
    "https://*.office.com",
    "https://*.office365.com",
    "https://*.microsoft.com",
])

_FRAME_ANCESTORS_CSP = f"frame-ancestors {_TEAMS_ORIGINS};"


class TeamsEmbedMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.path.startswith('/portal/'):
            response['X-Frame-Options'] = 'ALLOWALL'
            existing = response.get('Content-Security-Policy', '')
            if existing:
                # Replace or append frame-ancestors
                if 'frame-ancestors' in existing:
                    import re
                    csp = re.sub(r'frame-ancestors[^;]*;?', _FRAME_ANCESTORS_CSP, existing)
                else:
                    csp = existing.rstrip(';') + '; ' + _FRAME_ANCESTORS_CSP
            else:
                csp = _FRAME_ANCESTORS_CSP
            response['Content-Security-Policy'] = csp
        return response
