from django.db import models
from django.conf import settings


class Change(models.Model):
    STATUS_NEW = 'new'
    STATUS_PENDING = 'pending_approval'
    STATUS_APPROVED = 'approved'
    STATUS_IN_PROGRESS = 'in_progress'
    STATUS_DONE = 'done'
    STATUS_CANCELLED = 'cancelled'

    STATUS_CHOICES = [
        (STATUS_NEW, 'New'),
        (STATUS_PENDING, 'Pending Approval'),
        (STATUS_APPROVED, 'Approved'),
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

    SYSTEM_CHOICES = [
        ('priority', 'Priority'),
        ('qv', 'QV'),
        ('sf', 'SF'),
        ('kdesk', 'KDESK'),
        ('servers', 'Servers'),
        ('other', 'Other'),
    ]

    title = models.CharField(max_length=300)
    description = models.TextField()
    risk_level = models.CharField(max_length=10, choices=RISK_CHOICES, default=RISK_LOW)
    planned_date = models.DateTimeField()
    rollback_plan = models.TextField()
    affected_system = models.CharField(max_length=20, choices=SYSTEM_CHOICES)
    affected_system_other = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_NEW, db_index=True)
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='changes',
    )
    notes = models.TextField(blank=True)
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
            'pending_approval': 'bg-warning',
            'approved': 'bg-info',
            'in_progress': 'bg-primary',
            'done': 'bg-success',
            'cancelled': 'bg-danger',
        }.get(self.status, 'bg-secondary')

    @property
    def calendar_color(self):
        return {
            'new': '#666',
            'pending_approval': '#f59e0b',
            'approved': '#1a4a6e',
            'in_progress': '#8200B4',
            'done': '#68FFC3',
            'cancelled': '#BE0078',
        }.get(self.status, '#666')
