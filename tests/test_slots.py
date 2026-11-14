from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from apps.bookings.models import Booking
from apps.bookings.slots import (
    Interval,
    available_slots,
    available_slots_any_staff,
    is_slot_available,
    slot_starts,
    subtract,
)
from apps.salons.models import Break, TimeOff, Weekday
from tests.factories import (
    BookingFactory,
    ClientFactory,
    ServiceFactory,
    StaffFactory,
    WorkingHoursFactory,
)

TZ = ZoneInfo("Asia/Tbilisi")
MONDAY = date(2030, 1, 7)
NOW = datetime(2030, 1, 1, 9, 0, tzinfo=TZ)  # well before MONDAY


def at(h: int, m: int = 0, day: date = MONDAY) -> datetime:
    return datetime.combine(day, time(h, m), tzinfo=TZ)


def hm(slots: list[datetime]) -> list[str]:
    return [s.astimezone(TZ).strftime("%H:%M") for s in slots]


# --- pure interval helpers -------------------------------------------------------


class TestSubtract:
    def test_no_busy(self):
        assert subtract([Interval(at(10), at(12))], []) == [Interval(at(10), at(12))]

    def test_busy_in_middle_splits(self):
        assert subtract([Interval(at(10), at(18))], [Interval(at(13), at(14))]) == [
            Interval(at(10), at(13)),
            Interval(at(14), at(18)),
        ]

    def test_busy_covers_edges_and_whole(self):
        free = [Interval(at(10), at(12)), Interval(at(14), at(16))]
        busy = [Interval(at(9), at(10, 30)), Interval(at(13), at(17))]
        assert subtract(free, busy) == [Interval(at(10, 30), at(12))]

    def test_touching_intervals_do_not_overlap(self):
        assert subtract([Interval(at(10), at(12))], [Interval(at(12), at(13))]) == [
            Interval(at(10), at(12))
        ]

    def test_unsorted_overlapping_busy(self):
        busy = [
            Interval(at(15), at(16)),
            Interval(at(11), at(13)),
            Interval(at(12), at(14)),
        ]
        assert subtract([Interval(at(10), at(17))], busy) == [
            Interval(at(10), at(11)),
            Interval(at(14), at(15)),
            Interval(at(16), at(17)),
        ]


class TestSlotStarts:
    def test_block_must_fit(self):
        starts = slot_starts(
            [Interval(at(10), at(11, 30))], timedelta(hours=1), timedelta(minutes=15)
        )
        assert hm(starts) == ["10:00", "10:15", "10:30"]

    def test_aligned_to_clock_grid(self):
        starts = slot_starts(
            [Interval(at(10, 10), at(11, 30))],
            timedelta(hours=1),
            timedelta(minutes=15),
        )
        assert hm(starts) == ["10:15", "10:30"]

    def test_not_before(self):
        starts = slot_starts(
            [Interval(at(10), at(12))],
            timedelta(hours=1),
            timedelta(minutes=30),
            not_before=at(10, 1),
        )
        assert hm(starts) == ["10:30", "11:00"]


# --- available_slots with real data ----------------------------------------------


@pytest.fixture
def service(db):
    return ServiceFactory(duration_minutes=60, buffer_minutes=0)


@pytest.fixture
def staff(service):
    member = StaffFactory(salon=service.salon, services=[service])
    WorkingHoursFactory(
        staff=member, weekday=Weekday.MONDAY, start_time=time(10), end_time=time(14)
    )
    return member


def slots(staff, service, day=MONDAY, now=NOW, **kw):
    return hm(available_slots(staff, service, day, now=now, **kw))


def book(staff, service, start, **kwargs):
    return BookingFactory(
        salon=staff.salon, staff=staff, service=service, start_at=start, **kwargs
    )


pytestmark = pytest.mark.django_db


def test_whole_shift(staff, service):
    assert slots(staff, service, step_minutes=60) == [
        "10:00",
        "11:00",
        "12:00",
        "13:00",
    ]


def test_returns_aware_datetimes_in_salon_tz(staff, service):
    first = available_slots(staff, service, MONDAY, now=NOW)[0]
    assert first == at(10)
    assert first.utcoffset() == timedelta(hours=4)


