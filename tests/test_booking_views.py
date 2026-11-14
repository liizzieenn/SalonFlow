from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.bookings.models import Booking
from tests.factories import (
    BookingFactory,
    SalonFactory,
    ServiceFactory,
    StaffFactory,
    WorkingHoursFactory,
)

pytestmark = pytest.mark.django_db
TZ = ZoneInfo("Asia/Tbilisi")
HX = {"HTTP_HX_REQUEST": "true"}


@pytest.fixture
def day():
    """A day 3–9 days ahead, so all its slots are in the future and in range."""
    return timezone.now().astimezone(TZ).date() + timedelta(days=3)


@pytest.fixture
def setup(day):
    salon = SalonFactory(name="Kutaisi Beauty", slug="kutaisi-beauty")
    service = ServiceFactory(
        salon=salon, name="Haircut", duration_minutes=60, buffer_minutes=0
    )
    staff = StaffFactory(salon=salon, full_name="Ana", services=[service])
    WorkingHoursFactory(
        staff=staff, weekday=day.weekday(), start_time=time(10), end_time=time(12)
    )
    return salon, service, staff


def url(name, salon, **params):
    from urllib.parse import urlencode

    base = reverse(f"bookings:{name}", args=[salon.slug])
    return f"{base}?{urlencode(params)}" if params else base


def ten(day):
    return datetime.combine(day, time(10), tzinfo=TZ)


def post_data(service, staff, start, **overrides):
    data = {
        "service": service.pk,
        "staff": staff.pk if staff else "",
        "start_at": start.isoformat(),
        "full_name": "Nino  Beridze",
        "phone": "+995 555 12-34-56",
        "email": "",
        "notes": "",
    }
    data.update(overrides)
    return data


def test_services_page_full_layout(client, setup):
    salon, service, _ = setup
    response = client.get(url("services", salon))
    html = response.content.decode()
    assert response.status_code == 200
    assert "<html" in html and "htmx.org" in html
    assert "Kutaisi Beauty" in html and "Haircut" in html


def test_htmx_request_gets_fragment_only(client, setup):
    salon, *_ = setup
    response = client.get(url("services", salon), **HX)
    assert response.status_code == 200
    assert "<html" not in response.content.decode()


def test_history_restore_gets_full_page(client, setup):
    salon, *_ = setup
    response = client.get(
        url("services", salon), **HX, HTTP_HX_HISTORY_RESTORE_REQUEST="true"
    )
    assert "<html" in response.content.decode()


def test_services_without_staff_hidden(client, setup):
    salon, *_ = setup
    ServiceFactory(salon=salon, name="Nobody does this")
    assert "Nobody does this" not in client.get(url("services", salon)).content.decode()


def test_inactive_salon_404(client, setup):
    salon, *_ = setup
    salon.is_active = False
    salon.save()
    assert client.get(url("services", salon)).status_code == 404


def test_staff_step_lists_staff_and_any_option(client, setup):
    salon, service, staff = setup
    html = client.get(url("staff", salon, service=service.pk), **HX).content.decode()
    assert "Ana" in html and "Any specialist" in html


@pytest.mark.parametrize("value", ["", "abc", "999999"])
def test_bad_service_404(client, setup, value):
    salon, *_ = setup
    assert client.get(url("staff", salon, service=value)).status_code == 404


def test_service_from_other_salon_404(client, setup):
    salon, *_ = setup
    foreign = ServiceFactory()
    assert client.get(url("staff", salon, service=foreign.pk)).status_code == 404


def test_dates_step(client, setup, day):
    salon, service, staff = setup
    response = client.get(url("dates", salon, service=service.pk, staff=staff.pk), **HX)
    assert response.status_code == 200
    assert day.isoformat() in response.content.decode()


def test_staff_not_offering_service_404(client, setup):
    salon, service, _ = setup
    other = StaffFactory(salon=salon)
    response = client.get(url("dates", salon, service=service.pk, staff=other.pk))
    assert response.status_code == 404


def test_slots_step_shows_free_times(client, setup, day):
    salon, service, staff = setup
    BookingFactory(salon=salon, service=service, staff=staff, start_at=ten(day))
    html = client.get(
        url("slots", salon, service=service.pk, staff=staff.pk, date=day.isoformat()),
        **HX,
    ).content.decode()
    assert "10:00" not in html  # booked
    assert "11:00" in html


