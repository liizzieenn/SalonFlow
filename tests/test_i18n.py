"""Georgian/English internationalisation.

The catalogue tests make CI fail when a string is untranslated, fuzzy, has
mismatched placeholders, when the compiled .mo is stale, or when code contains a
translatable string that hasn't been extracted into the .po yet.
"""

import gettext
import os
import re
import shutil
from datetime import datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import polib
import pytest
from django.conf import settings
from django.core import mail
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone, translation

from apps.bookings.models import Booking
from apps.clients.models import Client
from apps.notifications.messages import render
from apps.notifications.models import Notification
from apps.notifications.sms.backends import locmem
from apps.notifications.tasks import deliver
from tests.factories import (
    BookingFactory,
    ClientFactory,
    SalonFactory,
    ServiceFactory,
    StaffFactory,
    WorkingHoursFactory,
)

LOCALE_DIR = Path(settings.BASE_DIR) / "locale" / "ka" / "LC_MESSAGES"
PO_PATH = LOCALE_DIR / "django.po"
MO_PATH = LOCALE_DIR / "django.mo"
PLACEHOLDER_RE = re.compile(r"%(?:\([a-z_]+\))?[sd]")
TZ = ZoneInfo("Asia/Tbilisi")


@pytest.fixture(scope="module")
def po():
    return polib.pofile(str(PO_PATH))


def test_default_language_is_georgian():
    assert settings.LANGUAGE_CODE == "ka"
    assert [code for code, _ in settings.LANGUAGES] == ["ka", "en"]


# --- catalogue quality (CI gate) ---------------------------------------------------


def test_every_string_is_translated(po):
    untranslated = [e.msgid for e in po.untranslated_entries()]
    untranslated += [
        e.msgid for e in po if e.msgid_plural and not all(e.msgstr_plural.values())
    ]
    assert untranslated == [], f"Untranslated strings in {PO_PATH}: {untranslated}"


def test_no_fuzzy_or_obsolete_entries(po):
    assert [e.msgid for e in po.fuzzy_entries()] == []
    assert [e.msgid for e in po.obsolete_entries()] == []
    assert not po.metadata_is_fuzzy


def test_plural_forms_match_django_georgian(po):
    assert po.metadata["Plural-Forms"] == "nplurals=2; plural=(n!=1);"
    for entry in po:
        if entry.msgid_plural:
            assert sorted(entry.msgstr_plural) == [0, 1], entry.msgid


def test_placeholders_preserved(po):
    """A dropped or renamed %(name)s would crash at runtime in Georgian only."""
    problems = []
    for entry in po.translated_entries():
        expected = sorted(PLACEHOLDER_RE.findall(entry.msgid))
        targets = entry.msgstr_plural.values() if entry.msgid_plural else [entry.msgstr]
        for target in targets:
            if sorted(PLACEHOLDER_RE.findall(target)) != expected:
                problems.append((entry.msgid, target))
    assert problems == []


def test_compiled_mo_matches_po(po):
    with MO_PATH.open("rb") as fh:
        mo = gettext.GNUTranslations(fh)
    stale = []
    for entry in po.translated_entries():
        if entry.msgid_plural:
            got = [mo.ngettext(entry.msgid, entry.msgid_plural, n) for n in (1, 2)]
            want = [entry.msgstr_plural[0], entry.msgstr_plural[1]]
        else:
            got, want = mo.gettext(entry.msgid), entry.msgstr
        if got != want:
            stale.append(entry.msgid)
    assert stale == [], "django.mo is stale: run `python manage.py compilemessages`"


def test_po_contains_every_translatable_string_in_code(po, tmp_path):
    """Re-extract strings from the code; anything missing from the .po fails."""
    if not shutil.which("xgettext"):
        if os.environ.get("CI"):
            pytest.fail("xgettext (GNU gettext) is required in CI")
        pytest.skip("GNU gettext not installed")
    backup = tmp_path / "django.po"
    shutil.copy(PO_PATH, backup)
    try:
        call_command(
            "makemessages", locale=["ka"], extensions=["html", "txt", "py"],
            ignore_patterns=[".venv", "staticfiles", "tests"], verbosity=0,
            no_obsolete=True,
        )  # fmt: skip
        extracted = {(e.msgctxt, e.msgid) for e in polib.pofile(str(PO_PATH))}
    finally:
        shutil.copy(backup, PO_PATH)
    current = {(e.msgctxt, e.msgid) for e in po}
    assert (
        sorted(m for _, m in extracted - current) == []
    ), "New strings in code: run makemessages and translate them"
    assert sorted(m for _, m in current - extracted) == [], "Obsolete strings in .po"


