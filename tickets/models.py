from django.db import models
from django.conf import settings
from django.utils import timezone


class Ticket(models.Model):
    STATUS_NEW = 'new'
    STATUS_IN_PROGRESS = 'in_progress'
    STATUS_PENDING_USER = 'pending_user'
    STATUS_PENDING_VENDOR = 'pending_vendor'
    STATUS_HOLD = 'hold'
    STATUS_CLOSED = 'closed'

    STATUS_CHOICES = [
        (STATUS_NEW, 'New'),
        (STATUS_IN_PROGRESS, 'In Progress'),
        (STATUS_PENDING_USER, 'Pending User Reply'),
        (STATUS_PENDING_VENDOR, 'Pending Vendor'),
        (STATUS_HOLD, 'Hold'),
        (STATUS_CLOSED, 'Closed'),
    ]

    # Statuses considered "terminal" (SLA stops, ticket is done)
    TERMINAL_STATUSES = [STATUS_CLOSED]

    SOURCE_EMAIL = 'email'
    SOURCE_MANUAL = 'manual'
    SOURCE_CHOICES = [
        (SOURCE_EMAIL, 'Email'),
        (SOURCE_MANUAL, 'Manual'),
    ]

    # Core fields
    title = models.CharField(max_length=300)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_NEW, db_index=True)
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default=SOURCE_MANUAL)

    # People
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='assigned_tickets',
    )
    # Requester info (from email — may not be a system user)
    requester_email = models.EmailField(db_index=True)
    requester_name = models.CharField(max_length=200, blank=True)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    # SLA
    sla_deadline = models.DateTimeField(null=True, blank=True, db_index=True)
    sla_breached = models.BooleanField(default=False, db_index=True)

    # Category (3-level hierarchy)
    category = models.ForeignKey(
        'TicketCategory', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='tickets',
    )
    subcategory = models.ForeignKey(
        'TicketSubCategory', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='tickets',
    )
    ticket_item = models.ForeignKey(
        'TicketItem', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='tickets',
    )

    # Email tracking (to avoid creating duplicate tickets)
    email_message_id = models.CharField(max_length=500, blank=True, unique=True, null=True)

    # Resolution
    solution = models.TextField(blank=True, help_text='Required when closing a ticket.')

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'#{self.pk:04d} — {self.title}'

    @property
    def is_overdue(self):
        if self.sla_deadline and self.status not in self.TERMINAL_STATUSES:
            return timezone.now() > self.sla_deadline
        return False

    @property
    def sla_percent_elapsed(self):
        """Returns how much of the SLA window has been used (0-100+)."""
        if not self.sla_deadline:
            return 0
        total = (self.sla_deadline - self.created_at).total_seconds()
        elapsed = (timezone.now() - self.created_at).total_seconds()
        if total <= 0:
            return 100
        return min(int((elapsed / total) * 100), 999)

    @property
    def sla_status(self):
        """Returns 'ok', 'warning' (>75%), or 'breached'."""
        if self.status in self.TERMINAL_STATUSES:
            return 'resolved'
        pct = self.sla_percent_elapsed
        if pct >= 100:
            return 'breached'
        if pct >= 75:
            return 'warning'
        return 'ok'


class TicketCategory(models.Model):
    name = models.CharField(max_length=100, unique=True)

    class Meta:
        ordering = ['name']
        verbose_name_plural = 'Ticket Categories'

    def __str__(self):
        return self.name


class TicketSubCategory(models.Model):
    category = models.ForeignKey(TicketCategory, on_delete=models.CASCADE, related_name='subcategories')
    name = models.CharField(max_length=100)
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='subcategory_assignments',
        help_text='Auto-assign tickets to this admin when this sub-category is selected.',
    )

    class Meta:
        ordering = ['name']
        unique_together = ('category', 'name')

    def __str__(self):
        return self.name


class TicketItem(models.Model):
    subcategory = models.ForeignKey(TicketSubCategory, on_delete=models.CASCADE, related_name='items')
    name = models.CharField(max_length=100)

    class Meta:
        ordering = ['name']
        unique_together = ('subcategory', 'name')

    def __str__(self):
        return self.name


class TicketComment(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name='comments')
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    body = models.TextField()
    is_internal = models.BooleanField(default=False, help_text='Internal note — not sent to requester')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f'Comment on #{self.ticket.pk} by {self.author}'


class TicketAttachment(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name='attachments')
    filename = models.CharField(max_length=255)
    file = models.FileField(upload_to='attachments/%Y/%m/')
    uploaded_at = models.DateTimeField(auto_now_add=True)
    file_size = models.PositiveIntegerField(default=0)

    def __str__(self):
        return self.filename


class EmailLog(models.Model):
    """Tracks processed emails to prevent duplicate ticket creation."""
    message_id = models.CharField(max_length=500, unique=True)
    processed_at = models.DateTimeField(auto_now_add=True)
    ticket = models.ForeignKey(Ticket, on_delete=models.SET_NULL, null=True, blank=True)
    error = models.TextField(blank=True)

    def __str__(self):
        return f'{self.message_id} → #{self.ticket_id}'


class SystemSetting(models.Model):
    """Key-value store for system-wide settings."""
    key = models.CharField(max_length=100, unique=True)
    value = models.TextField(blank=True)
    description = models.CharField(max_length=300, blank=True)

    def __str__(self):
        return f'{self.key} = {self.value}'

    @classmethod
    def get(cls, key, default=''):
        try:
            return cls.objects.get(key=key).value
        except cls.DoesNotExist:
            return default

    @classmethod
    def set(cls, key, value):
        obj, _ = cls.objects.get_or_create(key=key)
        obj.value = value
        obj.save()
