import json
from datetime import datetime, time, timedelta
from decimal import Decimal
from unittest import mock
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import pytest
from django.urls import reverse

from apps.assistant import chat, knowledge
from apps.assistant.models import FAQ, KnowledgeChunk
from apps.assistant.providers import ChatResult, ContextItem, reset_providers
from apps.salons.models import Weekday
from tests.factories import (
    BookingFactory,
    SalonFactory,
    ServiceFactory,
    StaffFactory,
    WorkingHoursFactory,
)

pytestmark = pytest.mark.django_db
TZ = ZoneInfo("Asia/Tbilisi")
MONDAY = datetime(2030, 1, 7, 8, 0, tzinfo=TZ)  # "now" in slot tests


@pytest.fixture(autouse=True)
def _fresh_providers():
    reset_providers()
    yield
    reset_providers()


@pytest.fixture
def salon():
    salon = SalonFactory(
        name="Kutaisi Beauty", slug="kb", phone="+995431000000", address="Rustaveli 12"
    )
    manicure = ServiceFactory(
        salon=salon,
        name="Manicure",
        price=Decimal("35"),
        duration_minutes=60,
        buffer_minutes=0,
        description="Classic manicure with gel polish.",
    )
    ServiceFactory(
        salon=salon, name="Haircut", price=Decimal("40"), duration_minutes=45
    )
    ana = StaffFactory(salon=salon, full_name="Ana", services=[manicure])
    WorkingHoursFactory(
        staff=ana, weekday=Weekday.MONDAY, start_time=time(10), end_time=time(13)
    )
    FAQ.objects.create(
        salon=salon,
        question="Can I pay by card?",
        answer="Yes, we accept Visa and Mastercard.",
    )
    knowledge.reindex_salon(salon)
    return salon


# --- knowledge index ---------------------------------------------------------------


def test_reindex_creates_one_chunk_per_source(salon):
    chunks = KnowledgeChunk.objects.filter(salon=salon)
    assert sorted(c.source for c in chunks) == ["faq", "salon", "service", "service"]
    manicure = chunks.get(title="Manicure")
    assert "Price: 35.00 GEL" in manicure.content
    assert "Specialists: Ana" in manicure.content
    assert manicure.embedding_model == "fake-hash-v1"
    info = chunks.get(source="salon")
    assert "Rustaveli 12" in info.content and "Monday: 10:00–13:00" in info.content
    assert "Sunday: closed" in info.content


def test_index_is_idempotent_and_tracks_changes(salon):
    faq = FAQ.objects.get(salon=salon)
    assert knowledge.index_source("faq", faq.pk) == "unchanged"
    faq.answer = "Card and cash."
    faq.save()
    assert knowledge.index_source("faq", faq.pk) == "updated"
    assert (
        "Card and cash."
        in KnowledgeChunk.objects.get(source="faq", source_id=faq.pk).content
    )
    faq.is_active = False
    faq.save()
    assert knowledge.index_source("faq", faq.pk) == "deleted"
    faq.delete()
    assert knowledge.index_source("faq", faq.pk) == "unchanged"


def test_inactive_salon_not_indexed(salon):
    salon.is_active = False
    salon.save()
    knowledge.reindex_salon(salon)
    assert not KnowledgeChunk.objects.filter(salon=salon).exists()


def test_reindex_removes_orphans(salon):
    service = salon.services.get(name="Haircut")
    service.delete()  # signal would queue a task; here we reindex by hand
    counts = knowledge.reindex_salon(salon)
    assert counts.get("deleted") == 1
    assert not KnowledgeChunk.objects.filter(title="Haircut").exists()


def test_search_is_scoped_to_salon(salon):
    other = SalonFactory(name="Other Salon")
    ServiceFactory(salon=other, name="Manicure deluxe", price=Decimal("99"))
    knowledge.reindex_salon(other)
    results = knowledge.search(salon, "manicure price", k=5, max_distance=2)
    assert results
    assert all(
        r.title != "Manicure deluxe" and "Other Salon" not in r.content for r in results
    )
    assert results[0].title == "Manicure"
    assert [r.number for r in results] == list(range(1, len(results) + 1))


def test_search_respects_distance_threshold(salon):
    assert knowledge.search(salon, "manicure", k=5, max_distance=0.0) == []


def test_search_ignores_chunks_from_other_embedding_model(salon):
    KnowledgeChunk.objects.update(embedding_model="old-model")
    assert knowledge.search(salon, "manicure", k=5, max_distance=2) == []


def test_signals_queue_reindex_on_commit(salon, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        FAQ.objects.create(
            salon=salon, question="Parking?", answer="Free parking behind."
        )
    assert KnowledgeChunk.objects.filter(title="Parking?").exists()

    service = salon.services.get(name="Haircut")
    nino = StaffFactory(salon=salon, full_name="Nino")
    with django_capture_on_commit_callbacks(execute=True):
        nino.services.add(service)
    assert "Specialists: Nino" in KnowledgeChunk.objects.get(title="Haircut").content

    with django_capture_on_commit_callbacks(execute=True):
        WorkingHoursFactory(
            staff=nino, weekday=Weekday.SUNDAY, start_time=time(11), end_time=time(15)
        )
    assert "Sunday: 11:00–15:00" in KnowledgeChunk.objects.get(source="salon").content

    with django_capture_on_commit_callbacks(execute=True):
        service.delete()
    assert not KnowledgeChunk.objects.filter(title="Haircut").exists()


def test_signal_broker_outage_does_not_break_save(
    salon, django_capture_on_commit_callbacks
):
    with (
        mock.patch(
            "apps.assistant.tasks.index_source.delay", side_effect=ConnectionError
        ),
        django_capture_on_commit_callbacks(execute=True),
    ):
        FAQ.objects.create(salon=salon, question="Q", answer="A")
    assert FAQ.objects.filter(question="Q").exists()


def test_reindex_command(salon, capsys):
    from django.core.management import call_command

    KnowledgeChunk.objects.all().delete()
    call_command("reindex_assistant", "kb")
    assert "kb:" in capsys.readouterr().out
    assert KnowledgeChunk.objects.filter(salon=salon).count() == 4


# --- chat pipeline -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "lang"),
    [("How much is a manicure?", "en"), ("მანიკური რა ღირს?", "ka"), ("ok 123", "en")],
)
def test_detect_language(text, lang):
    assert chat.detect_language(text) == lang


