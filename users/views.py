import secrets
import logging

import msal
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

logger = logging.getLogger(__name__)


def _msal_app(token_cache=None):
    return msal.ConfidentialClientApplication(
        client_id=settings.AZURE_CLIENT_ID,
        client_credential=settings.AZURE_CLIENT_SECRET,
        authority=f'https://login.microsoftonline.com/{settings.AZURE_TENANT_ID}',
        token_cache=token_cache,
    )


# Sites.Read.All lets the Budget page call Graph API as the logged-in user,
# so SharePoint file permissions are enforced natively.
_LOGIN_SCOPES = ['User.Read', 'Sites.Read.All']


def get_user_graph_token(request, scopes=None):
    """Return a fresh Graph API access token for the logged-in user.

    Uses the MSAL token cache stored in the session at login.
    Returns None if no cached token exists (user must re-login).
    """
    if scopes is None:
        scopes = ['Sites.Read.All']
    cache_data = request.session.get('msal_token_cache')
    if not cache_data:
        return None
    cache = msal.SerializableTokenCache()
    cache.deserialize(cache_data)
    app = _msal_app(cache)
    accounts = app.get_accounts()
    if not accounts:
        return None
    result = app.acquire_token_silent(scopes=scopes, account=accounts[0])
    if cache.has_state_changed:
        request.session['msal_token_cache'] = cache.serialize()
    if result and 'access_token' in result:
        return result['access_token']
    return None


def login_view(request):
    """Redirect the user to Microsoft to authenticate."""
    if request.user.is_authenticated:
        return redirect('dashboard' if request.user.is_admin else 'portal_dashboard')

    state = secrets.token_urlsafe(16)
    request.session['sso_state'] = state

    auth_url = _msal_app().get_authorization_request_url(
        scopes=_LOGIN_SCOPES,
        redirect_uri=settings.AZURE_REDIRECT_URI,
        state=state,
    )
    return redirect(auth_url)


def auth_callback(request):
    """
    Microsoft redirects here after the user authenticates.
    We verify the state, exchange the code for a token, check group membership,
    then log the user in (creating their account if it's their first time).
    """
    # Guard against CSRF via state mismatch
    if request.GET.get('state') != request.session.pop('sso_state', None):
        messages.error(request, 'Authentication failed: state mismatch. Please try again.')
        return redirect('login')

    error = request.GET.get('error')
    if error:
        description = request.GET.get('error_description', error)
        messages.error(request, f'Microsoft login error: {description}')
        return redirect('login')

    code = request.GET.get('code')
    if not code:
        messages.error(request, 'No authorisation code received.')
        return redirect('login')

    # Exchange code for access token; use a serializable cache so we can
    # reuse the refresh token for delegated Graph API calls (e.g. Budget page).
    cache = msal.SerializableTokenCache()
    app = _msal_app(cache)
    result = app.acquire_token_by_authorization_code(
        code=code,
        scopes=_LOGIN_SCOPES,
        redirect_uri=settings.AZURE_REDIRECT_URI,
    )

    if 'error' in result:
        logger.error(f'[SSO] Token error: {result}')
        messages.error(request, 'Could not complete sign-in. Please try again.')
        return redirect('login')

    # Get user profile from the token claims (faster than an extra API call)
    claims = result.get('id_token_claims', {})
    user_id = claims.get('oid') or claims.get('sub', '')
    email = (claims.get('preferred_username') or claims.get('email') or '').lower().strip()
    display_name = claims.get('name', '')

    if not email:
        messages.error(request, 'Could not retrieve your email from Microsoft. Contact IT.')
        return redirect('login')

    # All authenticated Kramer users can log in.
    # IT group members get admin access; Kdesk_Superusers group members get is_superuser.
    is_it_admin = _user_in_it_group(user_id)
    is_it_manager = _user_in_it_manager_group(user_id)
    is_support_admin = _user_in_support_admin_group(user_id)
    should_be_superuser = is_support_admin

    # Create or update the user record
    from users.models import User
    from django.utils import timezone

    user, created = User.objects.get_or_create(
        email=email,
        defaults={
            'display_name': display_name,
            'entra_id': user_id,
            'is_admin': is_it_admin,
            'is_it_manager': is_it_manager,
            'is_superuser': should_be_superuser,
            'is_staff': is_it_admin,
            'is_active': True,
        }
    )

    if not created:
        # Keep profile in sync with Entra
        changed = False
        if user.display_name != display_name:
            user.display_name = display_name
            changed = True
        if user.entra_id != user_id:
            user.entra_id = user_id
            changed = True
        # Promote to admin if now in IT group; demotion is handled by sync_admins.
        # Also re-enforce override pin in case anything cleared is_admin.
        if is_it_admin and not user.is_admin:
            user.is_admin = True
            user.is_staff = True
            changed = True
        elif user.is_admin_override and not user.is_admin:
            user.is_admin = True
            user.is_staff = True
            changed = True
        if user.is_it_manager != is_it_manager:
            user.is_it_manager = is_it_manager
            changed = True
        if user.is_superuser != should_be_superuser:
            user.is_superuser = should_be_superuser
            changed = True
        if not user.is_active:
            user.is_active = True
            changed = True
        if changed:
            user.save()

    user.last_sync = timezone.now()
    user.save(update_fields=['last_sync'])

    # Persist MSAL token cache in session for delegated Graph API calls
    if cache.has_state_changed:
        request.session['msal_token_cache'] = cache.serialize()

    login(request, user, backend='django.contrib.auth.backends.ModelBackend')
    logger.info(f'[SSO] {email} logged in (is_admin={user.is_admin})')

    # Redirect: admins follow ?next (or go to dashboard); employees go to portal
    next_url = request.GET.get('next', '')
    # Validate next_url to prevent open-redirect phishing (must be a local path)
    def _safe_next(url):
        return url if (url and url.startswith('/') and not url.startswith('//')) else ''
    if user.is_admin:
        return redirect(_safe_next(next_url) or 'dashboard')
    # For employees, only follow ?next if it points to the portal
    if next_url and next_url.startswith('/portal/'):
        return redirect(next_url)
    return redirect('portal_dashboard')


