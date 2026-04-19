"""
Syncs users from the KramerLicensedUsers Entra group into the local database.
Runs periodically via Celery Beat.
"""
import logging

from django.conf import settings
from django.utils import timezone

from .graph_client import get_client

logger = logging.getLogger(__name__)


def sync_users():
    """
    Pull all members of the ENTRA_USER_GROUP and upsert them into the local
    users.User table. Deactivates users that are no longer in the group.
    """
    from users.models import User

    client = get_client()
    group_name = settings.ENTRA_USER_GROUP

    try:
        group_id = client.get_group_id_by_name(group_name)
        members = client.get_group_members(group_id)
    except Exception as exc:
        logger.error(f'[UserSync] Failed to fetch group members: {exc}')
        return

    entra_ids_in_group = set()

    for member in members:
        entra_id = member.get('id', '')
        email = member.get('mail', '') or ''
        display_name = member.get('displayName', '') or ''
        account_enabled = member.get('accountEnabled', True)

        if not email:
            logger.debug(f'[UserSync] Skipping member {entra_id} — no email')
            continue

        email = email.lower().strip()
        entra_ids_in_group.add(entra_id)

        user, created = User.objects.get_or_create(
            email=email,
            defaults={
                'display_name': display_name,
                'entra_id': entra_id,
                'is_active': account_enabled,
            }
        )

        if not created:
            # Update existing user details
            changed = False
            if user.display_name != display_name:
                user.display_name = display_name
                changed = True
            if user.entra_id != entra_id:
                user.entra_id = entra_id
                changed = True
            # Never deactivate admin users via the regular user sync
            if not user.is_admin and user.is_active != account_enabled:
                user.is_active = account_enabled
                changed = True
            if changed:
                user.save(update_fields=['display_name', 'entra_id', 'is_active', 'last_sync'])

        user.last_sync = timezone.now()
        user.save(update_fields=['last_sync'])

        action = 'created' if created else 'updated'
        logger.debug(f'[UserSync] {action}: {email}')

    # Deactivate users who are no longer in the group (but keep their data)
    deactivated = (
        User.objects
        .filter(is_active=True, entra_id__isnull=False)
        .exclude(entra_id='')
        .exclude(entra_id__in=entra_ids_in_group)
        .exclude(is_superuser=True)  # never deactivate superusers
        .exclude(is_admin=True)      # never deactivate admins via user sync
        .update(is_active=False)
    )
    if deactivated:
        logger.info(f'[UserSync] Deactivated {deactivated} users no longer in group.')

    logger.info(f'[UserSync] Sync complete. {len(members)} members processed.')


def sync_admins():
    """
    Pull all members of ENTRA_ADMIN_GROUP and ensure they exist as admin users.
    Strips is_admin from anyone no longer in the group.
    """
    from users.models import User

    client = get_client()
    group_email = settings.ENTRA_ADMIN_GROUP_EMAIL

    try:
        group_id = client.get_group_id_by_email(group_email)
        members = client.get_group_members(group_id)
    except Exception as exc:
        logger.error(f'[AdminSync] Failed to fetch group members: {exc}')
        return

    admin_entra_ids = set()

    for member in members:
        entra_id = member.get('id', '')
        email = (member.get('mail', '') or '').lower().strip()
        display_name = member.get('displayName', '') or ''
        account_enabled = member.get('accountEnabled', True)

        if not email:
            logger.debug(f'[AdminSync] Skipping member {entra_id} — no email')
            continue

        admin_entra_ids.add(entra_id)

        user, created = User.objects.get_or_create(
            email=email,
            defaults={
                'display_name': display_name,
                'entra_id': entra_id,
                'is_active': account_enabled,
                'is_admin': True,
                'is_staff': True,
            }
        )

        if not created:
            changed = False
            for field, value in [
                ('display_name', display_name),
                ('entra_id', entra_id),
                ('is_active', account_enabled),
            ]:
                if getattr(user, field) != value:
                    setattr(user, field, value)
                    changed = True
            if not user.is_admin:
                user.is_admin = True
                user.is_staff = True
                changed = True
            if changed:
                user.save(update_fields=['display_name', 'entra_id', 'is_active', 'is_admin', 'is_staff', 'last_sync'])

        user.last_sync = timezone.now()
        user.save(update_fields=['last_sync'])

        action = 'created' if created else 'updated'
        logger.debug(f'[AdminSync] {action}: {email}')

    # Remove admin rights from anyone no longer in the group
    demoted = (
        User.objects
        .filter(is_admin=True, entra_id__isnull=False)
        .exclude(entra_id='')
        .exclude(entra_id__in=admin_entra_ids)
        .exclude(is_superuser=True)
        .update(is_admin=False, is_staff=False)
    )
    if demoted:
        logger.info(f'[AdminSync] Removed admin rights from {demoted} users no longer in {group_email}.')

    logger.info(f'[AdminSync] Sync complete. {len(members)} admins processed.')
