"""WhatsApp Agent Platform messenger: sends alerts and learns the owner's recipient id."""

import logging
import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from http import HTTPStatus
from pathlib import Path
from typing import Any

import httpx

from lookout_wire.core.capability import FatalError
from lookout_wire.core.loop import utc_now
from lookout_wire.core.ports import SendOutcome, SendStatus
from lookout_wire.core.state import read_json, write_json

logger = logging.getLogger(__name__)

SPACING = timedelta(seconds=10)
FIRST_BACKOFF = timedelta(seconds=10)
MAX_BACKOFF = timedelta(seconds=60)
POLL_SECONDS = 25
POLL_LIMIT = 50
MAX_REJECTED_GREETINGS = 3
GREETING = "lookout-wire is connected. Price alerts will arrive here."
NOT_ACCEPTED_CODE = 131016
INVALID_PARAMETER_CODE = 100
BACKOFF_STATUSES = (SendStatus.RATE_LIMITED, SendStatus.NOT_ACCEPTED)
RETRY_POLL_STATUSES = (HTTPStatus.CONFLICT, HTTPStatus.TOO_MANY_REQUESTS)


class WhatsAppMessenger:
    def __init__(  # noqa: PLR0913 -- adapter configuration, passed by keyword only
        self,
        *,
        token: str,
        base_url: str,
        state_path: Path,
        stop: threading.Event,
        connect_timeout: float,
        read_timeout: float,
        poll_read_timeout: float,
        clock: Callable[[], datetime] = utc_now,
        transport: httpx.BaseTransport | None = None,
        poll_retry_wait: float = 3.0,
    ):
        self._client = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}"},
            transport=transport,
        )
        self._state_path = state_path
        self._stop = stop
        self._clock = clock
        self._poll_retry_wait = poll_retry_wait
        self._send_timeout = httpx.Timeout(read_timeout, connect=connect_timeout)
        self._poll_timeout = httpx.Timeout(poll_read_timeout, connect=connect_timeout)
        self._recipient_id: str | None = None
        self._not_before: datetime | None = None
        self._backoff = FIRST_BACKOFF

    @property
    def recipient_id(self) -> str | None:
        return self._recipient_id

    def ready(self, now: datetime) -> bool:
        return self._not_before is None or now >= self._not_before

    def send(self, text: str) -> SendOutcome:
        outcome = self._deliver(text)
        if outcome.status == SendStatus.REJECTED_RECIPIENT:
            self._forget_recipient()
            self.ensure_paired()
        return outcome

    def ensure_paired(self) -> None:
        stored = read_json(self._state_path) or {}
        if stored.get("recipient_id"):
            self._recipient_id = stored["recipient_id"]
            return
        logger.info(
            "WhatsApp is not paired. Send any message to your agent in WhatsApp to pair it."
        )
        self._pair(started=self._clock())

    def _pair(self, started: datetime) -> None:
        offset = 0
        rejected = 0
        while not self._stop.is_set():
            batch = self._poll(offset)
            if batch is None:
                continue
            offset = batch["next_offset"]
            sender = newest_sender(batch, since=started)
            if sender is None:
                continue
            if self._greet(sender):
                logger.info("WhatsApp paired with the owner's account.")
                return
            rejected += 1
            if rejected >= MAX_REJECTED_GREETINGS:
                raise FatalError(
                    "WhatsApp rejected the pairing greeting three times; "
                    "the sender is not the agent's creator."
                )

    def _greet(self, sender: str) -> bool:
        self._remember_recipient(sender)
        outcome = self._deliver(GREETING)
        if outcome.status != SendStatus.REJECTED_RECIPIENT:
            return True
        logger.warning(
            "WhatsApp rejected the pairing greeting (%s). Waiting for another message.",
            outcome.detail,
        )
        self._forget_recipient()
        return False

    def _poll(self, offset: int) -> dict[str, Any] | None:
        params = {"offset": offset, "limit": POLL_LIMIT, "timeout": POLL_SECONDS}
        try:
            response = self._client.get("/updates", params=params, timeout=self._poll_timeout)
        except httpx.TransportError as error:
            logger.warning("WhatsApp poll failed (%s). Retrying.", type(error).__name__)
            self._stop.wait(self._poll_retry_wait)
            return None
        if response.status_code == HTTPStatus.OK:
            return response.json()
        if response.status_code == HTTPStatus.NO_CONTENT:
            return None
        if response.status_code in RETRY_POLL_STATUSES or response.is_server_error:
            logger.warning("WhatsApp poll returned HTTP %s. Retrying.", response.status_code)
            self._stop.wait(self._poll_retry_wait)
            return None
        raise poll_error(response)

    def _deliver(self, text: str) -> SendOutcome:
        now = self._clock()
        outcome = self._post(text)
        self._schedule_next(now, outcome.status)
        return outcome

    def _post(self, text: str) -> SendOutcome:
        body = {
            "messaging_product": "whatsapp",
            "to": self._recipient_id,
            "type": "text",
            "text": {"body": text},
        }
        try:
            response = self._client.post("/messages", json=body, timeout=self._send_timeout)
        except (httpx.ConnectError, httpx.ConnectTimeout) as error:
            return SendOutcome(SendStatus.NOT_ACCEPTED, f"request not sent: {type(error).__name__}")
        except httpx.TransportError as error:
            return SendOutcome(SendStatus.UNKNOWN, f"no response: {type(error).__name__}")
        return classify(response)

    def _schedule_next(self, now: datetime, status: SendStatus) -> None:
        if status == SendStatus.SENT:
            self._backoff = FIRST_BACKOFF
        if status not in BACKOFF_STATUSES:
            self._not_before = now + SPACING
            return
        self._not_before = now + self._backoff
        self._backoff = min(self._backoff * 2, MAX_BACKOFF)

    def _remember_recipient(self, recipient_id: str) -> None:
        write_json(self._state_path, {"recipient_id": recipient_id})
        self._recipient_id = recipient_id

    def _forget_recipient(self) -> None:
        self._state_path.unlink(missing_ok=True)
        self._recipient_id = None


