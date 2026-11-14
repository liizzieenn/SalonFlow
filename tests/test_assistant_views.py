from decimal import Decimal
from unittest import mock

import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from apps.assistant import chat, knowledge, ratelimit
from apps.assistant.models import FAQ
from apps.assistant.providers import ProviderError, reset_providers
from tests.factories import SalonFactory, ServiceFactory

pytestmark = pytest.mark.django_db
HX = {"HTTP_HX_REQUEST": "true"}


@pytest.fixture(autouse=True)
def _clean():
    cache.clear()
    reset_providers()
    yield
    cache.clear()


@pytest.fixture
def salon():
    salon = SalonFactory(slug="kb", name="Kutaisi Beauty", phone="+995431000000")
    ServiceFactory(salon=salon, name="Manicure", price=Decimal("35"))
    FAQ.objects.create(
        salon=salon, question="Can I pay by card?", answer="Yes, Visa and Mastercard."
    )
    knowledge.reindex_salon(salon)
    return salon


def ask(client, salon, message, **extra):
    return client.post(
        reverse("assistant:chat", args=[salon.slug]), {"message": message}, **extra
    )


def test_chat_returns_exchange_and_stores_history(client, salon):
    response = ask(client, salon, "Can I pay by card?", **HX)
    html = response.content.decode()
    assert response.status_code == 200
    assert "Can I pay by card?" in html and "Visa and Mastercard" in html
    assert "Sources:" in html
    history = client.session[f"assistant:{salon.pk}"]
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert history[1]["sources"][0]["title"] == "Can I pay by card?"


def test_history_is_trimmed_and_sent_to_model(client, salon, settings):
    settings.ASSISTANT_RATE_LIMITS = {}
    for i in range(5):
        ask(client, salon, f"card question {i}", **HX)
    history = client.session[f"assistant:{salon.pk}"]
    assert len(history) == 6
    assert history[0]["content"] == "card question 2"
    with mock.patch("apps.assistant.views.answer", wraps=chat.answer) as spy:
        ask(client, salon, "one more", **HX)
    sent = spy.call_args.args[2]
    assert len(sent) == 6 and set(sent[0]) == {"role", "content"}


def test_empty_message_204(client, salon):
    assert ask(client, salon, "   ", **HX).status_code == 204


def test_too_long_message_400(client, salon):
    response = ask(client, salon, "x" * 501, **HX)
    assert response.status_code == 400
    assert b"under 500 characters" in response.content


def test_client_rate_limit(client, salon, settings):
    settings.ASSISTANT_RATE_LIMITS = {"client": {"minute": 2}}
    assert ask(client, salon, "card?", **HX).status_code == 200
    assert ask(client, salon, "card?", **HX).status_code == 200
    response = ask(client, salon, "card?", **HX)
    assert response.status_code == 429
    assert b"too quickly" in response.content
    # A different client IP is not affected.
    assert ask(client, salon, "card?", REMOTE_ADDR="10.0.0.9", **HX).status_code == 200


def test_salon_daily_cap(client, salon, settings):
    settings.ASSISTANT_RATE_LIMITS = {"salon": {"day": 1}}
    assert ask(client, salon, "card?", **HX).status_code == 200
    assert ask(client, salon, "card?", REMOTE_ADDR="10.0.0.9", **HX).status_code == 429


def test_rate_limit_is_per_salon(client, salon, settings):
    settings.ASSISTANT_RATE_LIMITS = {"client": {"minute": 1}}
    other = SalonFactory(slug="other")
    assert ask(client, salon, "card?", **HX).status_code == 200
    assert ask(client, other, "card?", **HX).status_code == 200


def test_rate_limit_fails_closed_when_cache_down(salon):
    with mock.patch.object(ratelimit.cache, "incr", side_effect=ConnectionError):
        allowed = ratelimit.check(
            client="1.2.3.4", salon_id=salon.pk, limits={"client": {"minute": 5}}
        )
    assert allowed is False


def test_rate_limit_windows_reset(salon):
    limits = {"client": {"minute": 1}}
    assert ratelimit.check(client="ip", salon_id=1, limits=limits, now=0)
    assert not ratelimit.check(client="ip", salon_id=1, limits=limits, now=30)
    assert ratelimit.check(client="ip", salon_id=1, limits=limits, now=61)


def test_provider_error_503(client, salon):
    with mock.patch("apps.assistant.views.answer", side_effect=ProviderError("down")):
        response = ask(client, salon, "card?", **HX)
    assert response.status_code == 503
    assert b"unavailable" in response.content
    assert f"assistant:{salon.pk}" not in client.session


def test_inactive_salon_404(client, salon):
    salon.is_active = False
    salon.save()
    assert ask(client, salon, "card?", **HX).status_code == 404


def test_get_not_allowed(client, salon):
    assert client.get(reverse("assistant:chat", args=[salon.slug])).status_code == 405


def test_csrf_required(salon):
    strict = Client(enforce_csrf_checks=True)
    assert ask(strict, salon, "card?", **HX).status_code == 403


def test_non_htmx_post_redirects_to_widget(client, salon):
    response = ask(client, salon, "card?")
    assert response.status_code == 302
    assert response.url.endswith("/s/kb/book/#assistant")


def test_reset_clears_history(client, salon):
    ask(client, salon, "card?", **HX)
    response = client.post(reverse("assistant:reset", args=[salon.slug]), **HX)
    assert response.status_code == 200
    assert f"assistant:{salon.pk}" not in client.session


def test_widget_on_booking_page_shows_history(client, salon):
    html = client.get(reverse("bookings:services", args=[salon.slug])).content.decode()
    assert 'id="assistant"' in html and "Ask our assistant" in html
    ask(client, salon, "Can I pay by card?", **HX)
    html = client.get(reverse("bookings:services", args=[salon.slug])).content.decode()
    assert "Visa and Mastercard" in html
    assert '<details id="assistant"' in html and " open>" in html


def test_error_statuses_are_swapped_on_the_swap_target(client, salon):
    """htmx fires beforeSwap on the target, so the opt-in must live on #chat-log."""
    import re

    html = client.get(reverse("bookings:services", args=[salon.slug])).content.decode()
    chat_log = re.search(r'<div id="chat-log"[^>]*>', html).group(0)
    assert "hx-on::before-swap" in chat_log and "429" in chat_log


def test_widget_not_in_htmx_step_fragments(client, salon):
    html = client.get(
        reverse("bookings:services", args=[salon.slug]), **HX
    ).content.decode()
    assert 'id="assistant"' not in html
