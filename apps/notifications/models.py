"""Delivery log for booking notifications.

One row per (booking, kind, channel). The unique constraint makes sending
idempotent: a retried task, two workers or overlapping beat runs can never send the
same reminder twice.
"""

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel


class Notification(TimeStampedModel):
    class Kind(models.TextChoices):
        CONFIRMATION = "confirmation", _("Booking confirmation")
        REMINDER_24H = "reminder_24h", _("Reminder 24 hours before")
        REMINDER_2H = "reminder_2h", _("Reminder 2 hours before")

    class Channel(models.TextChoices):
        EMAIL = "email", _("Email")
        SMS = "sms", _("SMS")

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        SENT = "sent", _("Sent")
        FAILED = "failed", _("Failed")

    booking = models.ForeignKey(
        "bookings.Booking",
        on_delete=models.CASCADE,
        related_name="notifications",
        verbose_name=_("booking"),
    )
    kind = models.CharField(_("kind"), max_length=16, choices=Kind.choices)
    channel = models.CharField(_("channel"), max_length=8, choices=Channel.choices)
    recipient = models.CharField(_("recipient"), max_length=254)
    status = models.CharField(
        _("status"), max_length=8, choices=Status.choices, default=Status.PENDING
    )
    attempts = models.PositiveSmallIntegerField(_("attempts"), default=0)
    error = models.TextField(_("last error"), blank=True)
    sent_at = models.DateTimeField(_("sent at"), null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("notification")
        verbose_name_plural = _("notifications")
        constraints = [
            models.UniqueConstraint(
                fields=["booking", "kind", "channel"],
                name="notification_once_per_booking_kind_channel",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} · {self.channel} → {self.recipient}"
