import threading
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest
from django.db import connection

from apps.bookings.models import Booking
from apps.bookings.services import (
    ClientDetails,
    InvalidSelectionError,
    InvalidStatusChangeError,
    SlotUnavailableError,
    allowed_status_changes,
    cancel_booking,
    change_status,
    create_booking,
    get_or_create_client,
)
from apps.clients.models import Client
from apps.salons.models import Weekday
from tests.factories import (
    ServiceFactory,
    StaffFactory,
    UserFactory,
    WorkingHoursFactory,
)

TZ = ZoneInfo("Asia/Tbilisi")
MONDAY = date(2030, 1, 7)
NOW = datetime(2030, 1, 1, 9, 0, tzinfo=TZ)
TEN = datetime.combine(MONDAY, time(10), tzinfo=TZ)
DETAILS = ClientDetails(full_name="Nino Beridze", phone="+995555123456")


@pytest.fixture
def service(db):
    return ServiceFactory(duration_minutes=60, buffer_minutes=0)


def make_staff(service, start=10, end=19):
    member = StaffFactory(salon=service.salon, services=[service])
    WorkingHoursFactory(
        staff=member, weekday=Weekday.MONDAY, start_time=time(start), end_time=time(end)
    )
    return member


@pytest.fixture
def staff(service):
    return make_staff(service)


def book(service, staff=None, start=TEN, details=DETAILS, **kw):
    return create_booking(
        service=service,
        staff=staff,
        start_at=start,
        client_details=details,
        now=NOW,
        **kw,
    )


@pytest.mark.django_db
class TestCreateBooking:
    def test_creates_confirmed_booking_and_client(self, service, staff):
        booking = book(service, staff, notes="first visit")
        assert booking.pk
        assert booking.status == Booking.Status.CONFIRMED
        assert booking.staff == staff
        assert booking.price == service.price
        assert booking.notes == "first visit"
        assert booking.client.phone == DETAILS.phone
        assert booking.client.salon == service.salon

    def test_same_slot_twice_rejected(self, service, staff):
        book(service, staff)
        with pytest.raises(SlotUnavailableError):
            book(service, staff, details=ClientDetails("Other", "+995555000999"))
        assert Booking.objects.count() == 1

    def test_off_grid_or_outside_hours_rejected(self, service, staff):
        with pytest.raises(SlotUnavailableError):
            book(service, staff, start=TEN.replace(minute=7))
        with pytest.raises(SlotUnavailableError):
            book(service, staff, start=TEN.replace(hour=8))

    def test_past_slot_rejected(self, service, staff):
        with pytest.raises(SlotUnavailableError):
            create_booking(
                service=service,
                staff=staff,
                start_at=TEN,
                client_details=DETAILS,
                now=TEN.replace(hour=11),
            )

    def test_any_staff_picks_first_free(self, service, staff):
        second = make_staff(service)
        assert book(service).staff == staff
        assert (
            book(service, details=ClientDetails("B", "+995555000002")).staff == second
        )
        with pytest.raises(SlotUnavailableError):
            book(service, details=ClientDetails("C", "+995555000003"))

    def test_any_staff_skips_inactive(self, service, staff):
        staff.is_active = False
        staff.save()
        second = make_staff(service)
        assert book(service).staff == second

    def test_inactive_service(self, service, staff):
        service.is_active = False
        service.save()
        with pytest.raises(InvalidSelectionError):
            book(service, staff)

    def test_staff_from_other_salon(self, service):
        foreign = StaffFactory()
        with pytest.raises(InvalidSelectionError):
            book(service, foreign)

    def test_staff_not_offering_service(self, service, staff):
        other_service = ServiceFactory(salon=service.salon)
        with pytest.raises(SlotUnavailableError):
            book(other_service, staff)

    def test_links_logged_in_user(self, service, staff):
        user = UserFactory()
        assert book(service, staff, user=user).client.user == user


