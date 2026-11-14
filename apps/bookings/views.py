"""Public booking flow: service → staff → date → time → details → confirmation.

Every step is a small view returning an HTML fragment for HTMX. When requested
without HTMX (no JS, direct link, refresh) the same fragment is rendered inside the
full booking page, so the flow degrades gracefully and step URLs are shareable.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from django import forms
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone, translation
from django.views.decorators.http import require_POST, require_safe

from apps.salons.models import Salon, Service, Staff

from .forms import BookingForm
from .services import BookingError, ClientDetails, SlotUnavailableError, create_booking
from .slots import available_slots, available_slots_any_staff

BOOKING_HORIZON_DAYS = 30
DATES_SHOWN = 14
ANY_STAFF = "any"


def _render_step(
    request: HttpRequest, salon: Salon, template: str, context: dict[str, Any]
) -> HttpResponse:
    """Render a step fragment for HTMX, or the full page around it otherwise."""
    context = {"salon": salon, "any_staff": ANY_STAFF, **context}
    with timezone.override(ZoneInfo(salon.timezone)):
        # History-restore requests (back button on a cache miss) need the full page.
        if request.headers.get("HX-Request") and not request.headers.get(
            "HX-History-Restore-Request"
        ):
            return render(request, template, context)
        return render(
            request, "bookings/book.html", {**context, "step_template": template}
        )


def _salon(slug: str) -> Salon:
    return get_object_or_404(Salon, slug=slug, is_active=True)


def _service(salon: Salon, value: str | None) -> Service:
    if not value or not value.isdigit():
        raise Http404
    return get_object_or_404(Service, pk=value, salon=salon, is_active=True)


def _staff(salon: Salon, service: Service, value: str | None) -> Staff | None:
    """Selected staff member, or ``None`` for "any staff"."""
    if value == ANY_STAFF:
        return None
    if not value or not value.isdigit():
        raise Http404
    return get_object_or_404(
        Staff, pk=value, salon=salon, is_active=True, services=service
    )


def _local_today(salon: Salon) -> date:
    return timezone.now().astimezone(ZoneInfo(salon.timezone)).date()


def _day(salon: Salon, value: str | None) -> date:
    try:
        day = date.fromisoformat(value or "")
    except ValueError as exc:
        raise Http404 from exc
    today = _local_today(salon)
    if not today <= day <= today + timedelta(days=BOOKING_HORIZON_DAYS):
        raise Http404
    return day


def _slots(service: Service, staff: Staff | None, day: date) -> list[datetime]:
    if staff is None:
        return list(available_slots_any_staff(service, day))
    return available_slots(staff, service, day)


@require_safe
def services_step(request: HttpRequest, salon_slug: str) -> HttpResponse:
    salon = _salon(salon_slug)
    services = salon.services.filter(is_active=True, staff__is_active=True).distinct()
    return _render_step(
        request, salon, "bookings/partials/services.html", {"services": services}
    )


@require_safe
def staff_step(request: HttpRequest, salon_slug: str) -> HttpResponse:
    salon = _salon(salon_slug)
    service = _service(salon, request.GET.get("service"))
    staff = service.staff.filter(is_active=True).order_by("full_name")
    return _render_step(
        request,
        salon,
        "bookings/partials/staff.html",
        {"service": service, "staff_members": staff},
    )


@require_safe
def dates_step(request: HttpRequest, salon_slug: str) -> HttpResponse:
    salon = _salon(salon_slug)
    service = _service(salon, request.GET.get("service"))
    staff_value = request.GET.get("staff")
    staff = _staff(salon, service, staff_value)
    today = _local_today(salon)
    return _render_step(
        request,
        salon,
        "bookings/partials/dates.html",
        {
            "service": service,
            "staff": staff,
            "staff_value": staff_value,
            "dates": [today + timedelta(days=i) for i in range(DATES_SHOWN)],
        },
    )


@require_safe
def slots_step(request: HttpRequest, salon_slug: str) -> HttpResponse:
    salon = _salon(salon_slug)
    service = _service(salon, request.GET.get("service"))
    staff_value = request.GET.get("staff")
    staff = _staff(salon, service, staff_value)
    day = _day(salon, request.GET.get("date"))
    return _render_step(
        request,
        salon,
        "bookings/partials/slots.html",
        {
            "service": service,
            "staff": staff,
            "staff_value": staff_value,
            "day": day,
            "slots": _slots(service, staff, day),
        },
    )


@require_safe
def details_step(request: HttpRequest, salon_slug: str) -> HttpResponse:
    salon = _salon(salon_slug)
    service = _service(salon, request.GET.get("service"))
    staff_value = request.GET.get("staff")
    staff = _staff(salon, service, staff_value)
    try:
        start_at = datetime.fromisoformat(request.GET.get("start", ""))
    except ValueError as exc:
        raise Http404 from exc
    if timezone.is_naive(start_at):
        raise Http404
    form = BookingForm(
        salon=salon,
        initial={"service": service, "staff": staff, "start_at": start_at.isoformat()},
    )
    return _render_step(
        request,
        salon,
        "bookings/partials/details.html",
        {
            "service": service,
            "staff": staff,
            "staff_value": staff_value,
            "start_at": start_at,
            "form": form,
        },
    )


@require_POST
def confirm(request: HttpRequest, salon_slug: str) -> HttpResponse:
    """Validate the details form and create the booking."""
    salon = _salon(salon_slug)
    form = BookingForm(request.POST, salon=salon)
    if form.is_valid():
        data = form.cleaned_data
        try:
            booking = create_booking(
                service=data["service"],
                staff=data["staff"],
                start_at=data["start_at"],
                client_details=ClientDetails(
                    full_name=data["full_name"],
                    phone=data["phone"],
                    email=data["email"],
                    language=translation.get_language(),
                ),
                notes=data["notes"],
                user=request.user if request.user.is_authenticated else None,
            )
        except SlotUnavailableError as exc:
            form.add_error(
                None, forms.ValidationError(str(exc), code="slot_unavailable")
            )
        except BookingError as exc:
            form.add_error(None, str(exc))
        else:
            return _render_step(
                request, salon, "bookings/partials/success.html", {"booking": booking}
            )

    # Invalid input or slot taken: re-render the form with errors. Hidden fields are
    # tampered or missing only if service/start are absent — nothing to show then.
    service = form.cleaned_data.get("service")
    staff = form.cleaned_data.get("staff")
    start_at = form.cleaned_data.get("start_at")
    if service is None or start_at is None:
        raise Http404
    return _render_step(
        request,
        salon,
        "bookings/partials/details.html",
        {
            "service": service,
            "staff": staff,
            "staff_value": staff.pk if staff else ANY_STAFF,
            "start_at": start_at,
            "form": form,
            "slot_taken": any(
                e.code == "slot_unavailable" for e in form.non_field_errors().as_data()
            ),
        },
    )