def item(n, title="T"):
    return ContextItem(n, "faq", n, title, "content")


def test_clean_citations_keeps_only_retrieved_numbers():
    text, cited = chat._clean_citations(
        "Yes [2]. Also [7] and again [2]. Plus [1].", [item(1, "A"), item(2, "B")]
    )
    assert text == "Yes [2]. Also and again [2]. Plus [1]."
    assert [c.title for c in cited] == ["B", "A"]


def test_answer_from_faq_with_source(salon):
    result = chat.answer(salon, "Can I pay by card?")
    assert result.language == "en"
    assert "Visa and Mastercard" in result.text
    assert [s.title for s in result.sources] == ["Can I pay by card?"]
    assert result.slots == []


def test_booking_question_suggests_free_slots(salon):
    result = chat.answer(salon, "When can I book a manicure?", now=MONDAY)
    assert "Manicure" in result.text
    assert [s.start.astimezone(TZ).strftime("%H:%M") for s in result.slots][:3] == [
        "10:00",
        "10:15",
        "10:30",
    ]
    assert len(result.slots) == 6
    url = urlparse(result.slots[0].url)
    assert url.path == reverse("bookings:details", args=["kb"])
    params = parse_qs(url.query)
    assert params["staff"] == ["any"]
    assert params["service"] == [str(salon.services.get(name="Manicure").pk)]


def test_georgian_question_gets_georgian_answer(salon):
    FAQ.objects.create(
        salon=salon, question="ბარათით გადახდა?", answer="დიახ, ბარათით."
    )
    knowledge.reindex_salon(salon)
    result = chat.answer(salon, "შეიძლება ბარათით გადახდა?")
    assert result.language == "ka"
    assert "დიახ" in result.text


def test_unknown_question_falls_back_to_phone(salon, settings):
    settings.ASSISTANT_MAX_DISTANCE = 0.0  # nothing retrieved
    result = chat.answer(salon, "Do you sell cars?")
    assert "+995431000000" in result.text
    assert result.sources == []


def test_refusal_gives_polite_message(salon):
    with mock.patch(
        "apps.assistant.providers.fake.FakeLLM.answer",
        return_value=ChatResult("", refused=True),
    ):
        result = chat.answer(salon, "anything")
    assert "can't help" in result.text


def test_prompt_contains_salon_language_context_and_history(salon):
    captured = {}

    def fake_answer(self, request):
        captured["request"] = request
        return ChatResult("ok")

    with mock.patch("apps.assistant.providers.fake.FakeLLM.answer", fake_answer):
        chat.answer(
            salon,
            "manicure — მანიკური?",
            [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
            ],
        )
    req = captured["request"]
    assert "Kutaisi Beauty" in req.system and "Reply in Georgian" in req.system
    assert req.messages[:2] == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    assert req.messages[-1]["content"].startswith("<salon_info>")
    assert 'type="service"' in req.messages[-1]["content"]
    assert [t.name for t in req.tools] == ["find_free_slots"]


# --- slot tool ---------------------------------------------------------------------


def test_slot_tool_rejects_other_salons_service(salon):
    foreign = ServiceFactory(name="Foreign")
    tool = chat.SlotTool(salon, now=MONDAY)
    result = json.loads(tool(service_id=foreign.pk))
    assert result["error"] == "unknown service_id"
    assert {name for _, name in result["services"]} == {"Manicure", "Haircut"}
    assert tool.suggestions == []


def test_slot_tool_respects_bookings_and_date(salon):
    manicure = salon.services.get(name="Manicure")
    ana = salon.staff_members.get(full_name="Ana")
    BookingFactory(
        salon=salon, service=manicure, staff=ana, start_at=MONDAY.replace(hour=10)
    )
    tool = chat.SlotTool(salon, now=MONDAY)
    result = json.loads(tool(service_id=manicure.pk, date="2030-01-07"))
    assert result["free_times"][0] == "Monday 2030-01-07 11:00"


def test_slot_tool_clamps_bad_and_past_dates(salon):
    manicure = salon.services.get(name="Manicure")
    tool = chat.SlotTool(salon, now=MONDAY)
    for value in ("garbage", "2020-01-01"):
        assert (
            json.loads(tool(service_id=manicure.pk, date=value))["searched_from"]
            == "2030-01-07"
        )
    far = json.loads(tool(service_id=manicure.pk, date="2031-01-01"))["searched_from"]
    assert far == (MONDAY.date() + timedelta(days=30)).isoformat()


def test_slot_tool_no_staff_means_no_slots(salon):
    haircut = salon.services.get(name="Haircut")  # nobody offers it
    assert (
        json.loads(chat.SlotTool(salon, now=MONDAY)(service_id=haircut.pk))[
            "free_times"
        ]
        == []
    )
