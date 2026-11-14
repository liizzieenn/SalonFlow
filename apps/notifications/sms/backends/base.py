from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


class SMSSendError(Exception):
    """A provider failed to send. Tasks treat it as retryable."""


@dataclass(frozen=True)
class SMSMessage:
    to: str
    body: str
    sender: str = ""


class BaseSMSBackend:
    """Interface every SMS provider backend implements."""

    def __init__(self, fail_silently: bool = False, **kwargs) -> None:
        self.fail_silently = fail_silently

    def send_messages(self, messages: Sequence[SMSMessage]) -> int:
        """Send ``messages``; return how many were sent.

        Raise :class:`SMSSendError` on failure unless ``fail_silently``.
        """
        raise NotImplementedError
