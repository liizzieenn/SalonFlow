"""Slot engine: computes bookable start times for a staff member and a service.

Free time for a day is::

    working hours − breaks − time off − existing bookings (incl. their buffer)

A candidate start ``t`` is offered when the whole block
``[t, t + service.duration + service.buffer)`` lies inside free time, ``t`` sits on
the salon's time grid (every ``step`` minutes from midnight, salon-local) and ``t`` is
in the future. All intervals are half-open ``[start, end)`` so back-to-back
appointments are allowed.

The pure functions (:func:`subtract`, :func:`slot_starts`)
contain the algorithm and are unit-tested in isolation; :func:`available_slots`
loads the data for one staff member and one day.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, time, timedelta
from typing import NamedTuple
from zoneinfo import ZoneInfo

from django.utils import timezone

from apps.salons.models import Service, Staff

from .models import Booking

DEFAULT_STEP_MINUTES = 15


class Interval(NamedTuple):
    """Half-open time interval ``[start, end)``."""

    start: datetime
    end: datetime

    def overlaps(self, other: Interval) -> bool:
        return self.start < other.end and other.start < self.end


def subtract(intervals: Iterable[Interval], busy: Iterable[Interval]) -> list[Interval]:
    """Return ``intervals`` with every ``busy`` interval cut out.

    Both inputs may be unsorted and overlapping; the result is sorted and contains
    no empty intervals.
    """
    result = sorted(i for i in intervals if i.end > i.start)
    for b in sorted(busy):
        next_result: list[Interval] = []
        for i in result:
            if not i.overlaps(b):
                next_result.append(i)
                continue
            if i.start < b.start:
                next_result.append(Interval(i.start, b.start))
            if b.end < i.end:
                next_result.append(Interval(b.end, i.end))
        result = next_result
    return result


def _ceil_to_grid(moment: datetime, step: timedelta) -> datetime:
    """Round ``moment`` up to the next multiple of ``step`` since local midnight."""
    midnight = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed = moment - midnight
    remainder = elapsed % step
    return moment if not remainder else moment + (step - remainder)


def slot_starts(
    free: Iterable[Interval],
    block: timedelta,
    step: timedelta,
    not_before: datetime | None = None,
) -> list[datetime]:
    """Grid-aligned start times where a ``block`` fits entirely in a free interval."""
    starts: list[datetime] = []
    for interval in free:
        lower = (
            interval.start if not_before is None else max(interval.start, not_before)
        )
        t = _ceil_to_grid(lower, step)
        while t + block <= interval.end:
            starts.append(t)
            t += step
    return starts


def _at(day: date, t: time, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, t, tzinfo=tz)


def available_slots(
    staff: Staff,
    service: Service,
    day: date,
    *,
    step_minutes: int = DEFAULT_STEP_MINUTES,
    now: datetime | None = None,
) -> list[datetime]:
    """Bookable start times (aware, in the salon's time zone) for ``staff`` on ``day``.

    Returns an empty list when the staff member or service is inactive, belongs to
    another salon, or the staff member does not offer the service.
    """
    if (
        not staff.is_active
        or not service.is_active
        or staff.salon_id != service.salon_id
        or not staff.services.filter(pk=service.pk).exists()
    ):
        return []

    tz = ZoneInfo(staff.salon.timezone)
    weekday = day.weekday()
    day_start = _at(day, time.min, tz)
    day_end = day_start + timedelta(days=1)

    working = [
        Interval(_at(day, wh.start_time, tz), _at(day, wh.end_time, tz))
        for wh in staff.working_hours.filter(weekday=weekday)
    ]
    if not working:
        return []

    busy = [
        Interval(_at(day, br.start_time, tz), _at(day, br.end_time, tz))
        for br in staff.breaks.filter(weekday=weekday)
    ]
    busy += [
        Interval(off.start_at, off.end_at)
        for off in staff.time_off.filter(start_at__lt=day_end, end_at__gt=day_start)
    ]

    # Look back far enough that a previous-day booking's buffer is still seen.
    bookings = (
        Booking.objects.filter(
            staff=staff,
            start_at__lt=day_end,
            end_at__gt=day_start - timedelta(hours=12),
        )
        .exclude(status__in=Booking.INACTIVE_STATUSES)
        .select_related("service")
    )
    busy += [
        Interval(b.start_at, b.end_at + timedelta(minutes=b.service.buffer_minutes))
        for b in bookings
    ]

    block = timedelta(minutes=service.duration_minutes + service.buffer_minutes)
    now = now or timezone.now()
    return slot_starts(
        subtract(working, busy),
        block,
        timedelta(minutes=step_minutes),
        not_before=now.astimezone(tz),
    )


def available_slots_any_staff(
    service: Service,
    day: date,
    *,
    step_minutes: int = DEFAULT_STEP_MINUTES,
    now: datetime | None = None,
) -> dict[datetime, list[Staff]]:
    """Map each start time to the staff members free at that time (for "any staff")."""
    by_start: dict[datetime, list[Staff]] = {}
    staff_qs = (
        service.staff.filter(is_active=True).select_related("salon").order_by("pk")
    )
    for member in staff_qs:
        for start in available_slots(
            member, service, day, step_minutes=step_minutes, now=now
        ):
            by_start.setdefault(start, []).append(member)
    return dict(sorted(by_start.items()))


def is_slot_available(
    staff: Staff,
    service: Service,
    start_at: datetime,
    *,
    now: datetime | None = None,
) -> bool:
    """Whether ``start_at`` is an offered slot for ``staff`` and ``service``."""
    tz = ZoneInfo(staff.salon.timezone)
    local_start = start_at.astimezone(tz)
    return local_start in available_slots(staff, service, local_start.date(), now=now)
