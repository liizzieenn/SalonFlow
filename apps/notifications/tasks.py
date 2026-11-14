"""Celery tasks: booking confirmations and 24h / 2h reminders by email and SMS.

* ``send_booking_notification`` delivers one notification kind for one booking on
  every channel the client has (email and/or phone). Each (booking, kind, channel)
  is recorded in :class:`~apps.notifications.models.Notification` and locked while
  sending, so retries and duplicate tasks never send twice; a retry only re-attempts
  the channels that have not been sent yet.
* ``dispatch_due_reminders`` runs periodically (Celery beat) and queues reminders
  whose send time has come.
"""

from __future__ import annotations

import logging
import smtplib
from collections.abc import Callable
from datetime import datetime, timedelta

from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.db.models import Exists, F, OuterRef
from django.utils import timezone

from apps.bookings.models import Booking

from .messages import RenderedMessage, render
from .models import Notification
from .sms import SMSSendError, send_sms

logger = logging.getLogger(__name__)

Kind = Notification.Kind
Channel = Notification.Channel

RETRYABLE_ERRORS = (SMSSendError, smtplib.SMTPException, OSError)


def reminder_windows() -> list[tuple[str, timedelta, timedelta]]:
    """``(kind, lead, next_lead)`` sorted by lead, longest first.

    A reminder of ``kind`` is due once ``start - lead <= now`` and only while
    ``now < start - next_lead`` — after that the next (shorter) reminder takes over.
    """
    leads = sorted(
        (
            (kind, timedelta(hours=h))
            for kind, h in settings.BOOKING_REMINDER_HOURS.items()
        ),
        key=lambda item: item[1],
        reverse=True,
    )
    return [
        (kind, lead, leads[i + 1][1] if i + 1 < len(leads) else timedelta(0))
        for i, (kind, lead) in enumerate(leads)
    ]


def _channels(
    booking: Booking, msg: RenderedMessage
) -> list[tuple[str, str, Callable]]:
    client = booking.client
    channels: list[tuple[str, str, Callable]] = []
    if client.email:

        def email() -> None:
            mail = EmailMultiAlternatives(
                msg.subject, msg.text, settings.DEFAULT_FROM_EMAIL, [client.email]
            )
            mail.attach_alternative(msg.html, "text/html")
            mail.send(fail_silently=False)

        channels.append((Channel.EMAIL, client.email, email))
    if client.phone:
        channels.append(
            (Channel.SMS, client.phone, lambda: send_sms(client.phone, msg.sms))
        )
    return channels


def _send_once(booking: Booking, kind: str, channel: str, recipient: str, send) -> str:
    """Send on one channel unless already sent. Returns the resulting status.

    Re-raises the provider error (after recording it) so Celery can retry.
    """
    Notification.objects.get_or_create(
        booking=booking, kind=kind, channel=channel, defaults={"recipient": recipient}
    )
    error: Exception | None = None
    with transaction.atomic():
        note = Notification.objects.select_for_update().get(
            booking=booking, kind=kind, channel=channel
        )
        if note.status == Notification.Status.SENT:
            return note.status
        note.attempts += 1
        note.recipient = recipient
        try:
            send()
        except RETRYABLE_ERRORS as exc:
            note.status = Notification.Status.FAILED
            note.error = str(exc)[:2000]
            error = exc
        else:
            note.status = Notification.Status.SENT
            note.sent_at = timezone.now()
            note.error = ""
        note.save()
    if error is not None:
        logger.warning(
            "Sending %s %s for booking %s failed: %s", kind, channel, booking.pk, error
        )
        raise error
    return note.status


def deliver(
    booking_id: int, kind: str, *, now: datetime | None = None
) -> dict[str, str]:
    """Deliver ``kind`` for a booking on all channels; returns ``{channel: status}``.

    Cancelled bookings, and reminders for appointments that already started, are
    skipped. If a channel fails the others are still attempted, then the first
    error is raised.
    """
    booking = (
        Booking.objects.select_related("salon", "client", "staff", "service")
        .filter(pk=booking_id)
        .first()
    )
    if booking is None or not booking.is_active:
        return {}
    if kind != Kind.CONFIRMATION and booking.start_at <= (now or timezone.now()):
        return {}

    message = render(booking, kind)
    results: dict[str, str] = {}
    first_error: Exception | None = None
    for channel, recipient, send in _channels(booking, message):
        try:
            results[channel] = _send_once(booking, kind, channel, recipient, send)
        except RETRYABLE_ERRORS as exc:
            results[channel] = Notification.Status.FAILED
            first_error = first_error or exc
    if first_error is not None:
        raise first_error
    return results


@shared_task(
    name="notifications.send_booking_notification",
    autoretry_for=RETRYABLE_ERRORS,
    retry_backoff=30,
    retry_backoff_max=15 * 60,
    retry_jitter=True,
    max_retries=5,
)
def send_booking_notification(booking_id: int, kind: str) -> dict[str, str]:
    return deliver(booking_id, kind)


def due_reminders(now: datetime) -> list[tuple[int, str]]:
    """``(booking_id, kind)`` pairs whose reminder should be queued at ``now``.

    A reminder is skipped when the booking was made after its send time (the
    confirmation already covered it) or when it has already been queued/sent.
    """
    due: list[tuple[int, str]] = []
    for kind, lead, next_lead in reminder_windows():
        already = Notification.objects.filter(booking=OuterRef("pk"), kind=kind)
        ids = (
            Booking.objects.filter(
                status__in=[Booking.Status.PENDING, Booking.Status.CONFIRMED],
                start_at__gt=now + next_lead,
                start_at__lte=now + lead,
                created_at__lt=F("start_at") - lead,
            )
            .exclude(Exists(already))
            .values_list("pk", flat=True)
        )
        due += [(pk, kind) for pk in ids]
    return due


@shared_task(name="notifications.dispatch_due_reminders")
def dispatch_due_reminders() -> int:
    """Periodic task: queue every reminder that is due. Returns how many were queued."""
    due = due_reminders(timezone.now())
    for booking_id, kind in due:
        send_booking_notification.delay(booking_id, kind)
    if due:
        logger.info("Queued %d booking reminders", len(due))
    return len(due)