def test_slots_step_any_staff(client, setup, day):
    salon, service, _ = setup
    html = client.get(
        url("slots", salon, service=service.pk, staff="any", date=day.isoformat()), **HX
    ).content.decode()
    assert "10:00" in html


def test_slots_empty_day_message(client, setup, day):
    salon, service, staff = setup
    other_day = day + timedelta(days=1)
    html = client.get(
        url("slots", salon, service=service.pk, staff=staff.pk, date=other_day),
        **HX,
    ).content.decode()
    assert "No free time" in html


@pytest.mark.parametrize("offset", [-1, 31])
def test_slots_date_out_of_range_404(client, setup, offset):
    salon, service, staff = setup
    bad = timezone.now().astimezone(TZ).date() + timedelta(days=offset)
    response = client.get(
        url("slots", salon, service=service.pk, staff=staff.pk, date=bad.isoformat())
    )
    assert response.status_code == 404


def test_slots_bad_date_404(client, setup):
    salon, service, staff = setup
    response = client.get(
        url("slots", salon, service=service.pk, staff=staff.pk, date="nope")
    )
    assert response.status_code == 404


def test_details_step_renders_form(client, setup, day):
    salon, service, staff = setup
    response = client.get(
        url("details", salon, service=service.pk, staff=staff.pk, start=ten(day)),
        **HX,
    )
    html = response.content.decode()
    assert response.status_code == 200
    assert 'name="full_name"' in html and 'name="phone"' in html
    assert "csrfmiddlewaretoken" in html


@pytest.mark.parametrize("start", ["", "garbage", "2030-01-07T10:00:00"])
def test_details_bad_start_404(client, setup, start):
    salon, service, staff = setup
    response = client.get(
        url("details", salon, service=service.pk, staff=staff.pk, start=start)
    )
    assert response.status_code == 404


def test_confirm_creates_booking(client, setup, day):
    salon, service, staff = setup
    response = client.post(
        url("confirm", salon), post_data(service, staff, ten(day)), **HX
    )
    html = response.content.decode()
    assert response.status_code == 200
    assert "booked" in html
    booking = Booking.objects.get()
    assert booking.staff == staff
    assert booking.start_at == ten(day)
    assert booking.client.full_name == "Nino Beridze"
    assert booking.client.phone == "+995555123456"


def test_confirm_any_staff(client, setup, day):
    salon, service, staff = setup
    client.post(url("confirm", salon), post_data(service, None, ten(day)), **HX)
    assert Booking.objects.get().staff == staff


def test_confirm_requires_csrf(setup, day):
    from django.test import Client

    salon, service, staff = setup
    strict = Client(enforce_csrf_checks=True)
    response = strict.post(url("confirm", salon), post_data(service, staff, ten(day)))
    assert response.status_code == 403


def test_confirm_slot_taken_shows_error(client, setup, day):
    salon, service, staff = setup
    BookingFactory(salon=salon, service=service, staff=staff, start_at=ten(day))
    response = client.post(
        url("confirm", salon), post_data(service, staff, ten(day)), **HX
    )
    html = response.content.decode()
    assert response.status_code == 200
    assert "no longer available" in html
    assert "See free times" in html
    assert Booking.objects.count() == 1


def test_confirm_invalid_phone_shows_field_error(client, setup, day):
    salon, service, staff = setup
    response = client.post(
        url("confirm", salon), post_data(service, staff, ten(day), phone="12"), **HX
    )
    assert "valid phone number" in response.content.decode()
    assert not Booking.objects.exists()


def test_confirm_tampered_service_404(client, setup, day):
    salon, _, staff = setup
    foreign = ServiceFactory()
    response = client.post(
        url("confirm", salon), post_data(foreign, staff, ten(day)), **HX
    )
    assert response.status_code == 404
    assert not Booking.objects.exists()


def test_confirm_get_not_allowed(client, setup):
    salon, *_ = setup
    assert client.get(url("confirm", salon)).status_code == 405


def test_head_requests_allowed_for_uptime_monitors(client, setup):
    salon, *_ = setup
    assert client.head(url("services", salon)).status_code == 200