# --- URLs, switcher and rendering --------------------------------------------------


@pytest.fixture
def salon(db):
    salon = SalonFactory(slug="kb", name="Kutaisi Beauty")
    service = ServiceFactory(
        salon=salon, name="Haircut", price=Decimal("35"), duration_minutes=60
    )
    staff = StaffFactory(salon=salon, services=[service])
    day = timezone.now().astimezone(TZ).date() + timedelta(days=3)
    WorkingHoursFactory(
        staff=staff, weekday=day.weekday(), start_time=time(10), end_time=time(12)
    )
    salon.test_day = day
    return salon


def test_georgian_is_served_without_prefix(client, salon):
    with translation.override("ka"):
        url = reverse("bookings:services", args=["kb"])
    assert url == "/s/kb/book/"
    html = client.get(url).content.decode()
    assert '<html lang="ka">' in html
    assert "აირჩიეთ მომსახურება" in html
    assert "35.00 ₾" in html  # Django's Georgian locale uses "." for decimals


def test_english_under_prefix(client, salon):
    with translation.override("en"):
        url = reverse("bookings:services", args=["kb"])
    assert url == "/en/s/kb/book/"
    html = client.get(url).content.decode()
    assert '<html lang="en">' in html and "Choose a service" in html
    assert "35.00 ₾" in html


def test_unprefixed_url_ignores_browser_language(client, salon):
    html = client.get("/s/kb/book/", HTTP_ACCEPT_LANGUAGE="en").content.decode()
    assert '<html lang="ka">' in html


@pytest.mark.django_db
def test_admin_and_health_are_not_prefixed(client):
    assert client.get("/health/").status_code == 200
    assert client.get("/en/health/").status_code == 404


@pytest.mark.parametrize(
    ("language", "next_url", "expected"),
    [
        ("en", "/s/kb/book/", "/en/s/kb/book/"),
        ("ka", "/en/s/kb/book/staff/?service=1", "/s/kb/book/staff/?service=1"),
        ("en", "/dashboard/", "/en/dashboard/"),
    ],
)
def test_language_switcher_redirects_to_translated_url(
    client, salon, language, next_url, expected
):
    response = client.post(
        reverse("set_language"), {"language": language, "next": next_url}
    )
    assert response.status_code == 302
    assert response.url == expected
    assert response.cookies[settings.LANGUAGE_COOKIE_NAME].value == language


@pytest.mark.parametrize("cookie", ["ka", "en", None])
def test_switch_to_georgian_from_english_page_regardless_of_cookie(
    client, salon, settings, cookie
):
    if cookie:
        client.cookies[settings.LANGUAGE_COOKIE_NAME] = cookie
    response = client.post(
        reverse("set_language"),
        {"language": "ka", "next": "/en/s/kb/book/"},
        HTTP_ACCEPT_LANGUAGE="ka",
    )
    assert response.url == "/s/kb/book/"


def test_switcher_rendered_with_current_language(client, salon):
    html = client.get("/s/kb/book/").content.decode()
    assert 'action="/i18n/setlang/"' in html
    assert re.search(r'value="ka"[^>]*\n?[^>]*aria-current="true"', html)
    # No capfirst: str.upper() would turn Georgian into Mtavruli ("Ქართული").
    assert ">ქართული<" in html and ">English<" in html


@pytest.mark.django_db
def test_dashboard_login_in_georgian(client):
    html = client.get("/accounts/login/").content.decode()
    assert "მართვის პანელში შესვლა" in html


def test_slots_page_georgian_dates(client, salon):
    service = salon.services.get()
    day = salon.test_day
    url = f"/s/kb/book/slots/?service={service.pk}&staff=any&date={day.isoformat()}"
    html = client.get(url).content.decode()
    with translation.override("ka"):
        weekday = ["ორშაბათი", "სამშაბათი", "ოთხშაბათი", "ხუთშაბათი", "პარასკევი",
                   "შაბათი", "კვირა"][day.weekday()]  # fmt: skip
    assert weekday in html and "10:00" in html


# --- client language drives notifications ------------------------------------------


def confirm(client, salon, prefix: str):
    service = salon.services.get()
    start = datetime.combine(salon.test_day, time(10), tzinfo=TZ)
    data = {
        "service": service.pk, "staff": "", "start_at": start.isoformat(),
        "full_name": "Nino", "phone": "+995555123456", "email": "", "notes": "",
    }  # fmt: skip
    return client.post(f"{prefix}/s/kb/book/confirm/", data, HTTP_HX_REQUEST="true")


