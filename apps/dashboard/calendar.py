"""Week calendar layout: places bookings on a day × time grid.

In the owner's all-staff view every specialist gets a fixed lane for the week;
for a single specialist, overlapping bookings (rare: only after manual edits)
are split into side-by-side lanes like in most calendar apps.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.db.models import Max, Min

from apps.bookings.models import Booking
from apps.salons.models import Salon, WorkingHours

DEFAULT_HOURS = (9, 20)


@dataclass
class PlacedBooking:
    booking: Booking
    top: float  # % of the grid height
    height: float  # % of the grid height
    lane: int = 0
    lanes: int = 1

    @property
    def left(self) -> float:
        return 100 * self.lane / self.lanes

    @property
    def width(self) -> float:
        return 100 / self.lanes


@dataclass
class CalendarDay:
    day: date
    bookings: list[PlacedBooking] = field(default_factory=list)


@dataclass
class WeekCalendar:
    week_start: date
    first_hour: int
    last_hour: int  # exclusive
    days: list[CalendarDay]
    #: Specialists in lane order (left to right) in the all-staff view.
    lane_staff: list = field(default_factory=list)

    #: Pixel height of one hour row in the grid.
    hour_height_px = 56

    @property
    def grid_height_px(self) -> int:
        return (self.last_hour - self.first_hour) * self.hour_height_px

    @property
    def hours(self) -> list[int]:
        return list(range(self.first_hour, self.last_hour))

    @property
    def prev_week(self) -> date:
        return self.week_start - timedelta(days=7)

    @property
    def next_week(self) -> date:
        return self.week_start + timedelta(days=7)


def week_start_for(day: date) -> date:
    return day - timedelta(days=day.weekday())


def assign_lanes(placed: list[PlacedBooking]) -> None:
    """Give overlapping bookings separate lanes; ``lanes`` is per overlap cluster."""
    placed.sort(key=lambda p: (p.booking.start_at, p.booking.end_at))
    cluster: list[PlacedBooking] = []
    lane_ends: list[datetime] = []
    cluster_end: datetime | None = None

    def close_cluster() -> None:
        for p in cluster:
            p.lanes = len(lane_ends)

    for p in placed:
        start, end = p.booking.start_at, p.booking.end_at
        if cluster_end is not None and start >= cluster_end:
            close_cluster()
            cluster, lane_ends = [], []
        for i, lane_end in enumerate(lane_ends):
            if lane_end <= start:
                p.lane, lane_ends[i] = i, end
                break
        else:
            p.lane = len(lane_ends)
            lane_ends.append(end)
        cluster.append(p)
        cluster_end = (
            end
            if cluster_end is None or start >= cluster_end
            else max(cluster_end, end)
        )
    close_cluster()


def assign_staff_lanes(placed: list[PlacedBooking]) -> list:
    """One fixed lane per specialist (sorted by name) for the whole week.

    In the all-staff view this reads like a resource calendar: each person keeps
    their column, so a day can be scanned per specialist.
    """
    staff = sorted({p.booking.staff for p in placed}, key=lambda s: (s.full_name, s.pk))
    lane_of = {member.pk: i for i, member in enumerate(staff)}
    for p in placed:
        p.lane, p.lanes = lane_of[p.booking.staff_id], len(staff)
    return staff


def _hour_range(salon: Salon, bookings: list[Booking], tz: ZoneInfo) -> tuple[int, int]:
    span = WorkingHours.objects.filter(
        staff__salon=salon, staff__is_active=True
    ).aggregate(first=Min("start_time"), last=Max("end_time"))
    first, last = DEFAULT_HOURS
    if span["first"]:
        first = span["first"].hour
        last = span["last"].hour + (1 if span["last"].minute else 0)
    for b in bookings:
        start, end = b.start_at.astimezone(tz), b.end_at.astimezone(tz)
        first = min(first, start.hour)
        last = max(
            last, 24 if end.date() > start.date() else end.hour + (end.minute > 0)
        )
    return first, max(last, first + 1)


def build_week(
    salon: Salon, week_start: date, *, staff_id: int | None = None
) -> WeekCalendar:
    """Lay out the active bookings of ``salon`` for the week starting ``week_start``."""
    tz = ZoneInfo(salon.timezone)
    start = datetime.combine(week_start, time.min, tzinfo=tz)
    end = start + timedelta(days=7)
    qs = (
        Booking.objects.filter(salon=salon, start_at__gte=start, start_at__lt=end)
        .exclude(status=Booking.Status.CANCELLED)
        .select_related("client", "staff", "service")
        .order_by("start_at")
    )
    if staff_id is not None:
        qs = qs.filter(staff_id=staff_id)
    bookings = list(qs)

    first_hour, last_hour = _hour_range(salon, bookings, tz)
    grid_minutes = (last_hour - first_hour) * 60
    days = [CalendarDay(week_start + timedelta(days=i)) for i in range(7)]
    for b in bookings:
        local_start = b.start_at.astimezone(tz)
        day = days[(local_start.date() - week_start).days]
        offset = (local_start.hour - first_hour) * 60 + local_start.minute
        minutes = (b.end_at - b.start_at).total_seconds() / 60
        minutes = min(minutes, grid_minutes - offset)  # clip past midnight
        day.bookings.append(
            PlacedBooking(
                b, top=100 * offset / grid_minutes, height=100 * minutes / grid_minutes
            )
        )
    lane_staff = []
    if staff_id is None:
        lane_staff = assign_staff_lanes([p for day in days for p in day.bookings])
    else:
        for day in days:
            assign_lanes(day.bookings)
    return WeekCalendar(week_start, first_hour, last_hour, days, lane_staff)