@pytest.mark.django_db
class TestClients:
    def test_reuses_client_by_phone_and_updates_details(self, service):
        first = get_or_create_client(service.salon, DETAILS)
        again = get_or_create_client(
            service.salon,
            ClientDetails("Nino B.", DETAILS.phone, email="nino@example.com"),
        )
        assert again.pk == first.pk
        again.refresh_from_db()
        assert again.full_name == "Nino B."
        assert again.email == "nino@example.com"

    def test_same_phone_other_salon_is_new_client(self, service):
        get_or_create_client(service.salon, DETAILS)
        get_or_create_client(ServiceFactory().salon, DETAILS)
        assert Client.objects.filter(phone=DETAILS.phone).count() == 2


@pytest.mark.django_db
def test_cancel_frees_slot(service, staff):
    booking = book(service, staff)
    cancel_booking(booking)
    cancel_booking(booking)  # idempotent
    assert booking.status == Booking.Status.CANCELLED
    assert book(service, staff, details=ClientDetails("B", "+995555000002")).pk


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("pick_staff", [True, False], ids=["chosen-staff", "any-staff"])
def test_concurrent_requests_cannot_double_book(pick_staff):
    """Many simultaneous requests for one slot: exactly one wins, no overlap."""
    service = ServiceFactory(duration_minutes=60, buffer_minutes=0)
    staff = make_staff(service)
    workers = 8
    barrier = threading.Barrier(workers)
    results: list[str] = []
    lock = threading.Lock()

    def attempt(i: int) -> None:
        try:
            barrier.wait()
            create_booking(
                service=service,
                staff=staff if pick_staff else None,
                start_at=TEN,
                client_details=ClientDetails(f"Client {i}", f"+99555500{i:04d}"),
                now=NOW,
            )
            outcome = "ok"
        except SlotUnavailableError:
            outcome = "taken"
        except Exception as exc:  # pragma: no cover - would fail the assertion below
            outcome = f"error: {exc!r}"
        finally:
            connection.close()
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results) == ["ok"] + ["taken"] * (workers - 1)
    assert Booking.objects.filter(staff=staff).count() == 1


@pytest.mark.django_db
def test_db_constraint_is_last_line_of_defence(service, staff, monkeypatch):
    """Even if the availability check were wrong, the overlap is still refused."""
    book(service, staff)
    monkeypatch.setattr(
        "apps.bookings.services.is_slot_available", lambda *a, **kw: True
    )
    with pytest.raises(SlotUnavailableError):
        book(service, staff, details=ClientDetails("B", "+995555000002"))
    assert Booking.objects.count() == 1


@pytest.mark.django_db
class TestChangeStatus:
    @pytest.fixture
    def booking(self, service, staff):
        return book(service, staff)  # confirmed, starts at TEN

    def test_future_booking_can_only_be_cancelled(self, booking):
        assert allowed_status_changes(booking, now=NOW) == ["cancelled"]

    def test_started_booking_can_be_completed_or_no_show(self, booking):
        after = TEN.replace(hour=11)
        assert allowed_status_changes(booking, now=after) == [
            "completed",
            "no_show",
            "cancelled",
        ]
        change_status(booking, "no_show", now=after)
        booking.refresh_from_db()
        assert booking.status == "no_show"
        assert allowed_status_changes(booking, now=after) == ["completed"]

    def test_pending_can_be_confirmed(self, booking):
        booking.status = Booking.Status.PENDING
        assert "confirmed" in allowed_status_changes(booking, now=NOW)

    def test_cancelled_is_final(self, booking):
        change_status(booking, "cancelled", now=NOW)
        assert allowed_status_changes(booking, now=NOW) == []
        with pytest.raises(InvalidStatusChangeError):
            change_status(booking, "confirmed", now=NOW)

    @pytest.mark.parametrize("status", ["completed", "no_show", "bogus", ""])
    def test_invalid_change_rejected(self, booking, status):
        with pytest.raises(InvalidStatusChangeError):
            change_status(booking, status, now=NOW)
        booking.refresh_from_db()
        assert booking.status == "confirmed"
