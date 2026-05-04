from django.db.models.signals import post_save
from django.dispatch import receiver


@receiver(post_save, sender='tickets.Ticket')
def auto_create_kb_draft(sender, instance, created, **kwargs):
    if created:
        return
    if instance.status != 'closed':
        return
    if not instance.solution:
        return
    # Skip HR-category tickets
    if instance.subcategory and instance.subcategory.category.name == 'HR':
        return
    from .models import KBArticle
    if KBArticle.objects.filter(source_ticket=instance).exists():
        return
    KBArticle.objects.create(
        title=instance.title,
        body=instance.solution,
        subcategory=instance.subcategory,
        ticket_item=instance.ticket_item,
        source_ticket=instance,
        author=instance.assignee,
        status=KBArticle.STATUS_DRAFT,
    )
