"""RAG chat pipeline for one salon.

1. Detect the reply language (Georgian script → Georgian, otherwise English).
2. Retrieve the closest knowledge chunks — **this salon only**.
3. Ask the LLM to answer from those numbered chunks, citing them as ``[n]``, with a
   ``find_free_slots`` tool bound to this salon (slot engine underneath).
4. Keep only citations that point at retrieved chunks, and turn the tool's results
   into booking links (never trusting URLs written by the model).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from django.conf import settings
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.translation import gettext as _

from apps.bookings.slots import available_slots_any_staff
from apps.salons.models import Salon, Service

from . import knowledge
from .providers import ChatRequest, ContextItem, ToolSpec, get_llm

GEORGIAN_RE = re.compile(r"[Ⴀ-ჿ]")
CITATION_RE = re.compile(r"\s?\[(\d{1,2})\]")
LANGUAGE_NAMES = {"ka": "Georgian", "en": "English"}
MAX_SLOTS = 6
SLOT_SEARCH_DAYS = 7
BOOKING_HORIZON_DAYS = 30


def detect_language(text: str) -> str:
    return "ka" if GEORGIAN_RE.search(text) else "en"


@dataclass(frozen=True)
class SlotSuggestion:
    service_name: str
    start: datetime
    url: str


@dataclass
class Answer:
    text: str
    language: str
    sources: list[ContextItem] = field(default_factory=list)
    slots: list[SlotSuggestion] = field(default_factory=list)


SYSTEM_PROMPT = """\
You are the online assistant of {salon}, a beauty salon. You help clients with \
questions about the salon's services, prices, opening hours and policies, and with \
finding a free appointment time.

Answer only from the numbered entries in <salon_info> in the latest message; they \
are this salon's own data. Cite every entry you rely on with its number in square \
brackets, like [2]. If the entries don't contain the answer, say you don't know \
and suggest contacting the salon{phone}; don't guess prices, services or policies. \
Questions about other businesses or unrelated topics are out of scope: say so politely.

When the client wants to book or asks when something is available, call \
find_free_slots with the id of the matching service from <salon_info> (and a date \
if they gave one). The chat shows the returned times as booking buttons, so refer \
to them briefly instead of listing every time or writing links. If it isn't clear \
which service they mean, ask.

Reply in {language}. Keep replies short and friendly: two to four sentences of \
plain text, no markdown.\
"""


def _system_prompt(salon: Salon, language: str) -> str:
    phone = f" at {salon.phone}" if salon.phone else ""
    return SYSTEM_PROMPT.format(
        salon=salon.name, phone=phone, language=LANGUAGE_NAMES[language]
    )


def _user_turn(question: str, context: list[ContextItem]) -> str:
    entries = "\n\n".join(
        f'<entry number="{c.number}" type="{c.source}" id="{c.source_id}">\n'
        f"{c.content}\n</entry>"
        for c in context
    )
    info = entries or "(no matching entries)"
    return f"<salon_info>\n{info}\n</salon_info>\n\n{question}"


class SlotTool:
    """``find_free_slots`` bound to one salon; records what it returned."""

    name = "find_free_slots"
    description = (
        "Find the next free appointment times at this salon for one service. "
        "Returns up to 6 start times (salon local time) within a week from the "
        "given date. Use the service id from <salon_info>."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "service_id": {
                "type": "integer",
                "description": "Service id from <salon_info>.",
            },
            "date": {
                "type": "string",
                "description": "First day to search, YYYY-MM-DD. Omit to start today.",
            },
        },
        "required": ["service_id"],
        "additionalProperties": False,
    }

    def __init__(self, salon: Salon, now: datetime | None = None) -> None:
        self.salon = salon
        self.now = now or timezone.now()
        self.tz = ZoneInfo(salon.timezone)
        self.suggestions: list[SlotSuggestion] = []

    def _start_day(self, value: str | None) -> date:
        today = self.now.astimezone(self.tz).date()
        try:
            day = date.fromisoformat(value) if value else today
        except ValueError:
            day = today
        return min(max(day, today), today + timedelta(days=BOOKING_HORIZON_DAYS))

    def __call__(self, service_id: int, date: str | None = None) -> str:  # noqa: A002
        service = Service.objects.filter(
            pk=service_id, salon=self.salon, is_active=True
        ).first()
        if service is None:  # includes ids of other salons
            valid = list(
                self.salon.services.filter(is_active=True).values_list("pk", "name")
            )
            return json.dumps({"error": "unknown service_id", "services": valid})

        first = self._start_day(date)
        found: list[datetime] = []
        for offset in range(SLOT_SEARCH_DAYS):
            day = first + timedelta(days=offset)
            if day > first + timedelta(days=BOOKING_HORIZON_DAYS):
                break
            found += list(available_slots_any_staff(service, day, now=self.now))
            if len(found) >= MAX_SLOTS:
                break
        found = found[:MAX_SLOTS]

        base = reverse("bookings:details", args=[self.salon.slug])
        for start in found:
            query = urlencode(
                {"service": service.pk, "staff": "any", "start": start.isoformat()}
            )
            self.suggestions.append(
                SlotSuggestion(service.name, start, f"{base}?{query}")
            )
        return json.dumps(
            {
                "service": service.name,
                "free_times": [
                    s.astimezone(self.tz).strftime("%A %Y-%m-%d %H:%M") for s in found
                ],
                "searched_from": first.isoformat(),
            }
        )

    def spec(self) -> ToolSpec:
        def find_free_slots(
            service_id: int, date: str | None = None
        ) -> str:  # noqa: A002
            return self(service_id=service_id, date=date)

        return ToolSpec(self.name, self.description, self.input_schema, find_free_slots)


def _clean_citations(
    text: str, context: list[ContextItem]
) -> tuple[str, list[ContextItem]]:
    """Keep ``[n]`` markers that point at retrieved items; return the cited items."""
    by_number = {c.number: c for c in context}
    cited: list[ContextItem] = []

    def keep(match: re.Match) -> str:
        item = by_number.get(int(match.group(1)))
        if item is None:
            return ""
        if item not in cited:
            cited.append(item)
        return match.group(0)

    return CITATION_RE.sub(keep, text).strip(), cited


def answer(
    salon: Salon,
    question: str,
    history: list[dict[str, str]] | None = None,
    *,
    now: datetime | None = None,
) -> Answer:
    """Answer one client message for ``salon``. Raises ``ProviderError`` on failure."""
    language = detect_language(question)
    context = knowledge.search(
        salon,
        question,
        k=settings.ASSISTANT_TOP_K,
        max_distance=settings.ASSISTANT_MAX_DISTANCE,
    )
    slot_tool = SlotTool(salon, now=now)
    request = ChatRequest(
        system=_system_prompt(salon, language),
        messages=[
            *(history or []),
            {"role": "user", "content": _user_turn(question, context)},
        ],
        tools=[slot_tool.spec()],
        question=question,
        language=language,
        context=context,
        salon_phone=salon.phone,
    )
    result = get_llm().answer(request)
    with translation.override(language):
        if result.refused or not result.text:
            return Answer(
                _("Sorry, I can't help with that. Please contact the salon."), language
            )
        text, sources = _clean_citations(result.text, context)
    return Answer(text, language, sources, slot_tool.suggestions)
