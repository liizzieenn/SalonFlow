"""Chat widget endpoints on the public salon (booking) page."""

from __future__ import annotations

from zoneinfo import ZoneInfo

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.salons.models import Salon

from . import ratelimit
from .chat import answer, detect_language
from .providers import ProviderError

MAX_MESSAGE_LENGTH = 500
#: Turns kept in the session and sent back to the model as conversation history.
HISTORY_MESSAGES = 6


def history_key(salon: Salon) -> str:
    return f"assistant:{salon.pk}"


def get_history(request: HttpRequest, salon: Salon) -> list[dict]:
    return request.session.get(history_key(salon), [])


def _client_ip(request: HttpRequest) -> str:
    # Behind the reverse proxy REMOTE_ADDR is set from X-Forwarded-For by the
    # proxy config; never trust a client-supplied header here.
    return request.META.get("REMOTE_ADDR", "unknown")


def _reply(request, salon, messages: list[dict], *, status: int = 200) -> HttpResponse:
    if not request.headers.get("HX-Request"):
        return redirect(reverse("bookings:services", args=[salon.slug]) + "#assistant")
    with timezone.override(ZoneInfo(salon.timezone)):
        return render(
            request,
            "assistant/partials/messages.html",
            {"salon": salon, "messages_list": messages},
            status=status,
        )


@require_POST
def chat(request: HttpRequest, salon_slug: str) -> HttpResponse:
    salon = get_object_or_404(Salon, slug=salon_slug, is_active=True)
    question = " ".join(request.POST.get("message", "").split())
    language = detect_language(question) if question else translation.get_language()

    with translation.override(language):
        if not question:
            return HttpResponse(status=204)
        if len(question) > MAX_MESSAGE_LENGTH:
            error = _("Please keep your question under %(n)d characters.") % {
                "n": MAX_MESSAGE_LENGTH
            }
            return _reply(
                request, salon, [{"role": "error", "content": error}], status=400
            )
        if not ratelimit.check(
            client=_client_ip(request),
            salon_id=salon.pk,
            limits=settings.ASSISTANT_RATE_LIMITS,
        ):
            error = _(
                "You're sending messages too quickly. Please try again in a moment."
            )
            return _reply(
                request, salon, [{"role": "error", "content": error}], status=429
            )

        history = get_history(request, salon)
        llm_history = [{"role": m["role"], "content": m["content"]} for m in history]
        try:
            result = answer(salon, question, llm_history)
        except ProviderError:
            error = _("The assistant is unavailable right now. Please try again later.")
            return _reply(
                request, salon, [{"role": "error", "content": error}], status=503
            )

    user_msg = {"role": "user", "content": question}
    tz = ZoneInfo(salon.timezone)
    bot_msg = {
        "role": "assistant",
        "content": result.text,
        "sources": [{"n": c.number, "title": c.title} for c in result.sources],
        "slots": [
            {
                "label": s.start.astimezone(tz).strftime("%d.%m %H:%M"),
                "service": s.service_name,
                "url": s.url,
            }
            for s in result.slots
        ],
    }
    request.session[history_key(salon)] = (history + [user_msg, bot_msg])[
        -HISTORY_MESSAGES:
    ]
    return _reply(request, salon, [user_msg, bot_msg])


@require_POST
def reset(request: HttpRequest, salon_slug: str) -> HttpResponse:
    salon = get_object_or_404(Salon, slug=salon_slug, is_active=True)
    request.session.pop(history_key(salon), None)
    if request.headers.get("HX-Request"):
        return HttpResponse("")
    return redirect(reverse("bookings:services", args=[salon.slug]) + "#assistant")
