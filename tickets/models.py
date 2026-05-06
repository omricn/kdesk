from django.db import models
from django.conf import settings
from django.utils import timezone


class Ticket(models.Model):
    STATUS_NEW = 'new'
    STATUS_IN_PROGRESS = 'in_progress'
    STATUS_PENDING_USER = 'pending_user'
    STATUS_PENDING_VENDOR = 'pending_vendor'
    STATUS_HOLD = 'hold'
    STATUS_PENDING_MANAGER = 'pending_manager'
    STATUS_CLOSED = 'closed'
    STATUS_USER_RESPONDED = 'user_responded'

    STATUS_CHOICES = [
        (STATUS_NEW, 'New'),
        (STATUS_IN_PROGRESS, 'In Progress'),
        (STATUS_PENDING_USER, 'Pending User Reply'),
        (STATUS_PENDING_VENDOR, 'Pending Vendor'),
        (STATUS_HOLD, 'Hold'),
        (STATUS_PENDING_MANAGER, 'Pending Manager Approval'),
        (STATUS_CLOSED, 'Closed'),
        (STATUS_USER_RESPONDED, 'User Responded'),
    ]

    # Statuses considered "terminal" (SLA stops, ticket is done)
    TERMINAL_STATUSES = [STATUS_CLOSED]

    # Statuses where the SLA clock is paused (waiting on someone else)
    SLA_PAUSED_STATUSES = [
        STATUS_PENDING_USER,
        STATUS_PENDING_VENDOR,
        STATUS_HOLD,
        STATUS_PENDING_MANAGER,
    ]

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
    requester_department = models.CharField(max_length=200, blank=True)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    # SLA
    sla_deadline = models.DateTimeField(null=True, blank=True, db_index=True)
    sla_breached = models.BooleanField(default=False, db_index=True)
    sla_paused_at = models.DateTimeField(null=True, blank=True)

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

    # Whether description contains HTML (email-sourced tickets)
    description_is_html = models.BooleanField(default=False)

    # AI-generated one-sentence summary (populated asynchronously after creation)
    ai_summary = models.TextField(blank=True)

    # Satisfaction rating — filled in by employee via portal after ticket closes
    satisfaction_rating = models.PositiveSmallIntegerField(null=True, blank=True)  # 1–5
    satisfaction_text   = models.CharField(max_length=50, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'#{self.pk:04d} — {self.title}'

    def save(self, *args, **kwargs):
        # Auto-set SLA deadline for brand-new tickets that don't have one yet.
        # Uses business hours (Sun–Thu, 08:00–17:00 Asia/Jerusalem).
        if not self.pk and self.sla_deadline is None:
            from tickets.sla import sla_deadline_for
            self.sla_deadline = sla_deadline_for(timezone.now())
        super().save(*args, **kwargs)

    @property
    def non_inline_attachments(self):
        """Attachments that are not inline images (safe to show in the attachments panel)."""
        return self.attachments.filter(is_inline=False)

    @property
    def sla_is_paused(self):
        return bool(self.sla_paused_at) and self.status in self.SLA_PAUSED_STATUSES

    @property
    def is_overdue(self):
        if self.sla_is_paused:
            return False
        if self.sla_deadline and self.status not in self.TERMINAL_STATUSES:
            return timezone.now() > self.sla_deadline
        return False

    @property
    def sla_percent_elapsed(self):
        """Business hours consumed as a percentage of the SLA target.
        Frozen while ticket is in a paused status or globally suspended."""
        if not self.sla_deadline or not self.created_at:
            return 0
        from tickets.sla import business_hours_elapsed, get_effective_now, get_sla_hours
        # While paused: freeze the clock at the moment pausing began
        if self.sla_is_paused:
            effective_now = self.sla_paused_at
        else:
            effective_now = get_effective_now()
        elapsed = business_hours_elapsed(self.created_at, effective_now)
        sla_hours = get_sla_hours()
        if sla_hours <= 0:
            return 100
        return min(int((elapsed / sla_hours) * 100), 999)

    @property
    def sla_status(self):
        """Returns 'resolved', 'paused', 'ok', 'warning' (≥75%), or 'breached' (≥100%)."""
        if self.status in self.TERMINAL_STATUSES:
            return 'resolved'
        if self.sla_is_paused:
            return 'paused'
        if not self.sla_deadline:
            return 'ok'
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
    updated_at = models.DateTimeField(null=True, blank=True)

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
    content_id = models.CharField(max_length=500, blank=True)  # CID reference for inline images
    is_inline = models.BooleanField(default=False)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='uploaded_attachments',
    )

    def __str__(self):
        return self.filename


class TicketHistory(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name='history')
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
    )
    changed_at = models.DateTimeField(auto_now_add=True)
    field = models.CharField(max_length=100)
    old_value = models.TextField(blank=True)
    new_value = models.TextField(blank=True)

    class Meta:
        ordering = ['-changed_at']

    def __str__(self):
        return f'#{self.ticket_id} {self.field}: {self.old_value} → {self.new_value}'


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


class TicketEmail(models.Model):
    """Stores outbound and inbound emails associated with a specific ticket."""
    DIRECTION_SENT = 'sent'
    DIRECTION_RECEIVED = 'received'
    DIRECTION_CHOICES = [
        (DIRECTION_SENT, 'Sent'),
        (DIRECTION_RECEIVED, 'Received'),
    ]

    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name='emails')
    direction = models.CharField(max_length=10, choices=DIRECTION_CHOICES)
    subject = models.CharField(max_length=500)
    body = models.TextField()
    from_email = models.EmailField()
    to_email = models.EmailField()
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='sent_ticket_emails',
    )
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['timestamp']

    def __str__(self):
        return f'[{self.direction}] #{self.ticket_id}: {self.subject}'
