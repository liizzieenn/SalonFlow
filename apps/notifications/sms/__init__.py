"""Pluggable SMS sending, modelled on Django's email backends.

Pick a backend with the ``SMS_BACKEND`` setting (dotted path), e.g.::

    SMS_BACKEND = "apps.notifications.sms.backends.console.ConsoleBackend"   # dev
    SMS_BACKEND = "apps.notifications.sms.backends.locmem.LocMemBackend"     # tests
    SMS_BACKEND = "apps.notifications.sms.backends.twilio.TwilioBackend"     # prod

A new provider only needs a subclass of
:class:`~apps.notifications.sms.backends.base.BaseSMSBackend` implementing
``send_messages``.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.utils.module_loading import import_string

from .backends.base import BaseSMSBackend, SMSMessage, SMSSendError

__all__ = ["BaseSMSBackend", "SMSMessage", "SMSSendError", "get_backend", "send_sms"]

DEFAULT_BACKEND = "apps.notifications.sms.backends.console.ConsoleBackend"


def get_backend(path: str | None = None, **kwargs: Any) -> BaseSMSBackend:
    """Instantiate the configured (or given) SMS backend."""
    backend_cls = import_string(
        path or getattr(settings, "SMS_BACKEND", DEFAULT_BACKEND)
    )
    return backend_cls(**kwargs)


def send_sms(to: str, body: str, *, sender: str | None = None) -> int:
    """Send one SMS with the configured backend. Returns the number sent (0 or 1)."""
    message = SMSMessage(to=to, body=body, sender=sender or settings.SMS_SENDER)
    return get_backend().send_messages([message])
