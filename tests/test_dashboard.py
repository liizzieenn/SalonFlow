from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.bookings.models import Booking
from tests.factories import (
    BookingFactory,
    SalonFactory,
    ServiceFactory,
    StaffFactory,
    UserFactory,
)

pytestmark = pytest.mark.django_db
TZ = ZoneInfo("Asia/Tbilisi")
HX = {"HTTP_HX_REQUEST": "true"}


@pytest.fixture
def world():
    """A salon with an owner, two staff (one with a login) and bookings for each."""
    owner = UserFactory(username="owner")
    salon = SalonFactory(owner=owner, slug="kb", name="Kutaisi Beauty")
    ana_user = UserFactory(username="ana")
    ana = StaffFactory(salon=salon, full_name="Ana", user=ana_user)
    nino = StaffFactory(salon=salon, full_name="Nino")
    service = ServiceFactory(salon=salon, name="Haircut", duration_minutes=60)
    today = timezone.now().astimezone(TZ).date()
    start = datetime.combine(today, time(11), tzinfo=TZ)
    ana_booking = BookingFactory(
        salon=salon, staff=ana, service=service, start_at=start,
        client__full_name="Client of Ana", status=Booking.Status.CONFIRMED,
    )  # fmt: skip
    nino_booking = BookingFactory(
        salon=salon, staff=nino, service=service, start_at=start,
        client__full_name="Client of Nino", status=Booking.Status.CONFIRMED,
    )  # fmt: skip
    return {
        "salon": salon, "owner": owner, "ana_user": ana_user, "ana": ana,
        "ana_booking": ana_booking, "nino_booking": nino_booking,
    }  # fmt: skip


def login(user) -> Client:
    client = Client()
    client.force_login(user)
    return client


def cal(salon, **params):
    from urllib.parse import urlencode

    url = reverse("dashboard:calendar", args=[salon.slug])
    return f"{url}?{urlencode(params)}" if params else url


# --- authentication & access -------------------------------------------------------


@pytest.mark.parametrize("name", ["home", "calendar", "analytics"])
def test_anonymous_redirected_to_login(client, world, name):
    args = [] if name == "home" else [world["salon"].slug]
    response = client.get(reverse(f"dashboard:{name}", args=args))
    assert response.status_code == 302
    assert response.url.startswith(reverse("login"))


def test_login_page_and_login_flow(client, world):
    world["owner"].set_password("s3cret-pass")
    world["owner"].save()
    assert client.get(reverse("login")).status_code == 200
    response = client.post(
        reverse("login"), {"username": "owner", "password": "s3cret-pass"}, follow=True
    )
    assert response.redirect_chain[-1][0] == cal(world["salon"])


def test_logout_requires_post(world):
    client = login(world["owner"])
    assert client.post(reverse("logout")).status_code == 302
    assert client.get(cal(world["salon"])).status_code == 302  # logged out


@pytest.mark.parametrize("name", ["calendar", "analytics"])
def test_outsider_gets_404(world, name):
    outsider = login(UserFactory())
    response = outsider.get(reverse(f"dashboard:{name}", args=[world["salon"].slug]))
    assert response.status_code == 404


def test_owner_of_other_salon_gets_404(world):
    other = SalonFactory()
    response = login(other.owner).get(cal(world["salon"]))
    assert response.status_code == 404


def test_inactive_staff_loses_access(world):
    world["ana"].is_active = False
    world["ana"].save()
    assert login(world["ana_user"]).get(cal(world["salon"])).status_code == 404


def test_home_redirects_to_only_salon(world):
    response = login(world["owner"]).get(reverse("dashboard:home"))
    assert response.status_code == 302 and response.url == cal(world["salon"])


def test_home_lists_multiple_salons(world):
    SalonFactory(owner=world["owner"], name="Second Salon")
    html = login(world["owner"]).get(reverse("dashboard:home")).content.decode()
    assert "Kutaisi Beauty" in html and "Second Salon" in html


def test_home_without_salon(world):
    response = login(UserFactory()).get(reverse("dashboard:home"))
    assert response.status_code == 200
    assert "isn't linked to a salon" in response.content.decode()


# --- calendar ----------------------------------------------------------------------


def test_owner_sees_all_bookings_and_staff_filter(world):
    html = login(world["owner"]).get(cal(world["salon"])).content.decode()
    assert "Client of Ana" in html and "Client of Nino" in html
    assert 'id="staff-filter"' in html
    assert "left to right:" in html
    assert "Analytics" in html


def test_owner_filter_by_staff(world):
    html = login(world["owner"]).get(cal(world["salon"], staff=world["ana"].pk)).content
    assert b"Client of Ana" in html and b"Client of Nino" not in html


def test_owner_filter_ignores_foreign_staff(world):
    foreign = StaffFactory()
    html = login(world["owner"]).get(cal(world["salon"], staff=foreign.pk)).content
    assert b"Client of Nino" in html


def test_staff_sees_only_own_bookings(world):
    html = login(world["ana_user"]).get(cal(world["salon"])).content.decode()
    assert "Client of Ana" in html and "Client of Nino" not in html
    assert 'id="staff-filter"' not in html
    assert "Analytics" not in html


def test_staff_cannot_widen_filter(world):
    html = login(world["ana_user"]).get(cal(world["salon"], staff="")).content
    assert b"Client of Nino" not in html


