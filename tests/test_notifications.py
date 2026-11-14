from datetime import datetime, time, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

import pytest
from django.core import mail
from django.utils import timezone

from apps.bookings.models import Booking
from apps.bookings.services import ClientDetails, create_booking
from apps.notifications import tasks
from apps.notifications.messages import render
from apps.notifications.models import Notification
from apps.notifications.sms import SMSSendError
from apps.notifications.sms.backends import locmem
from apps.notifications.tasks import (
    deliver,
    dispatch_due_reminders,
    due_reminders,
    reminder_windows,
    send_booking_notification,
)
from tests.factories import (
    BookingFactory,
    ClientFactory,
    SalonFactory,
    ServiceFactory,
    StaffFactory,
    WorkingHoursFactory,
)

pytestmark = pytest.mark.django_db
TZ = ZoneInfo("Asia/Tbilisi")
Kind = Notification.Kind


@pytest.fixture(autouse=True)
def clear_outbox():
    locmem.outbox.clear()


def make_booking(start: datetime, *, created: datetime | None = None, **client_kw):
    salon = SalonFactory(
        name="Kutaisi Beauty", phone="+995431000000", address="Rustaveli 12"
    )
    service = ServiceFactory(salon=salon, name="Haircut", duration_minutes=45)
    client_kw.setdefault("email", "nino@example.com")
    client_kw.setdefault("phone", "+995555123456")
    client = ClientFactory(salon=salon, full_name="Nino", **client_kw)
    booking = BookingFactory(
        salon=salon,
        service=service,
        client=client,
        staff=StaffFactory(salon=salon, full_name="Ana"),
        start_at=start,
        status=Booking.Status.CONFIRMED,
    )
    if created is not None:
        Booking.objects.filter(pk=booking.pk).update(created_at=created)
        booking.refresh_from_db()
    return booking


@pytest.fixture
def booking():
    start = datetime(2030, 3, 4, 15, 30, tzinfo=TZ)
    return make_booking(start, created=start - timedelta(days=7))


# --- rendering ---------------------------------------------------------------------


def test_confirmation_texts(booking):
    msg = render(booking, Kind.CONFIRMATION)
    assert msg.subject == "Your booking at Kutaisi Beauty is confirmed"
    for text in (msg.text, msg.html):
        assert "Haircut" in text and "Ana" in text and "15:30" in text
        assert "Rustaveli 12" in text and "+995431000000" in text
    assert msg.sms == (
        "Kutaisi Beauty: booking confirmed — Haircut, 04.03 at 15:30. "
        "Questions: +995431000000"
    )


def test_time_rendered_in_salon_timezone(booking, settings):
    settings.TIME_ZONE = "UTC"
    assert "15:30" in render(booking, Kind.REMINDER_2H).sms


def test_reminder_texts_differ(booking):
    day_before = render(booking, Kind.REMINDER_24H)
    soon = render(booking, Kind.REMINDER_2H)
    assert day_before.subject.startswith("Reminder")
    assert "reminder — Haircut on 04.03 at 15:30" in day_before.sms
    assert "see you soon — Haircut at 15:30" in soon.sms
    assert "upcoming appointment" in day_before.text
    assert "starts shortly" in soon.text


# --- delivery ----------------------------------------------------------------------

NOW = datetime(2030, 3, 1, 12, 0, tzinfo=TZ)


def test_deliver_sends_email_and_sms(booking):
    assert deliver(booking.pk, Kind.REMINDER_24H, now=NOW) == {
        "email": "sent",
        "sms": "sent",
    }
    assert len(mail.outbox) == 1
    email = mail.outbox[0]
    assert email.to == ["nino@example.com"]
    assert email.alternatives[0][1] == "text/html"
    assert [m.to for m in locmem.outbox] == ["+995555123456"]
    notes = Notification.objects.filter(booking=booking)
    assert {(n.channel, n.status, n.attempts) for n in notes} == {
        ("email", "sent", 1),
        ("sms", "sent", 1),
    }
    assert all(n.sent_at for n in notes)


def test_deliver_is_idempotent(booking):
    deliver(booking.pk, Kind.REMINDER_24H, now=NOW)
    deliver(booking.pk, Kind.REMINDER_24H, now=NOW)
    assert len(mail.outbox) == 1 and len(locmem.outbox) == 1


def test_only_available_channels():
    booking = make_booking(NOW + timedelta(days=1), email="")
    assert deliver(booking.pk, Kind.REMINDER_24H, now=NOW) == {"sms": "sent"}
    assert mail.outbox == []


def test_cancelled_booking_skipped(booking):
    booking.status = Booking.Status.CANCELLED
    booking.save()
    assert deliver(booking.pk, Kind.REMINDER_2H, now=NOW) == {}
    assert not mail.outbox and not locmem.outbox


def test_missing_booking_skipped():
    assert deliver(999999, Kind.CONFIRMATION) == {}


def test_reminder_after_start_skipped(booking):
    assert deliver(booking.pk, Kind.REMINDER_2H, now=booking.start_at) == {}


