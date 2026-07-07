from django.contrib.auth import get_user_model, login
from django.shortcuts import redirect
from django.views import View

User = get_user_model()

class DemoLoginView(View):
    def get(self, request):
        user, _ = User.objects.get_or_create(
            email='demo@demo.com',
            defaults={
                'display_name': 'Demo Admin',
                'is_staff': True,
                'is_superuser': True,
                'is_admin': True,
            },
        )
        login(request, user, backend='django.contrib.auth.backends.ModelBackend')
        return redirect('/')