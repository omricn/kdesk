import datetime
from zoneinfo import ZoneInfo
from django.shortcuts import render
from django.utils import timezone

# Portal is locked until this moment. Remove this file (or this class from settings)
# once maintenance is done — it auto-lifts when the clock passes REOPEN_AT.
_IL = ZoneInfo('Asia/Jerusalem')
PORTAL_REOPEN_AT = datetime.datetime(2026, 5, 6, 11, 0, 0, tzinfo=_IL)


class PortalMaintenanceMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.path.startswith('/portal/'):
            return self.get_response(request)

        # Admins always pass through
        if request.user.is_authenticated and getattr(request.user, 'is_admin', False):
            return self.get_response(request)

        if timezone.now() < PORTAL_REOPEN_AT:
            return render(request, 'portal/maintenance.html',
                          {'reopen_at': PORTAL_REOPEN_AT}, status=503)

        return self.get_response(request)
