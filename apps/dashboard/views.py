"""Owner / staff dashboard: week calendar, booking status updates, analytics."""

from __future__ import annotations

from datetime import date
from zoneinfo import ZoneInfo

from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.views.decorators.http import require_GET, require_POST

from apps.bookings.models import Booking
from apps.bookings.services import (
    InvalidStatusChangeError,
    allowed_status_changes,
    change_status,
)

from . import analytics as an
from .access import SalonAccess, accessible_salons, salon_view
from .calendar import build_week, week_start_for

RANGES = (7, 30, 90)
DEFAULT_RANGE = 30


def _is_htmx(request: HttpRequest) -> bool:
    return bool(request.headers.get("HX-Request")) and not request.headers.get(
        "HX-History-Restore-Request"
    )


def _today(access: SalonAccess) -> date:
    return timezone.now().astimezone(ZoneInfo(access.salon.timezone)).date()


def _render(request, access: SalonAccess, template: str, context: dict, **kw):
    context = {"access": access, "salon": access.salon, **context}
    with timezone.override(ZoneInfo(access.salon.timezone)):
        return render(request, template, context, **kw)


@login_required
@require_GET
def home(request: HttpRequest) -> HttpResponse:
    salons = list(accessible_salons(request.user))
    if len(salons) == 1:
        return redirect("dashboard:calendar", salon_slug=salons[0].slug)
    return render(request, "dashboard/home.html", {"salons": salons})


@salon_view()
@require_GET
def calendar(request: HttpRequest, access: SalonAccess) -> HttpResponse:
    try:
        week = week_start_for(date.fromisoformat(request.GET["week"]))
    except (KeyError, ValueError):
        week = week_start_for(_today(access))

    staff_members = []
    if access.is_owner:
        staff_members = list(access.salon.staff_members.filter(is_active=True))
        raw = request.GET.get("staff", "")
        staff_id = int(raw) if raw.isdigit() else None
        if staff_id is not None and staff_id not in {s.pk for s in staff_members}:
            staff_id = None
    else:
        staff_id = access.staff.pk if access.staff else -1  # staff see only their own

    context = {
        "week": build_week(access.salon, week, staff_id=staff_id),
        "today": _today(access),
        "staff_members": staff_members,
        "selected_staff": staff_id,
    }
    template = (
        "dashboard/partials/calendar_grid.html"
        if _is_htmx(request)
        else ("dashboard/calendar.html")
    )
    return _render(request, access, template, context)


def _booking_for(access: SalonAccess, pk: int) -> Booking:
    booking = get_object_or_404(
        Booking.objects.select_related("client", "staff", "service"),
        pk=pk,
        salon=access.salon,
    )
    if not access.can_manage_booking(booking):
        raise Http404
    return booking


def _detail(request, access, booking, *, error: str = "", **kw) -> HttpResponse:
    context = {
        "booking": booking,
        "transitions": [
            (status, Booking.Status(status).label)
            for status in allowed_status_changes(booking)
        ],
        "error": error,
    }
    template = (
        "dashboard/partials/booking_detail.html"
        if _is_htmx(request)
        else ("dashboard/booking_detail.html")
    )
    return _render(request, access, template, context, **kw)


@salon_view()
@require_GET
def booking_detail(request: HttpRequest, access: SalonAccess, pk: int) -> HttpResponse:
    return _detail(request, access, _booking_for(access, pk))


@salon_view()
@require_POST
def booking_status(request: HttpRequest, access: SalonAccess, pk: int) -> HttpResponse:
    booking = _booking_for(access, pk)
    try:
        change_status(booking, request.POST.get("status", ""))
    except InvalidStatusChangeError as exc:
        return _detail(request, access, booking, error=str(exc), status=400)
    if not _is_htmx(request):
        url = reverse("dashboard:calendar", args=[access.salon.slug])
        week = week_start_for(
            booking.start_at.astimezone(ZoneInfo(access.salon.timezone)).date()
        )
        return redirect(f"{url}?week={week.isoformat()}")
    response = _detail(request, access, booking)
    response["HX-Trigger"] = "bookingChanged"  # calendar grid refreshes itself
    return response


def _pct(value: float | None) -> float | None:
    return None if value is None else round(100 * value, 1)


@salon_view(owner_only=True)
@require_GET
def analytics(request: HttpRequest, access: SalonAccess) -> HttpResponse:
    raw = request.GET.get("days", "")
    days = int(raw) if raw.isdigit() and int(raw) in RANGES else DEFAULT_RANGE
    salon, now = access.salon, timezone.now()
    period = an.Period.last_days(salon, days, _today(access))
    current = an.summary(salon, period, now)
    previous = an.summary(salon, period.previous, now)
    by_day = an.revenue_by_day(salon, period, now)
    by_hour = an.bookings_by_hour(salon, period)
    services = an.top_services(salon, period, now)

    context = {
        "days": days,
        "ranges": RANGES,
        "period": period,
        "summary": current,
        "revenue_change": _pct(an.change(current.revenue, previous.revenue)),
        "booked_change": _pct(an.change(current.booked, previous.booked)),
        "no_show_pct": _pct(current.no_show_rate),
        "cancellation_pct": _pct(current.cancellation_rate),
        "by_day": by_day,
        "by_hour": by_hour,
        "services": services,
        "chart_data": {
            "revenue": {
                # Localised on the server (Django's locale data), not with JS Intl,
                # whose Georgian data is missing from some browser builds.
                "labels": [date_format(d, "j M") for d, _ in by_day],
                "values": [float(v) for _, v in by_day],
            },
            "hours": {
                "labels": [f"{h:02d}:00" for h, _ in by_hour],
                "values": [n for _, n in by_hour],
            },
            "services": {
                "labels": [s.name for s in services],
                "values": [s.bookings for s in services],
            },
        },
    }
    return _render(request, access, "dashboard/analytics.html", context)
