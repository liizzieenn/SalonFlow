from datetime import time

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from apps.salons.models import Break, Weekday, WorkingHours
from tests.factories import (
    SalonFactory,
    ServiceFactory,
    StaffFactory,
    WorkingHoursFactory,
)

pytestmark = pytest.mark.django_db


def test_salon_str(salon):
    assert str(salon) == salon.name


def test_service_str_includes_duration():
    service = ServiceFactory(name="Haircut", duration_minutes=45)
    assert str(service) == "Haircut (45 min)"


def test_service_name_unique_per_salon():
    service = ServiceFactory(name="Haircut")
    ServiceFactory(name="Haircut")  # other salon: fine
    with pytest.raises(IntegrityError):
        ServiceFactory(name="Haircut", salon=service.salon)


def test_service_min_duration_enforced_by_db():
    with pytest.raises(IntegrityError):
        ServiceFactory(duration_minutes=2)


def test_staff_related_to_salon():
    salon = SalonFactory()
    staff = StaffFactory(salon=salon)
    assert list(salon.staff_members.all()) == [staff]


def test_working_hours_end_after_start_db_constraint():
    with pytest.raises(IntegrityError):
        WorkingHoursFactory(start_time=time(18, 0), end_time=time(9, 0))


def test_working_hours_clean_rejects_inverted_interval():
    staff = StaffFactory()
    wh = WorkingHours(
        staff=staff, weekday=Weekday.MONDAY, start_time=time(18), end_time=time(9)
    )
    with pytest.raises(ValidationError) as exc:
        wh.full_clean()
    assert "end_time" in exc.value.message_dict


def test_split_shifts_allowed_but_duplicate_start_rejected():
    wh = WorkingHoursFactory(start_time=time(9), end_time=time(13))
    WorkingHoursFactory(staff=wh.staff, start_time=time(14), end_time=time(19))
    assert wh.staff.working_hours.count() == 2
    with pytest.raises(IntegrityError):
        WorkingHoursFactory(staff=wh.staff, start_time=time(9), end_time=time(12))


def test_break_str():
    staff = StaffFactory()
    br = Break.objects.create(
        staff=staff, weekday=Weekday.FRIDAY, start_time=time(13), end_time=time(14)
    )
    assert str(br) == "Friday 13:00–14:00"
