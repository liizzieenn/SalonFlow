"""Twilio Programmable Messaging backend (plain HTTPS, no SDK dependency)."""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence

from django.conf import settings

from .base import BaseSMSBackend, SMSMessage, SMSSendError

API_URL = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"


class TwilioBackend(BaseSMSBackend):
    """Sends via Twilio's REST API using ``TWILIO_*`` settings."""

    timeout = 10

    def __init__(
        self,
        account_sid: str | None = None,
        auth_token: str | None = None,
        from_number: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.account_sid = account_sid or settings.TWILIO_ACCOUNT_SID
        self.auth_token = auth_token or settings.TWILIO_AUTH_TOKEN
        self.from_number = from_number or settings.TWILIO_FROM_NUMBER
        if not (self.account_sid and self.auth_token and self.from_number):
            raise SMSSendError("Twilio backend is not configured (TWILIO_* settings).")

    def _request(self, message: SMSMessage) -> urllib.request.Request:
        data = urllib.parse.urlencode(
            {"To": message.to, "From": self.from_number, "Body": message.body}
        ).encode()
        token = base64.b64encode(f"{self.account_sid}:{self.auth_token}".encode())
        return urllib.request.Request(
            API_URL.format(sid=self.account_sid),
            data=data,
            method="POST",
            headers={"Authorization": f"Basic {token.decode()}"},
        )

    def send_messages(self, messages: Sequence[SMSMessage]) -> int:
        sent = 0
        for message in messages:
            try:
                with urllib.request.urlopen(  # noqa: S310 - fixed https URL
                    self._request(message), timeout=self.timeout
                ) as response:
                    json.load(response)
                sent += 1
            except (urllib.error.URLError, TimeoutError, ValueError) as exc:
                if not self.fail_silently:
                    raise SMSSendError(
                        f"Twilio send to {message.to} failed: {exc}"
                    ) from exc
        return sent
