import sys
import threading
from collections.abc import Sequence
from typing import TextIO

from .base import BaseSMSBackend, SMSMessage


class ConsoleBackend(BaseSMSBackend):
    """Development backend: writes each SMS to stdout instead of sending it."""

    def __init__(self, stream: TextIO | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.stream = stream or sys.stdout
        self._lock = threading.Lock()

    def send_messages(self, messages: Sequence[SMSMessage]) -> int:
        with self._lock:
            for message in messages:
                self.stream.write(
                    f"--- SMS from {message.sender or '-'} to {message.to} ---\n"
                    f"{message.body}\n"
                    f"{'-' * 40}\n"
                )
            self.stream.flush()
        return len(messages)
