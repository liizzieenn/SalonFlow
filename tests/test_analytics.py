from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from apps.bookings.models import Booking
from apps.dashboard import analytics as an
from tests.factories import BookingFactory, SalonFactory, ServiceFactory, StaffFactory

pytestmark = pytest.mark.django_db
TZ = ZoneInfo("Asia/Tbilisi")
TODAY = date(2030, 3, 10)
NOW = datetime.combine(TODAY, time(12), tzinfo=TZ)
S = Booking.Status


@pytest.fixture
def salon():
    return SalonFactory()


@pytest.fixture
def make(salon):
    """Create a booking: ``make(day_offset, hour, service, status=...)``."""
    staff = StaffFactory(salon=salon)

    def _make(offset: int, hour: int, service, status=S.CONFIRMED, minute=0):
        start = datetime.combine(
            TODAY + timedelta(days=offset), time(hour, minute), tzinfo=TZ
        )
        return BookingFactory(
            salon=salon, staff=staff, service=service, start_at=start, status=status
        )

    return _make


@pytest.fixture
def services(salon):
    return {
        "cut": ServiceFactory(
            salon=salon, name="Haircut", price=Decimal("40"), duration_minutes=30
        ),
        "nails": ServiceFactory(
            salon=salon, name="Manicure", price=Decimal("25"), duration_minutes=30
        ),
    }


@pytest.fixture
def period(salon):
    return an.Period.last_days(salon, 7, TODAY)


def test_period_is_salon_local_and_includes_today(salon, period):
    assert period.start == datetime(2030, 3, 4, tzinfo=TZ)
    assert period.end == datetime(2030, 3, 11, tzinfo=TZ)
    assert period.previous == an.Period(datetime(2030, 2, 25, tzinfo=TZ), period.start)
    assert len(period.dates(TZ)) == 7


def test_summary(salon, make, services, period):
    make(-1, 10, services["cut"], S.COMPLETED)
    make(-1, 11, services["cut"])  # past & confirmed: counts as attended
    make(-2, 10, services["nails"], S.NO_SHOW)
    make(-2, 11, services["nails"], S.CANCELLED)
    make(0, 15, services["cut"])  # later today: booked, not yet attended
    make(-10, 10, services["cut"], S.COMPLETED)  # outside period

    result = an.summary(salon, period, NOW)
    assert result == an.Summary(
        revenue=Decimal("80.00"), booked=4, attended=2, no_shows=1, cancelled=1
    )
    assert result.average_value == Decimal("40.00")
    assert result.no_show_rate == pytest.approx(1 / 3)
    assert result.cancellation_rate == pytest.approx(1 / 5)


def test_summary_empty(salon, period):
    result = an.summary(salon, period, NOW)
    assert result.revenue == Decimal("0.00")
    assert result.average_value is None
    assert result.no_show_rate is None
    assert result.cancellation_rate is None


def test_other_salon_excluded(salon, services, period):
    BookingFactory(start_at=NOW - timedelta(days=1), status=S.COMPLETED)
    assert an.summary(salon, period, NOW).booked == 0


def test_revenue_by_day_fills_gaps(salon, make, services, period):
    make(-1, 10, services["cut"], S.COMPLETED)
    make(-1, 11, services["nails"], S.COMPLETED)
    make(-3, 10, services["nails"], S.NO_SHOW)  # no revenue
    series = an.revenue_by_day(salon, period, NOW)
    assert len(series) == 7
    assert dict(series)[TODAY - timedelta(days=1)] == Decimal("65.00")
    assert sum(v for _, v in series) == Decimal("65.00")


def test_revenue_day_uses_salon_timezone(salon, make, services, period):
    # 00:30 Tbilisi is 20:30 UTC the previous day.
    make(-1, 0, services["cut"], S.COMPLETED, minute=30)
    assert dict(an.revenue_by_day(salon, period, NOW))[TODAY - timedelta(days=1)] == 40


def test_bookings_by_hour(salon, make, services, period):
    make(-1, 10, services["cut"])
    make(-2, 10, services["cut"])
    make(-2, 13, services["nails"])
    make(-3, 11, services["nails"], S.CANCELLED)
    assert an.bookings_by_hour(salon, period) == [(10, 2), (11, 0), (12, 0), (13, 1)]


def test_bookings_by_hour_empty(salon, period):
    assert an.bookings_by_hour(salon, period) == []


def test_top_services(salon, make, services, period):
    make(-1, 10, services["nails"], S.COMPLETED)
    make(-2, 10, services["nails"], S.NO_SHOW)
    make(-3, 10, services["cut"], S.COMPLETED)
    make(-3, 11, services["cut"], S.CANCELLED)
    assert an.top_services(salon, period, NOW) == [
        an.ServiceStats("Manicure", 2, Decimal("25.00")),
        an.ServiceStats("Haircut", 1, Decimal("40.00")),
    ]
    assert len(an.top_services(salon, period, NOW, limit=1)) == 1


@pytest.mark.parametrize(
    ("current", "previous", "expected"),
    [(150, 100, 0.5), (50, 100, -0.5), (10, 0, None), (0, 0, None)],
)
def test_change(current, previous, expected):
    assert an.change(current, previous) == expected
