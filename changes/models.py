from django.db import models
from django.conf import settings


class Change(models.Model):
    STATUS_NEW = 'new'
    STATUS_PENDING = 'pending_approval'
    STATUS_PENDING_CHANGES = 'pending_changes'
    STATUS_APPROVED = 'approved'
    STATUS_NOT_APPROVED = 'not_approved'
    STATUS_IN_PROGRESS = 'in_progress'
    STATUS_DONE = 'done'
    STATUS_CANCELLED = 'cancelled'

    STATUS_CHOICES = [
        (STATUS_NEW, 'New'),
        (STATUS_PENDING, 'Pending Approval'),
        (STATUS_PENDING_CHANGES, 'Pending Changes'),
        (STATUS_APPROVED, 'Approved'),
        (STATUS_NOT_APPROVED, 'Not Approved'),
        (STATUS_IN_PROGRESS, 'In Progress'),
        (STATUS_DONE, 'Done'),
        (STATUS_CANCELLED, 'Cancelled'),
    ]

    RISK_LOW = 'low'
    RISK_MEDIUM = 'medium'
    RISK_HIGH = 'high'

    RISK_CHOICES = [
        (RISK_LOW, 'Low'),
        (RISK_MEDIUM, 'Medium'),
        (RISK_HIGH, 'High'),
    ]

    REGION_ISRAEL = 'israel'
    REGION_GLOBAL = 'global'
    REGION_CHOICES = [
        (REGION_ISRAEL, 'Israel'),
        (REGION_GLOBAL, 'Globally'),
    ]

    SYSTEM_CHOICES = [
        ('priority', 'Priority'),
        ('qv', 'Qlikview'),
        ('sf', 'SalesForce'),
        ('kdesk', 'KDESK'),
        ('servers', 'Servers - Network connectivity'),
        ('other', 'Other'),
    ]

    title = models.CharField(max_length=300)
    description = models.TextField()
    risk_level = models.CharField(max_length=10, choices=RISK_CHOICES, default=RISK_LOW)
    planned_date = models.DateField()
    planned_from = models.TimeField(null=True, blank=True)
    planned_to = models.TimeField(null=True, blank=True)
    rollback_plan = models.TextField()
    affected_system = models.CharField(max_length=20, choices=SYSTEM_CHOICES)
    affected_system_other = models.CharField(max_length=200, blank=True)
    affected_region = models.CharField(max_length=20, choices=REGION_CHOICES, default=REGION_ISRAEL)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_NEW, db_index=True)
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='changes',
    )
    notes = models.TextField(blank=True)
    manager_remarks = models.TextField(blank=True)

    reminded_start = models.BooleanField(default=False)
    reminded_done = models.BooleanField(default=False)
    reminded_done_followup = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'#{self.pk:04d} — {self.title}'

    @property
    def affected_system_display(self):
        if self.affected_system == 'other' and self.affected_system_other:
            return self.affected_system_other
        return dict(self.SYSTEM_CHOICES).get(self.affected_system, self.affected_system)

    @property
    def risk_badge_class(self):
        return {
            'low': 'bg-success',
            'medium': 'bg-warning',
            'high': 'bg-danger',
        }.get(self.risk_level, 'bg-secondary')

    @property
    def status_badge_class(self):
        return {
            'new': 'bg-secondary',
            'pending_approval': 'bg-warning text-dark',
            'pending_changes': 'bg-orange text-dark',
            'approved': 'bg-info',
            'not_approved': 'bg-danger',
            'in_progress': 'bg-primary',
            'done': 'bg-success',
            'cancelled': 'bg-secondary',
        }.get(self.status, 'bg-secondary')

    @property
    def calendar_color(self):
        return {
            'new': '#666',
            'pending_approval': '#f59e0b',
            'approved': '#1a4a6e',
            'not_approved': '#BE0078',
            'in_progress': '#8205B4',
            'done': '#69FFC3',
            'cancelled': '#555',
        }.get(self.status, '#666')


class ChangeAttachment(models.Model):
    change = models.ForeignKey(Change, on_delete=models.CASCADE, related_name='attachments')
    filename = models.CharField(max_length=255)
    file = models.FileField(upload_to='change_attachments/%Y/%m/')
    file_size = models.PositiveIntegerField(default=0)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.filename
