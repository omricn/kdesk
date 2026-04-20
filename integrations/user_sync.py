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
            changed = False
            # Don't overwrite [OldUser] tag if already deactivated
            if not user.display_name.startswith('[OldUser]') and user.display_name != display_name:
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

    # Deactivate and tag users who are no longer in the group
    users_to_deactivate = (
        User.objects
        .filter(is_active=True, entra_id__isnull=False)
        .exclude(entra_id='')
        .exclude(entra_id__in=entra_ids_in_group)
        .exclude(is_superuser=True)
        .exclude(is_admin=True)
    )
    deactivated_count = 0
    for user in users_to_deactivate:
        if not user.display_name.startswith('[OldUser]'):
            user.display_name = f'[OldUser] {user.display_name}'.strip()
        user.is_active = False
        user.save(update_fields=['display_name', 'is_active'])
        deactivated_count += 1

    if deactivated_count:
        logger.info(f'[UserSync] Deactivated and tagged {deactivated_count} users no longer in group.')

    logger.info(f'[UserSync] Sync complete. {len(members)} members processed.')


def sync_admins():
    """
    Pull all members of ENTRA_ADMIN_GROUP and ensure they exist as admin users.
    Strips is_admin from anyone no longer in the group and tags them [OldAdmin].
    Also syncs IT manager role from ENTRA_IT_MANAGER_GROUP_EMAIL.
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

    # Remove admin rights and tag anyone no longer in the group
    users_to_demote = (
        User.objects
        .filter(is_admin=True, entra_id__isnull=False)
        .exclude(entra_id='')
        .exclude(entra_id__in=admin_entra_ids)
        .exclude(is_superuser=True)
    )
    demoted_count = 0
    for user in users_to_demote:
        if not user.display_name.startswith('[OldAdmin]'):
            user.display_name = f'[OldAdmin] {user.display_name}'.strip()
        user.is_admin = False
        user.is_staff = False
        user.save(update_fields=['display_name', 'is_admin', 'is_staff'])
        demoted_count += 1

    if demoted_count:
        logger.info(f'[AdminSync] Removed admin rights and tagged {demoted_count} users no longer in {group_email}.')

    logger.info(f'[AdminSync] Sync complete. {len(members)} admins processed.')

    # Also sync IT manager role and superuser status
    _sync_it_managers(client)
    _sync_superusers(client)


def _sync_it_managers(client):
    """Sync is_it_manager flag from the IT_Manager Entra group."""
    from users.models import User

    group_email = getattr(settings, 'ENTRA_IT_MANAGER_GROUP_EMAIL', 'IT_Manager@kramerav.com')

    try:
        group_id = client.get_group_id_by_email(group_email)
        members = client.get_group_members(group_id)
    except Exception as exc:
        logger.error(f'[ITManagerSync] Failed to fetch group members: {exc}')
        return

    manager_entra_ids = set()

    for member in members:
        entra_id = member.get('id', '')
        email = (member.get('mail', '') or '').lower().strip()
        if not email:
            continue
        manager_entra_ids.add(entra_id)
        User.objects.filter(entra_id=entra_id).update(is_it_manager=True)

    # Clear the flag for anyone no longer in the group
    cleared = (
        User.objects
        .filter(is_it_manager=True, entra_id__isnull=False)
        .exclude(entra_id='')
        .exclude(entra_id__in=manager_entra_ids)
        .update(is_it_manager=False)
    )
    if cleared:
        logger.info(f'[ITManagerSync] Cleared is_it_manager from {cleared} users no longer in {group_email}.')

    logger.info(f'[ITManagerSync] Sync complete. {len(members)} IT managers processed.')


def _sync_superusers(client):
    """Sync is_superuser flag: members of IT_SupportAdmin OR IT_Manager groups get superuser."""
    from users.models import User

    superuser_entra_ids = set()

    for group_setting in ('ENTRA_SUPPORT_ADMIN_GROUP_EMAIL', 'ENTRA_IT_MANAGER_GROUP_EMAIL'):
        group_email = getattr(settings, group_setting, '')
        if not group_email:
            continue
        try:
            group_id = client.get_group_id_by_email(group_email)
            members = client.get_group_members(group_id)
        except Exception as exc:
            logger.error(f'[SuperuserSync] Failed to fetch {group_email}: {exc}')
            continue
        for member in members:
            entra_id = member.get('id', '')
            if entra_id:
                superuser_entra_ids.add(entra_id)

    for entra_id in superuser_entra_ids:
        User.objects.filter(entra_id=entra_id).update(is_superuser=True)

    cleared = (
        User.objects
        .filter(is_superuser=True, entra_id__isnull=False)
        .exclude(entra_id='')
        .exclude(entra_id__in=superuser_entra_ids)
        .update(is_superuser=False)
    )
    if cleared:
        logger.info(f'[SuperuserSync] Cleared is_superuser from {cleared} SSO users no longer in either group.')

    logger.info(f'[SuperuserSync] Sync complete. {len(superuser_entra_ids)} superusers across both groups.')
