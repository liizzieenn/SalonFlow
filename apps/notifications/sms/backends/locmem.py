from collections.abc import Sequence

from .base import BaseSMSBackend, SMSMessage

#: Messages "sent" by :class:`LocMemBackend`; tests clear and inspect it.
outbox: list[SMSMessage] = []


class LocMemBackend(BaseSMSBackend):
    """Test backend: stores messages in :data:`outbox`."""

    def send_messages(self, messages: Sequence[SMSMessage]) -> int:
        outbox.extend(messages)
        return len(messages)