def test_day_without_working_hours(staff, service):
    assert slots(staff, service, day=MONDAY + timedelta(days=1)) == []


def test_split_shifts(staff, service):
    WorkingHoursFactory(staff=staff, start_time=time(16), end_time=time(18))
    assert slots(staff, service, step_minutes=60) == [
        "10:00", "11:00", "12:00", "13:00", "16:00", "17:00",
    ]  # fmt: skip


def test_break_removed(staff, service):
    Break.objects.create(
        staff=staff, weekday=Weekday.MONDAY, start_time=time(12), end_time=time(12, 30)
    )
    assert slots(staff, service, step_minutes=30) == [
        "10:00", "10:30", "11:00", "12:30", "13:00",
    ]  # fmt: skip


def test_break_on_other_weekday_ignored(staff, service):
    Break.objects.create(
        staff=staff, weekday=Weekday.TUESDAY, start_time=time(10), end_time=time(14)
    )
    assert len(slots(staff, service, step_minutes=60)) == 4


def test_time_off(staff, service):
    TimeOff.objects.create(staff=staff, start_at=at(9), end_at=at(12))
    assert slots(staff, service, step_minutes=60) == ["12:00", "13:00"]


def test_multi_day_time_off(staff, service):
    TimeOff.objects.create(
        staff=staff, start_at=at(0, day=MONDAY - timedelta(days=3)), end_at=at(23)
    )
    assert slots(staff, service) == []


def test_existing_booking_and_its_buffer_blocked(staff, service):
    other = ServiceFactory(salon=service.salon, duration_minutes=30, buffer_minutes=15)
    staff.services.add(other)
    book(staff, other, at(11))  # busy 11:00–11:45 incl. buffer
    assert slots(staff, service, step_minutes=15) == [
        "10:00", "11:45", "12:00", "12:15", "12:30", "12:45", "13:00",
    ]  # fmt: skip


def test_new_service_buffer_must_fit(staff):
    service = ServiceFactory(salon=staff.salon, duration_minutes=60, buffer_minutes=30)
    staff.services.add(service)
    # 13:00 start would need until 14:30 — past end of shift.
    assert slots(staff, service, step_minutes=60) == ["10:00", "11:00", "12:00"]


def test_cancelled_booking_ignored(staff, service):
    book(staff, service, at(10), status=Booking.Status.CANCELLED)
    assert "10:00" in slots(staff, service)


def test_other_staff_booking_ignored(staff, service):
    other = StaffFactory(salon=staff.salon, services=[service])
    book(other, service, at(10))
    assert "10:00" in slots(staff, service)


def test_past_slots_excluded(staff, service):
    assert slots(staff, service, now=at(11, 5), step_minutes=60) == ["12:00", "13:00"]


def test_staff_not_offering_service(staff):
    assert slots(staff, ServiceFactory(salon=staff.salon)) == []


def test_inactive_staff_or_service(staff, service):
    staff.is_active = False
    assert slots(staff, service) == []
    staff.is_active = True
    service.is_active = False
    assert slots(staff, service) == []


def test_any_staff_merges_and_lists_who_is_free(staff, service):
    other = StaffFactory(salon=staff.salon, services=[service])
    WorkingHoursFactory(staff=other, start_time=time(13), end_time=time(15))
    book(staff, service, at(13), client=ClientFactory(salon=staff.salon))

    result = available_slots_any_staff(service, MONDAY, step_minutes=60, now=NOW)
    assert {hm([k])[0]: v for k, v in result.items()} == {
        "10:00": [staff],
        "11:00": [staff],
        "12:00": [staff],
        "13:00": [other],
        "14:00": [other],
    }


def test_is_slot_available(staff, service):
    assert is_slot_available(staff, service, at(10), now=NOW)
    assert not is_slot_available(staff, service, at(10, 7), now=NOW)  # off-grid
    assert not is_slot_available(staff, service, at(13, 30), now=NOW)  # overruns shift
    book(staff, service, at(10))
    assert not is_slot_available(staff, service, at(10, 30), now=NOW)