def test_week_navigation(world):
    next_week = timezone.now().astimezone(TZ).date() + timedelta(days=7)
    html = login(world["owner"]).get(cal(world["salon"], week=next_week)).content
    assert b"Client of Ana" not in html


def test_bad_week_falls_back_to_current(world):
    response = login(world["owner"]).get(cal(world["salon"], week="garbage"))
    assert response.status_code == 200
    assert b"Client of Ana" in response.content


def test_calendar_htmx_returns_grid_fragment(world):
    response = login(world["owner"]).get(cal(world["salon"]), **HX)
    html = response.content.decode()
    assert '<section id="calendar"' in html
    assert "<html" not in html
    assert 'hx-trigger="bookingChanged from:body"' in html


# --- booking detail & status -------------------------------------------------------


def detail_url(world, booking):
    return reverse("dashboard:booking", args=[world["salon"].slug, booking.pk])


def status_url(world, booking):
    return reverse("dashboard:booking_status", args=[world["salon"].slug, booking.pk])


def test_owner_sees_any_booking_detail(world):
    response = login(world["owner"]).get(detail_url(world, world["nino_booking"]), **HX)
    assert response.status_code == 200
    assert b"Client of Nino" in response.content


def test_detail_full_page_without_htmx(world):
    response = login(world["owner"]).get(detail_url(world, world["nino_booking"]))
    assert b"<html" in response.content


def test_staff_detail_only_own(world):
    staff = login(world["ana_user"])
    assert staff.get(detail_url(world, world["ana_booking"]), **HX).status_code == 200
    assert staff.get(detail_url(world, world["nino_booking"]), **HX).status_code == 404


def test_booking_of_other_salon_404(world):
    foreign = BookingFactory()
    assert login(world["owner"]).get(detail_url(world, foreign)).status_code == 404


def test_status_change_by_staff(world):
    booking = world["ana_booking"]
    response = login(world["ana_user"]).post(
        status_url(world, booking), {"status": "cancelled"}, **HX
    )
    assert response.status_code == 200
    assert response["HX-Trigger"] == "bookingChanged"
    booking.refresh_from_db()
    assert booking.status == Booking.Status.CANCELLED


def test_status_change_without_htmx_redirects_to_week(world):
    response = login(world["owner"]).post(
        status_url(world, world["nino_booking"]), {"status": "cancelled"}
    )
    assert response.status_code == 302
    assert "week=" in response.url


def test_staff_cannot_change_colleagues_booking(world):
    booking = world["nino_booking"]
    response = login(world["ana_user"]).post(
        status_url(world, booking), {"status": "cancelled"}, **HX
    )
    assert response.status_code == 404
    booking.refresh_from_db()
    assert booking.status == Booking.Status.CONFIRMED


def test_invalid_status_change_400(world):
    booking = world["ana_booking"]
    booking.status = Booking.Status.CANCELLED
    booking.save()
    response = login(world["owner"]).post(
        status_url(world, booking), {"status": "confirmed"}, **HX
    )
    assert response.status_code == 400
    assert b"not allowed" in response.content


def test_status_change_requires_post_and_csrf(world):
    owner = Client(enforce_csrf_checks=True)
    owner.force_login(world["owner"])
    url = status_url(world, world["ana_booking"])
    assert owner.get(url).status_code == 405
    assert owner.post(url, {"status": "cancelled"}).status_code == 403


# --- analytics ---------------------------------------------------------------------


def analytics_url(world, **params):
    url = reverse("dashboard:analytics", args=[world["salon"].slug])
    return url + (
        "?" + "&".join(f"{k}={v}" for k, v in params.items()) if params else ""
    )


def test_analytics_owner_only(world):
    assert login(world["ana_user"]).get(analytics_url(world)).status_code == 403


def test_analytics_page(world):
    Booking.objects.all().delete()  # fixture bookings may already count as attended
    yesterday = timezone.now() - timedelta(days=1)
    BookingFactory(
        salon=world["salon"], staff=world["ana"], service=world["ana_booking"].service,
        start_at=yesterday.replace(minute=0, second=0, microsecond=0),
        status=Booking.Status.COMPLETED,
    )  # fmt: skip
    response = login(world["owner"]).get(analytics_url(world))
    assert response.status_code == 200
    html = response.content.decode()
    assert 'id="chart-data"' in html and "chart.js@4.4.7" in html
    assert 'id="chart-revenue"' in html and 'id="chart-hours"' in html
    assert response.context["days"] == 30
    assert response.context["summary"].attended == 1
    assert response.context["chart_data"]["services"]["labels"] == ["Haircut"]
    first_day = response.context["by_day"][0][0]
    assert response.context["chart_data"]["revenue"]["labels"][0] == (
        f"{first_day.day} {first_day:%b}"  # English page: "16 Oct"
    )


@pytest.mark.parametrize(
    ("param", "expected"), [("7", 7), ("90", 90), ("5", 30), ("x", 30)]
)
def test_analytics_range(world, param, expected):
    response = login(world["owner"]).get(analytics_url(world, days=param))
    assert response.context["days"] == expected
    assert len(response.context["by_day"]) == expected


def test_analytics_empty_state(world):
    Booking.objects.all().delete()
    html = login(world["owner"]).get(analytics_url(world)).content.decode()
    assert "No revenue in this period yet." in html
    assert 'id="chart-revenue"' not in html
