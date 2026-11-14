import io
import json
import urllib.error
from unittest import mock

import pytest

from apps.notifications.sms import SMSMessage, SMSSendError, get_backend, send_sms
from apps.notifications.sms.backends import locmem
from apps.notifications.sms.backends.console import ConsoleBackend
from apps.notifications.sms.backends.twilio import TwilioBackend


@pytest.fixture(autouse=True)
def clear_outbox():
    locmem.outbox.clear()


def test_send_sms_uses_configured_backend(settings):
    settings.SMS_SENDER = "Salon"
    assert send_sms("+995555000111", "hi") == 1
    assert locmem.outbox == [SMSMessage(to="+995555000111", body="hi", sender="Salon")]


def test_console_backend_writes_message():
    stream = io.StringIO()
    sent = ConsoleBackend(stream=stream).send_messages(
        [SMSMessage("+995555000111", "Your visit is tomorrow", "Salon")]
    )
    assert sent == 1
    out = stream.getvalue()
    assert "to +995555000111" in out and "Your visit is tomorrow" in out


def test_get_backend_by_path():
    backend = get_backend("apps.notifications.sms.backends.console.ConsoleBackend")
    assert isinstance(backend, ConsoleBackend)


@pytest.fixture
def twilio():
    return TwilioBackend(account_sid="AC123", auth_token="tok", from_number="+1555")


def test_twilio_posts_form_with_basic_auth(twilio):
    response = io.BytesIO(json.dumps({"sid": "SM1"}).encode())
    with mock.patch("urllib.request.urlopen") as urlopen:
        urlopen.return_value.__enter__.return_value = response
        assert twilio.send_messages([SMSMessage("+995555000111", "hi")]) == 1
    request = urlopen.call_args.args[0]
    assert request.full_url.endswith("/Accounts/AC123/Messages.json")
    assert request.get_method() == "POST"
    assert request.headers["Authorization"].startswith("Basic ")
    assert b"To=%2B995555000111" in request.data and b"Body=hi" in request.data


def test_twilio_error_raises_send_error(twilio):
    with (
        mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("down")),
        pytest.raises(SMSSendError, match="down"),
    ):
        twilio.send_messages([SMSMessage("+995555000111", "hi")])


def test_twilio_fail_silently():
    backend = TwilioBackend(
        account_sid="AC1", auth_token="t", from_number="+1", fail_silently=True
    )
    with mock.patch("urllib.request.urlopen", side_effect=TimeoutError):
        assert backend.send_messages([SMSMessage("+1", "x")]) == 0


def test_twilio_requires_configuration(settings):
    settings.TWILIO_ACCOUNT_SID = ""
    with pytest.raises(SMSSendError, match="not configured"):
        TwilioBackend()