def classify(response: httpx.Response) -> SendOutcome:
    status = response.status_code
    if response.is_success:
        return SendOutcome(SendStatus.SENT, f"HTTP {status} message {message_id(response)}")
    code, message = error_fields(response)
    if is_invalid_token(status, code, message):
        raise FatalError(invalid_token_text(status, message))
    return SendOutcome(failure_status(response, code), f"HTTP {status} code {code}")


def failure_status(response: httpx.Response, code: int | None) -> SendStatus:
    status = response.status_code
    if status == HTTPStatus.TOO_MANY_REQUESTS:
        return SendStatus.RATE_LIMITED
    if status == HTTPStatus.SERVICE_UNAVAILABLE and code == NOT_ACCEPTED_CODE:
        return SendStatus.NOT_ACCEPTED
    if status == HTTPStatus.FORBIDDEN:
        return SendStatus.REJECTED_RECIPIENT
    if response.is_client_error:
        return SendStatus.OTHER_CLIENT_ERROR
    return SendStatus.UNKNOWN


def is_invalid_token(status: int, code: int | None, message: str) -> bool:
    if status == HTTPStatus.UNAUTHORIZED:
        return True
    if status != HTTPStatus.BAD_REQUEST or code != INVALID_PARAMETER_CODE:
        return False
    return not is_field_name(message)


def is_field_name(message: str) -> bool:
    """The manual's length-cap rejection carries only the field name, such as `text.body`."""
    return bool(message) and not any(character.isspace() for character in message)


def invalid_token_text(status: int, message: str) -> str:
    return (
        f"WhatsApp rejected the agent API token (HTTP {status}: {message}). "
        "The token is invalid or was rotated; set a new LOOKOUT_WHATSAPP_TOKEN and restart."
    )


def poll_error(response: httpx.Response) -> FatalError:
    code, message = error_fields(response)
    if is_invalid_token(response.status_code, code, message):
        return FatalError(invalid_token_text(response.status_code, message))
    return FatalError(
        f"WhatsApp updates poll failed with HTTP {response.status_code} code {code}: {message}"
    )


def error_fields(response: httpx.Response) -> tuple[int | None, str]:
    try:
        error = response.json().get("error") or {}
    except ValueError, AttributeError:
        return None, ""
    return error.get("code"), str(error.get("message", ""))


def message_id(response: httpx.Response) -> str:
    try:
        return response.json()["messages"][0]["id"]
    except ValueError, KeyError, IndexError, TypeError:
        return "unknown"


def newest_sender(batch: dict[str, Any], since: datetime) -> str | None:
    cutoff = int(since.timestamp())
    senders = [
        message["from"]
        for entry in batch.get("entry", [])
        for change in entry.get("changes", [])
        for message in change.get("value", {}).get("messages", [])
        if int(message["timestamp"]) >= cutoff
    ]
    return senders[-1] if senders else None
