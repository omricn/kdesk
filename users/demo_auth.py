"""
Demo-mode authentication for Kdesk.
Enabled when DEMO_MODE=True in .env — bypasses Azure SSO entirely.
Navigating to /demo-login/ creates a superuser session automatically.
"""
from django.contrib.auth import get_user_model, login
from django.shortcuts import redirect
from django.views import View

User = get_user_model()


class DemoLoginView(View):
    """One-click login — no credentials needed."""

    def get(self, request):
        user, _ = User.objects.get_or_create(
            username='demo@demo.com',
            defaults={
                'email': 'demo@demo.com',
                'first_name': 'Demo',
                'last_name': 'Admin',
                'is_staff': True,
                'is_superuser': True,
            },
        )
        login(request, user, backend='django.contrib.auth.backends.ModelBackend')
        return redirect('/')
