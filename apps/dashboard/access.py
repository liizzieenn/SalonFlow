"""Who may see which salon's dashboard.

* The salon **owner** sees everything: all staff calendars and the analytics.
* An active **staff member** with a linked user account sees only their own
  bookings in the calendar and can update their status; analytics (revenue) are
  owner-only.
* Anyone else gets a 404 — we don't reveal that a salon's dashboard exists.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q, QuerySet
from django.http import Http404, HttpRequest, HttpResponse

from apps.salons.models import Salon, Staff


@dataclass(frozen=True)
class SalonAccess:
    salon: Salon
    is_owner: bool
    staff: Staff | None  # the user's own staff profile in this salon, if any

    def can_manage_booking(self, booking) -> bool:
        return booking.salon_id == self.salon.pk and (
            self.is_owner
            or (self.staff is not None and booking.staff_id == self.staff.pk)
        )


def accessible_salons(user) -> QuerySet[Salon]:
    """Active salons the user owns or works at."""
    if not user.is_authenticated:
        return Salon.objects.none()
    return (
        Salon.objects.filter(is_active=True)
        .filter(
            Q(owner=user) | Q(staff_members__user=user, staff_members__is_active=True)
        )
        .distinct()
        .order_by("name")
    )


def get_access(user, salon_slug: str) -> SalonAccess:
    salon = accessible_salons(user).filter(slug=salon_slug).first()
    if salon is None:
        raise Http404
    is_owner = salon.owner_id == user.pk
    staff = Staff.objects.filter(salon=salon, user=user, is_active=True).first()
    return SalonAccess(salon=salon, is_owner=is_owner, staff=staff)


def salon_view(*, owner_only: bool = False) -> Callable:
    """Decorator: require login and salon access; passes ``access`` to the view."""

    def decorator(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
        @login_required
        @wraps(view)
        def wrapper(request: HttpRequest, salon_slug: str, *args, **kwargs):
            access = get_access(request.user, salon_slug)
            if owner_only and not access.is_owner:
                raise PermissionDenied
            return view(request, access, *args, **kwargs)

        return wrapper

    return decorator