def test_sms_failure_still_sends_email_and_records_error(booking):
    with (
        mock.patch.object(tasks, "send_sms", side_effect=SMSSendError("provider down")),
        pytest.raises(SMSSendError),
    ):
        deliver(booking.pk, Kind.REMINDER_24H, now=NOW)
    assert len(mail.outbox) == 1
    sms = Notification.objects.get(booking=booking, channel="sms")
    assert (sms.status, sms.attempts, sms.error) == ("failed", 1, "provider down")

    # Retry: only the failed channel is re-sent.
    deliver(booking.pk, Kind.REMINDER_24H, now=NOW)
    assert len(mail.outbox) == 1 and len(locmem.outbox) == 1
    sms.refresh_from_db()
    assert (sms.status, sms.attempts, sms.error) == ("sent", 2, "")


def test_task_retries_transient_failure(booking):
    real_send = tasks.send_sms
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise SMSSendError("timeout")
        return real_send(*args, **kwargs)

    with mock.patch.object(tasks, "send_sms", side_effect=flaky):
        result = send_booking_notification.apply(
            args=(booking.pk, Kind.CONFIRMATION), throw=False
        )
    assert result.successful()
    sms = Notification.objects.get(booking=booking, channel="sms")
    assert (sms.status, sms.attempts) == ("sent", 2)
    assert len(mail.outbox) == 1


# --- confirmation on booking creation ----------------------------------------------


def test_create_booking_sends_confirmation_after_commit(
    django_capture_on_commit_callbacks,
):
    service = ServiceFactory(duration_minutes=60)
    staff = StaffFactory(salon=service.salon, services=[service])
    day = timezone.now().astimezone(TZ).date() + timedelta(days=2)
    WorkingHoursFactory(staff=staff, weekday=day.weekday(), start_time=time(10))
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        booking = create_booking(
            service=service,
            staff=staff,
            start_at=datetime.combine(day, time(10), tzinfo=TZ),
            client_details=ClientDetails(
                "Nino", "+995555123456", "nino@example.com", language="en"
            ),
        )
    assert not mail.outbox  # nothing before commit
    assert len(callbacks) == 1
    callbacks[0]()
    assert mail.outbox[0].subject.endswith("is confirmed")
    assert (
        Notification.objects.filter(booking=booking, kind=Kind.CONFIRMATION).count()
        == 2
    )


# --- scheduling --------------------------------------------------------------------


def test_reminder_windows(settings):
    assert reminder_windows() == [
        ("reminder_24h", timedelta(hours=24), timedelta(hours=2)),
        ("reminder_2h", timedelta(hours=2), timedelta(0)),
    ]


@pytest.mark.parametrize(
    ("hours_ahead", "expected"),
    [
        (30, []),
        (24, ["reminder_24h"]),
        (23, ["reminder_24h"]),
        (2.5, ["reminder_24h"]),
        (2, ["reminder_2h"]),
        (0.5, ["reminder_2h"]),
        (-0.5, []),
    ],
)
def test_due_reminders_windows(hours_ahead, expected):
    start = NOW + timedelta(hours=hours_ahead)
    booking = make_booking(start, created=start - timedelta(days=5))
    assert due_reminders(NOW) == [(booking.pk, kind) for kind in expected]


def test_no_reminder_for_booking_made_after_send_time():
    start = NOW + timedelta(hours=20)  # 24h reminder time already passed when booked
    make_booking(start, created=NOW - timedelta(hours=1))
    assert due_reminders(NOW) == []


def test_no_reminder_for_cancelled_or_already_notified(booking):
    now = booking.start_at - timedelta(hours=1)
    Notification.objects.create(
        booking=booking, kind=Kind.REMINDER_2H, channel="sms", recipient="x"
    )
    other = make_booking(booking.start_at, created=booking.created_at)
    other.status = Booking.Status.CANCELLED
    other.save()
    assert due_reminders(now) == []


def test_dispatch_sends_due_reminders_once(booking):
    with mock.patch(
        "django.utils.timezone.now", return_value=booking.start_at - timedelta(hours=1)
    ):
        assert dispatch_due_reminders() == 1
        assert dispatch_due_reminders() == 0
    assert len(mail.outbox) == 1 and len(locmem.outbox) == 1
    assert "see you soon — Haircut at 15:30" in locmem.outbox[0].body


def test_broker_outage_does_not_fail_booking(django_capture_on_commit_callbacks):
    service = ServiceFactory(duration_minutes=60)
    staff = StaffFactory(salon=service.salon, services=[service])
    day = timezone.now().astimezone(TZ).date() + timedelta(days=2)
    WorkingHoursFactory(staff=staff, weekday=day.weekday(), start_time=time(10))
    with (
        mock.patch.object(
            send_booking_notification, "delay", side_effect=ConnectionError("no redis")
        ),
        django_capture_on_commit_callbacks(execute=True),
    ):
        booking = create_booking(
            service=service,
            staff=staff,
            start_at=datetime.combine(day, time(10), tzinfo=TZ),
            client_details=ClientDetails("Nino", "+995555123456"),
        )
    assert Booking.objects.filter(pk=booking.pk).exists()
