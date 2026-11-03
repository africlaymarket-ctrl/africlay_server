"""Retry due marketplace emails from a scheduled job or manual command."""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from notifications.models import EmailDelivery, EmailDeliveryStatus
from notifications.services import send_notification_email


class Command(BaseCommand):
    help = 'Send due marketplace emails and retry transient failures.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=100)

    def handle(self, *args, **options):
        limit = max(0, min(options['limit'], 500))
        now = timezone.now()
        due = EmailDelivery.objects.filter(
            Q(status__in=[EmailDeliveryStatus.PENDING, EmailDeliveryStatus.RETRY], next_attempt_at__lte=now)
            | Q(status=EmailDeliveryStatus.SENDING, updated_at__lte=now - timedelta(minutes=10))
        ).order_by('created_at').values_list('pk', flat=True)[:limit]
        sent = sum(bool(send_notification_email(pk)) for pk in list(due))
        self.stdout.write(f'Processed {len(due)} due emails; {sent} accepted by backend/provider.')
