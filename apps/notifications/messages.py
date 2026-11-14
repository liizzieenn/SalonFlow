"""Render notification texts in the client's language and the salon's time zone."""

from __future__ import annotations

from dataclasses import dataclass
from zoneinfo import ZoneInfo

from django.template.loader import render_to_string
from django.utils import timezone, translation

from apps.bookings.models import Booking

from .models import Notification


@dataclass(frozen=True)
class RenderedMessage:
    subject: str
    text: str
    html: str
    sms: str


def render(booking: Booking, kind: str) -> RenderedMessage:
    """Render the email (subject/text/html) and SMS body for one notification kind."""
    context = {
        "booking": booking,
        "salon": booking.salon,
        "kind": kind,
        "Kind": Notification.Kind,
    }
    base = "notifications"
    with (
        translation.override(booking.client.preferred_language),
        timezone.override(ZoneInfo(booking.salon.timezone)),
    ):
        return RenderedMessage(
            subject=" ".join(render_to_string(f"{base}/subject.txt", context).split()),
            text=render_to_string(f"{base}/email.txt", context).strip(),
            html=render_to_string(f"{base}/email.html", context),
            sms=" ".join(render_to_string(f"{base}/sms.txt", context).split()),
        )
