from django.conf import settings
from django.db import models


class ProvisioningRequest(models.Model):
    STATUS_PENDING = 'pending'
    STATUS_CLAIMED = 'claimed'
    STATUS_COMPLETED = 'completed'
    STATUS_FAILED = 'failed'
    STATUS_REVIEW_NEEDED = 'review_needed'
    STATUS_CANCELLED = 'cancelled'
    STATUS_PAUSED = 'paused'
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('claimed', 'Claimed'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
        ('review_needed', 'Review Needed'),
        ('cancelled', 'Cancelled'),
        ('paused', 'Paused'),
    ]

    # Linked ticket (created from the HiBob notification email)
    ticket = models.ForeignKey(
        'tickets.Ticket', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='provisioning_requests',
    )

    # Employee data parsed from HiBob email
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    middle_name = models.CharField(max_length=100, blank=True)
    department = models.CharField(max_length=100)
    division = models.CharField(max_length=100)
    country = models.CharField(max_length=100)   # full name, e.g. "Israel"
    region = models.CharField(max_length=50)     # e.g. "HQ", "EMEA"
    start_date = models.DateField(null=True, blank=True)
    personal_mobile = models.CharField(max_length=50, blank=True)
    reports_to = models.CharField(max_length=200, blank=True)
    job_title = models.CharField(max_length=200, blank=True)
    employment_type = models.CharField(max_length=100, blank=True)
    employee_id = models.CharField(max_length=50, blank=True)

    # Resolved provisioning data
    work_email = models.EmailField(blank=True)    # e.g. clinetski@kramerav.com
    m365_groups = models.JSONField(default=list)  # resolved group email addresses
    groups_fallback = models.BooleanField(default=False)  # True when Excel lookup found no match

    # Review / active-user-conflict fields
    force_create = models.BooleanField(default=False)    # set True to skip active-account check on re-queue
    blocked_by_email = models.CharField(max_length=200, blank=True)  # existing active account UPN

    # Workflow
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    is_dry_run = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    claimed_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    # Result reported back by the agent
    result_success = models.BooleanField(null=True, blank=True)
    result_log = models.TextField(blank=True)
    result_message = models.TextField(blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.first_name} {self.last_name} — {self.status}'


class ProvisioningSettings(models.Model):
    """Singleton — always pk=1. Controls whether the provisioning pipeline is active."""
    enabled = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
    )

    class Meta:
        verbose_name = 'Provisioning Settings'
        verbose_name_plural = 'Provisioning Settings'

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return f'Provisioning {"enabled" if self.enabled else "disabled"}'


class SyncTrigger(models.Model):
    STATUS_PENDING = 'pending'
    STATUS_RUNNING = 'running'
    STATUS_COMPLETED = 'completed'
    STATUS_FAILED = 'failed'
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('running', 'Running'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]

    is_dry_run = models.BooleanField(default=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    claimed_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        kind = 'Dry Run' if self.is_dry_run else 'Live Sync'
        return f'{kind} — {self.status} @ {self.created_at:%Y-%m-%d %H:%M}'


class SyncRun(models.Model):
    trigger = models.OneToOneField(
        SyncTrigger, null=True, blank=True, on_delete=models.SET_NULL, related_name='run',
    )
    started_at = models.DateTimeField()
    completed_at = models.DateTimeField()
    is_dry_run = models.BooleanField()
    matched = models.IntegerField(default=0)
    updated = models.IntegerField(default=0)
    skipped = models.IntegerField(default=0)
    not_found = models.IntegerField(default=0)
    errors = models.IntegerField(default=0)
    raw_log = models.TextField(blank=True)
    success = models.BooleanField(default=True)
    error_message = models.TextField(blank=True)
    log_filename = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['-completed_at']

    def __str__(self):
        kind = 'Dry Run' if self.is_dry_run else 'Live Sync'
        return f'{kind} @ {self.completed_at:%Y-%m-%d %H:%M}'


class SyncChange(models.Model):
    run = models.ForeignKey(SyncRun, on_delete=models.CASCADE, related_name='changes')
    email = models.EmailField()
    field_name = models.CharField(max_length=100)
    old_value = models.TextField(blank=True)
    new_value = models.TextField(blank=True)

    class Meta:
        ordering = ['email', 'field_name']

    def __str__(self):
        return f'{self.email} / {self.field_name}'
