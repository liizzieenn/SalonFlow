from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.bookings.models import Booking
from tests.factories import BookingFactory, ClientFactory, ServiceFactory, StaffFactory

pytestmark = pytest.mark.django_db


def test_end_and_price_filled_from_service(booking):
    assert booking.end_at - booking.start_at == timedelta(
        minutes=booking.service.duration_minutes
    )
    assert booking.price == booking.service.price


def test_explicit_price_kept():
    booking = BookingFactory(price=Decimal("10.00"))
    assert booking.price == Decimal("10.00")


def test_factory_builds_valid_booking(booking):
    booking.full_clean()  # no ValidationError


def _overlapping(booking: Booking, **kwargs) -> Booking:
    """Unsaved booking for the same staff overlapping ``booking`` by 30 minutes."""
    return Booking(
        salon=booking.salon,
        client=ClientFactory(salon=booking.salon),
        staff=booking.staff,
        service=booking.service,
        start_at=booking.start_at + timedelta(minutes=30),
        **kwargs,
    )


def test_db_rejects_overlapping_booking_for_same_staff(booking):
    with pytest.raises(IntegrityError), transaction.atomic():
        _overlapping(booking).save()


def test_full_clean_reports_overlap(booking):
    candidate = _overlapping(booking)
    candidate.end_at = candidate.start_at + timedelta(hours=1)
    candidate.price = Decimal("1")
    with pytest.raises(ValidationError, match="already has a booking"):
        candidate.full_clean()


def test_back_to_back_bookings_allowed(booking):
    Booking.objects.create(
        salon=booking.salon,
        client=booking.client,
        staff=booking.staff,
        service=booking.service,
        start_at=booking.end_at,
    )


def test_cancelled_booking_frees_the_slot(booking):
    booking.status = Booking.Status.CANCELLED
    booking.save()
    _overlapping(booking).save()


def test_other_staff_can_overlap(booking):
    other = StaffFactory(salon=booking.salon, services=[booking.service])
    Booking.objects.create(
        salon=booking.salon,
        client=booking.client,
        staff=other,
        service=booking.service,
        start_at=booking.start_at,
    )


def test_end_must_be_after_start(booking):
    with pytest.raises(IntegrityError), transaction.atomic():
        Booking.objects.filter(pk=booking.pk).update(end_at=booking.start_at)


def test_clean_rejects_cross_salon_relations(booking):
    foreign_client = ClientFactory()  # different salon
    booking.client = foreign_client
    with pytest.raises(ValidationError) as exc:
        booking.full_clean()
    assert "client" in exc.value.message_dict


def test_clean_rejects_service_staff_does_not_offer(booking):
    booking.service = ServiceFactory(salon=booking.salon)
    with pytest.raises(ValidationError) as exc:
        booking.full_clean()
    assert "service" in exc.value.message_dict


def test_is_active(booking):
    assert booking.is_active
    booking.status = Booking.Status.CANCELLED
    assert not booking.is_active
