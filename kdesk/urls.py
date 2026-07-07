from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.views.generic import TemplateView, RedirectView
from users.demo_auth import DemoLoginView
urlpatterns = [
    path('demo-login/', DemoLoginView.as_view(), name='demo-login'),
    path('favicon.ico', RedirectView.as_view(url='/static/img/favicon.ico', permanent=True)),
    path('sw.js', TemplateView.as_view(
        template_name='sw.js',
        content_type='application/javascript',
    ), name='service_worker'),
    path('manifest.json', TemplateView.as_view(
        template_name='manifest.json',
        content_type='application/manifest+json',
    ), name='manifest'),
    path('admin/', admin.site.urls),
    path('', include('tickets.urls')),
    path('', include('users.urls')),
    path('', include('changes.urls')),
    path('', include('budget.urls')),
    path('', include('kb.urls')),
    path('', include('hibob_sync.urls')),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
