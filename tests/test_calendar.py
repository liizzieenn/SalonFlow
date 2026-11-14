from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from apps.bookings.models import Booking
from apps.dashboard.calendar import (
    PlacedBooking,
    assign_lanes,
    build_week,
    week_start_for,
)
from tests.factories import (
    BookingFactory,
    SalonFactory,
    ServiceFactory,
    StaffFactory,
    WorkingHoursFactory,
)

TZ = ZoneInfo("Asia/Tbilisi")
MONDAY = date(2030, 1, 7)


def at(day_offset: int, h: int, m: int = 0) -> datetime:
    return datetime.combine(MONDAY + timedelta(days=day_offset), time(h, m), tzinfo=TZ)


def test_week_start_for():
    assert week_start_for(date(2030, 1, 10)) == MONDAY
    assert week_start_for(MONDAY) == MONDAY


class FakeBooking:
    def __init__(self, start, end):
        self.start_at, self.end_at = start, end


def lanes(*spans):
    placed = [PlacedBooking(FakeBooking(at(0, a), at(0, b)), 0, 0) for a, b in spans]
    assign_lanes(placed)
    return [(p.lane, p.lanes) for p in placed]


def test_assign_lanes_no_overlap():
    assert lanes((10, 11), (11, 12)) == [(0, 1), (0, 1)]


def test_assign_lanes_overlap_and_reuse():
    # 10-12 and 11-13 overlap; 12-13 reuses lane 0 in the same cluster.
    assert lanes((10, 12), (11, 13), (12, 13)) == [(0, 2), (1, 2), (0, 2)]


def test_assign_lanes_separate_clusters():
    assert lanes((10, 12), (11, 12), (14, 15)) == [(0, 2), (1, 2), (0, 1)]


@pytest.mark.django_db
class TestBuildWeek:
    @pytest.fixture
    def salon(self):
        salon = SalonFactory()
        staff = StaffFactory(salon=salon)
        WorkingHoursFactory(staff=staff, start_time=time(10), end_time=time(18, 30))
        return salon

    def book(self, salon, start, staff=None, **kw):
        service = ServiceFactory(salon=salon, duration_minutes=60)
        staff = staff or StaffFactory(salon=salon)
        return BookingFactory(
            salon=salon, service=service, staff=staff, start_at=start, **kw
        )

    def test_hours_follow_working_hours(self, salon):
        week = build_week(salon, MONDAY)
        assert (week.first_hour, week.last_hour) == (10, 19)
        assert week.grid_height_px == 9 * week.hour_height_px
        assert week.prev_week == MONDAY - timedelta(days=7)
        assert [d.day for d in week.days][-1] == MONDAY + timedelta(days=6)

    def test_default_hours_without_staff(self):
        week = build_week(SalonFactory(), MONDAY)
        assert (week.first_hour, week.last_hour) == (9, 20)

    def test_booking_positioned_in_its_day(self, salon):
        booking = self.book(salon, at(2, 12))
        week = build_week(salon, MONDAY)
        placed = week.days[2].bookings
        assert [p.booking for p in placed] == [booking]
        assert placed[0].top == pytest.approx(100 * 2 / 9)
        assert placed[0].height == pytest.approx(100 * 1 / 9)

    def test_range_grows_for_early_booking(self, salon):
        self.book(salon, at(0, 8, 30))
        assert build_week(salon, MONDAY).first_hour == 8

    def test_excludes_cancelled_other_weeks_and_salons(self, salon):
        self.book(salon, at(1, 11), status=Booking.Status.CANCELLED)
        self.book(salon, at(7, 11))  # next week
        BookingFactory(start_at=at(1, 11))  # other salon
        assert all(not d.bookings for d in build_week(salon, MONDAY).days)

    def test_staff_filter(self, salon):
        ana, nino = StaffFactory(salon=salon), StaffFactory(salon=salon)
        mine = self.book(salon, at(0, 11), staff=ana)
        self.book(salon, at(0, 11), staff=nino)
        week = build_week(salon, MONDAY, staff_id=ana.pk)
        assert [p.booking for p in week.days[0].bookings] == [mine]

    def test_all_staff_view_gives_each_specialist_a_fixed_lane(self, salon):
        ana = StaffFactory(salon=salon, full_name="Ana")
        nino = StaffFactory(salon=salon, full_name="Nino")
        self.book(salon, at(0, 11), staff=nino)
        self.book(salon, at(2, 15), staff=ana)
        self.book(salon, at(2, 11), staff=nino)
        week = build_week(salon, MONDAY)
        lanes = {
            (d.day.weekday(), p.booking.staff.full_name): (p.lane, p.lanes)
            for d in week.days
            for p in d.bookings
        }
        assert lanes == {(0, "Nino"): (1, 2), (2, "Ana"): (0, 2), (2, "Nino"): (1, 2)}
        assert week.lane_staff == [ana, nino]

    def test_single_staff_view_uses_full_width(self, salon):
        ana = StaffFactory(salon=salon)
        self.book(salon, at(0, 11), staff=ana)
        self.book(salon, at(0, 13), staff=ana)
        placed = build_week(salon, MONDAY, staff_id=ana.pk).days[0].bookings
        assert [(p.lane, p.lanes, p.width) for p in placed] == [
            (0, 1, 100),
            (0, 1, 100),
        ]
