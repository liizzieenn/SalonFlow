"""Booking use cases: create and cancel bookings safely under concurrency.

Concurrency strategy (two layers):

1. ``create_booking`` runs in ``transaction.atomic`` and takes a row lock on the
   staff member (``SELECT ... FOR UPDATE``) before re-checking availability. Two
   requests for the same staff member are therefore serialised: the second one waits
   for the first to commit and then sees its booking when it re-runs the slot check.
2. The ``booking_no_staff_overlap`` exclusion constraint is the last line of defence
   (e.g. bookings created outside this service); its ``IntegrityError`` is translated
   into :class:`SlotUnavailableError`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.clients.models import Client
from apps.salons.models import Salon, Service, Staff

from .models import Booking
from .slots import is_slot_available


class BookingError(Exception):
    """Base class for booking failures that should be shown to the user."""

    message = _("The booking could not be created.")

    def __str__(self) -> str:
        return str(self.message)


class SlotUnavailableError(BookingError):
    message = _("Sorry, this time slot is no longer available. Please pick another.")


class InvalidSelectionError(BookingError):
    message = _("The selected service or staff member is not available.")


@dataclass(frozen=True)
class ClientDetails:
    """Contact details entered in the booking form."""

    full_name: str
    phone: str
    email: str = ""


def get_or_create_client(salon: Salon, details: ClientDetails, user=None) -> Client:
    """Find the salon's client by phone number, or create one.

    Existing clients get their name/email refreshed with the latest input.
    """
    client, created = Client.objects.get_or_create(
        salon=salon,
        phone=details.phone,
        defaults={
            "full_name": details.full_name,
            "email": details.email,
            "user": user,
        },
    )
    if not created:
        changed = False
        for field in ("full_name", "email"):
            value = getattr(details, field)
            if value and getattr(client, field) != value:
                setattr(client, field, value)
                changed = True
        if user is not None and client.user_id is None:
            client.user = user
            changed = True
        if changed:
            client.save()
    return client


def _candidate_staff(service: Service, staff: Staff | None) -> list[int]:
    if staff is not None:
        if staff.salon_id != service.salon_id:
            raise InvalidSelectionError
        return [staff.pk]
    # "Any staff": deterministic pk order also gives a consistent lock order.
    return list(
        service.staff.filter(is_active=True).order_by("pk").values_list("pk", flat=True)
    )


@transaction.atomic
def create_booking(
    *,
    service: Service,
    start_at: datetime,
    client_details: ClientDetails,
    staff: Staff | None = None,
    notes: str = "",
    user=None,
    now: datetime | None = None,
) -> Booking:
    """Book ``service`` at ``start_at`` with ``staff`` (or any free staff member).

    Raises :class:`SlotUnavailableError` if the slot is taken or outside free time and
    :class:`InvalidSelectionError` for inactive / mismatched service or staff.
    """
    if not service.is_active:
        raise InvalidSelectionError
    salon = service.salon
    now = now or timezone.now()

    chosen: Staff | None = None
    for staff_pk in _candidate_staff(service, staff):
        locked = (
            Staff.objects.select_for_update()
            .select_related("salon")
            .filter(pk=staff_pk, salon=salon, is_active=True)
            .first()
        )
        if locked is not None and is_slot_available(locked, service, start_at, now=now):
            chosen = locked
            break
    if chosen is None:
        raise SlotUnavailableError

    client = get_or_create_client(salon, client_details, user=user)
    booking = Booking(
        salon=salon,
        client=client,
        staff=chosen,
        service=service,
        start_at=start_at,
        status=Booking.Status.CONFIRMED,
        notes=notes,
    )
    try:
        with transaction.atomic():  # savepoint, so the outer transaction stays usable
            booking.save()
    except IntegrityError as exc:
        if "booking_no_staff_overlap" in str(exc):
            raise SlotUnavailableError from exc
        raise
    return booking


def cancel_booking(booking: Booking) -> Booking:
    """Cancel a booking, freeing its slot. Idempotent."""
    if booking.status != Booking.Status.CANCELLED:
        booking.status = Booking.Status.CANCELLED
        booking.save(update_fields=["status", "updated_at"])
    return booking
