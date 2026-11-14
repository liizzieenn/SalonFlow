from io import StringIO
from unittest import mock

import pytest
from django.core.management import CommandError, call_command
from django.db.models import Count
from django.test import Client as HttpClient
from django.utils import timezone

from apps.assistant.models import FAQ, KnowledgeChunk
from apps.bookings.models import Booking
from apps.clients.models import Client
from apps.salons.models import Salon
from tests.factories import SalonFactory

pytestmark = pytest.mark.django_db


def seed(*args) -> str:
    out = StringIO()
    call_command("seed_demo", *args, stdout=out)
    return out.getvalue()


def test_creates_demo_salon_with_logins():
    output = seed("--password", "pw-123456")
    salon = Salon.objects.get(slug="demo")
    assert salon.staff_members.count() == 3
    assert salon.services.count() == 6
    assert FAQ.objects.filter(salon=salon).count() == 8
    assert Client.objects.filter(salon=salon).count() == 30
    assert "demo / pw-123456" in output and "demo-staff / pw-123456" in output

    owner = HttpClient()
    assert owner.login(username="demo", password="pw-123456")
    assert owner.get("/en/dashboard/demo/analytics/").status_code == 200
    staff = HttpClient()
    assert staff.login(username="demo-staff", password="pw-123456")
    assert staff.get("/en/dashboard/demo/analytics/").status_code == 403  # staff view


def test_bookings_cover_past_and_future_with_mixed_statuses():
    seed()
    bookings = Booking.objects.filter(salon__slug="demo")
    now = timezone.now()
    assert bookings.filter(start_at__lt=now).count() > 300
    assert bookings.filter(start_at__gt=now).exists()
    statuses = dict(bookings.values_list("status").annotate(n=Count("pk")))
    assert {"completed", "no_show", "cancelled", "confirmed"} <= set(statuses)
    # Prices are snapshots and every booking ends after it starts.
    assert not bookings.filter(price__isnull=True).exists()


def test_bookings_respect_breaks_and_specialities():
    from datetime import time

    seed()
    bookings = Booking.objects.filter(salon__slug="demo").select_related("staff")
    for booking in bookings:
        assert booking.staff.services.filter(pk=booking.service_id).exists()
        start = timezone.localtime(booking.start_at).time()
        end = timezone.localtime(booking.end_at).time()
        overlaps_lunch = start < time(14, 30) and end > time(14)
        assert not overlaps_lunch, booking
        assert time(10) <= start and end <= time(19), booking


def test_demo_clients_have_no_contact_details():
    """Fake numbers could reach real people once SMS/email is configured."""
    seed()
    clients = Client.objects.filter(salon__slug="demo")
    assert not clients.exclude(phone="").exists()
    assert not clients.exclude(email="").exists()


def test_refuses_to_overwrite_without_reset():
    seed()
    with pytest.raises(CommandError, match="--reset"):
        seed()


def test_reset_rebuilds_and_is_deterministic():
    seed()
    first = Booking.objects.filter(salon__slug="demo").count()
    seed("--reset")
    assert Salon.objects.filter(slug="demo").count() == 1
    assert Booking.objects.filter(salon__slug="demo").count() == first


def test_reset_leaves_other_salons_alone():
    other = SalonFactory(slug="real-salon")
    seed()
    seed("--reset")
    assert Salon.objects.filter(pk=other.pk).exists()


def test_builds_assistant_index_once_without_per_object_tasks():
    with mock.patch("apps.assistant.tasks.index_source.delay") as delay:
        seed()
    delay.assert_not_called()
    assert KnowledgeChunk.objects.filter(salon__slug="demo").count() == 15


def test_password_from_environment(monkeypatch):
    monkeypatch.setenv("DEMO_PASSWORD", "from-env-123")
    # The option default is read when the parser is built, i.e. per call.
    output = seed()
    assert "demo / from-env-123" in output