def _user_in_it_group(entra_user_id: str) -> bool:
    """Returns True if the user is a member of the Global_OPS_IT group."""
    try:
        from integrations.graph_client import get_client
        client = get_client()
        group_id = client.get_group_id_by_email(settings.ENTRA_ADMIN_GROUP_EMAIL)
        return client.is_user_in_group(entra_user_id, group_id)
    except Exception as exc:
        logger.error(f'[SSO] Group check failed for {entra_user_id}: {exc}')
        return False


def _user_in_it_manager_group(entra_user_id: str) -> bool:
    """Returns True if the user is a member of the IT_Manager group."""
    try:
        from integrations.graph_client import get_client
        client = get_client()
        group_id = client.get_group_id_by_email(settings.ENTRA_IT_MANAGER_GROUP_EMAIL)
        return client.is_user_in_group(entra_user_id, group_id)
    except Exception as exc:
        logger.error(f'[SSO] IT Manager group check failed for {entra_user_id}: {exc}')
        return False


def _user_in_support_admin_group(entra_user_id: str) -> bool:
    """Returns True if the user is a member of the Kdesk_Superusers group."""
    try:
        from integrations.graph_client import get_client
        client = get_client()
        group_id = client.get_group_id_by_email(settings.ENTRA_SUPPORT_ADMIN_GROUP_EMAIL)
        return client.is_user_in_group(entra_user_id, group_id)
    except Exception as exc:
        logger.error(f'[SSO] Support Admin group check failed for {entra_user_id}: {exc}')
        return False


def logout_view(request):
    logout(request)
    # Also sign out from Microsoft so the session is fully cleared
    microsoft_logout = (
        f'https://login.microsoftonline.com/{settings.AZURE_TENANT_ID}/oauth2/v2.0/logout'
        f'?post_logout_redirect_uri={settings.AZURE_REDIRECT_URI.replace("/auth/callback/", "/login/")}'
    )
    return redirect(microsoft_logout)


@login_required
def profile_view(request):
    if request.method == 'POST':
        user = request.user
        user.notify_on_assign = 'notify_on_assign' in request.POST
        user.notify_on_update = 'notify_on_update' in request.POST
        user.notify_on_sla_breach = 'notify_on_sla_breach' in request.POST
        user.save(update_fields=['notify_on_assign', 'notify_on_update', 'notify_on_sla_breach'])
        messages.success(request, 'Preferences saved.')
        return redirect('profile')
    return render(request, 'users/profile.html')