@pytest.mark.parametrize(("prefix", "language"), [("", "ka"), ("/en", "en")])
def test_booking_stores_client_language(client, salon, prefix, language):
    response = confirm(client, salon, prefix)
    assert response.status_code == 200
    assert Client.objects.get(phone="+995555123456").preferred_language == language


def test_rebooking_in_other_language_updates_preference(client, salon):
    ClientFactory(salon=salon, phone="+995555123456", preferred_language="ka")
    confirm(client, salon, "/en")
    assert Client.objects.get(phone="+995555123456").preferred_language == "en"


@pytest.fixture
def ka_booking(db):
    salon = SalonFactory(name="Kutaisi Beauty", phone="+995431000000")
    service = ServiceFactory(salon=salon, name="Haircut", price=Decimal("40"))
    client = ClientFactory(
        salon=salon, preferred_language="ka", email="nino@example.com"
    )
    booking = BookingFactory(
        salon=salon,
        service=service,
        client=client,
        start_at=datetime(2030, 3, 4, 15, 30, tzinfo=TZ),
        status=Booking.Status.CONFIRMED,
    )
    booking.refresh_from_db()  # price as stored (Decimal 40.00)
    return booking


def test_notification_texts_in_georgian(ka_booking):
    msg = render(ka_booking, Notification.Kind.CONFIRMATION)
    assert msg.subject == "თქვენი ჯავშანი სალონში „Kutaisi Beauty“ დადასტურებულია"
    assert msg.sms == (
        "Kutaisi Beauty: ჯავშანი დადასტურებულია — Haircut, 04.03, 15:30. "
        "კითხვები: +995431000000"
    )
    assert "თქვენი ჯავშანი დადასტურებულია." in msg.text
    assert "ორშაბათი" in msg.text  # weekday name in Georgian
    assert "40.00 ₾" in msg.html


def test_reminder_sent_in_client_language_whatever_is_active(ka_booking):
    locmem.outbox.clear()
    with translation.override("en"):  # e.g. a Celery worker's default
        deliver(ka_booking.pk, Notification.Kind.REMINDER_2H,
                now=ka_booking.start_at - timedelta(hours=1))  # fmt: skip
    assert mail.outbox[0].subject.startswith("შეხსენება:")
    assert locmem.outbox[0].body.startswith("Kutaisi Beauty: მალე გნახავთ")
    assert translation.get_language() == "en"  # override didn't leak


def test_english_client_gets_english(ka_booking):
    ka_booking.client.preferred_language = "en"
    msg = render(ka_booking, Notification.Kind.REMINDER_24H)
    assert msg.subject == "Reminder: your appointment at Kutaisi Beauty"


# --- assistant messages ------------------------------------------------------------


def test_assistant_errors_follow_question_language(client, salon, settings):
    from django.core.cache import cache

    cache.clear()
    settings.ASSISTANT_RATE_LIMITS = {"client": {"minute": 0}}
    url = "/s/kb/assistant/"
    ka = client.post(url, {"message": "გამარჯობა"}, HTTP_HX_REQUEST="true")
    en = client.post(url, {"message": "hello"}, HTTP_HX_REQUEST="true")
    assert ka.status_code == en.status_code == 429
    assert "შეტყობინებებს ძალიან ხშირად აგზავნით" in ka.content.decode()
    assert "too quickly" in en.content.decode()


def test_assistant_widget_in_georgian(client, salon):
    html = client.get("/s/kb/book/").content.decode()
    assert "ჰკითხეთ ჩვენს ასისტენტს" in html
    assert 'hx-post="/s/kb/assistant/"' in html


def test_analytics_chart_labels_localised_on_server(db):
    from tests.factories import UserFactory

    owner = UserFactory()
    SalonFactory(owner=owner, slug="kb2")
    from django.test import Client as HttpClient

    http = HttpClient()
    http.force_login(owner)
    ka = http.get("/dashboard/kb2/analytics/?days=7").context["chart_data"]
    en = http.get("/en/dashboard/kb2/analytics/?days=7").context["chart_data"]
    assert re.search(r"[\u10A0-\u10FF]", ka["revenue"]["labels"][0])  # Georgian month
    assert re.search(r"[A-Z][a-z]{2}$", en["revenue"]["labels"][0])
