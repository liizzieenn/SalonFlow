"""Owner analytics: revenue, bookings, busiest hours, top services, no-show rate.

Definitions (period = bookings whose start is in ``[start, end)``):

* **Booked** — every non-cancelled booking.
* **Attended** — ``completed``, or ``pending``/``confirmed`` whose end has passed
  (staff don't always mark completion; anything not flagged as cancelled or
  no-show is assumed to have happened).
* **Revenue** — sum of the price snapshot of attended bookings.
* **No-show rate** — no-shows / (attended + no-shows), i.e. among finished visits.

All grouping by day and hour happens in the salon's time zone, in the database.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.db.models import Count, Q, QuerySet, Sum
from django.db.models.functions import ExtractHour, TruncDate

from apps.bookings.models import Booking
from apps.salons.models import Salon

S = Booking.Status
ZERO = Decimal("0.00")


@dataclass(frozen=True)
class Period:
    start: datetime
    end: datetime

    @classmethod
    def last_days(cls, salon: Salon, days: int, today: date) -> Period:
        """The ``days`` days ending with (and including) ``today``, salon-local."""
        tz = ZoneInfo(salon.timezone)
        end = datetime.combine(today + timedelta(days=1), time.min, tzinfo=tz)
        return cls(end - timedelta(days=days), end)

    @property
    def previous(self) -> Period:
        return Period(self.start - (self.end - self.start), self.start)

    def dates(self, tz: ZoneInfo) -> list[date]:
        first = self.start.astimezone(tz).date()
        return [first + timedelta(days=i) for i in range((self.end - self.start).days)]


@dataclass(frozen=True)
class Summary:
    revenue: Decimal
    booked: int
    attended: int
    no_shows: int
    cancelled: int

    @property
    def average_value(self) -> Decimal | None:
        return (
            (self.revenue / self.attended).quantize(Decimal("0.01"))
            if self.attended
            else None
        )

    @property
    def no_show_rate(self) -> float | None:
        finished = self.attended + self.no_shows
        return self.no_shows / finished if finished else None

    @property
    def cancellation_rate(self) -> float | None:
        total = self.booked + self.cancelled
        return self.cancelled / total if total else None


def _attended_q(now: datetime) -> Q:
    return Q(status=S.COMPLETED) | Q(
        status__in=[S.PENDING, S.CONFIRMED], end_at__lte=now
    )


def _in_period(salon: Salon, period: Period) -> QuerySet[Booking]:
    return Booking.objects.filter(
        salon=salon, start_at__gte=period.start, start_at__lt=period.end
    )


def summary(salon: Salon, period: Period, now: datetime) -> Summary:
    attended = _attended_q(now)
    row = _in_period(salon, period).aggregate(
        revenue=Sum("price", filter=attended),
        booked=Count("pk", filter=~Q(status=S.CANCELLED)),
        attended=Count("pk", filter=attended),
        no_shows=Count("pk", filter=Q(status=S.NO_SHOW)),
        cancelled=Count("pk", filter=Q(status=S.CANCELLED)),
    )
    return Summary(
        revenue=row["revenue"] or ZERO,
        booked=row["booked"],
        attended=row["attended"],
        no_shows=row["no_shows"],
        cancelled=row["cancelled"],
    )


def revenue_by_day(
    salon: Salon, period: Period, now: datetime
) -> list[tuple[date, Decimal]]:
    """Revenue per salon-local day, every day of the period present (0 if none)."""
    tz = ZoneInfo(salon.timezone)
    rows = (
        _in_period(salon, period)
        .filter(_attended_q(now))
        .annotate(day=TruncDate("start_at", tzinfo=tz))
        .values("day")
        .annotate(total=Sum("price"))
    )
    totals = {r["day"]: r["total"] for r in rows}
    return [(d, totals.get(d, ZERO)) for d in period.dates(tz)]


def bookings_by_hour(salon: Salon, period: Period) -> list[tuple[int, int]]:
    """Non-cancelled bookings per starting hour (earliest to latest hour seen)."""
    tz = ZoneInfo(salon.timezone)
    rows = (
        _in_period(salon, period)
        .exclude(status=S.CANCELLED)
        .annotate(hour=ExtractHour("start_at", tzinfo=tz))
        .values("hour")
        .annotate(n=Count("pk"))
    )
    counts = {r["hour"]: r["n"] for r in rows}
    if not counts:
        return []
    return [(h, counts.get(h, 0)) for h in range(min(counts), max(counts) + 1)]


@dataclass(frozen=True)
class ServiceStats:
    name: str
    bookings: int
    revenue: Decimal


def top_services(
    salon: Salon, period: Period, now: datetime, limit: int = 5
) -> list[ServiceStats]:
    """Services ranked by number of (non-cancelled) bookings, then revenue."""
    attended = _attended_q(now)
    rows = (
        _in_period(salon, period)
        .exclude(status=S.CANCELLED)
        .values("service__name")
        .annotate(n=Count("pk"), revenue=Sum("price", filter=attended))
        .order_by("-n", "-revenue", "service__name")[:limit]
    )
    return [
        ServiceStats(r["service__name"], r["n"], r["revenue"] or ZERO) for r in rows
    ]


def change(current: Decimal | int, previous: Decimal | int) -> float | None:
    """Relative change vs. the previous period, or ``None`` when undefined."""
    if not previous:
        return None
    return float((current - previous) / previous)
