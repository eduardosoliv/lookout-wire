import json
import threading
import time
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from lookout_wire.adapters.whatsapp import WhatsAppMessenger
from lookout_wire.core.capability import FatalError
from lookout_wire.core.ports import SendStatus
from lookout_wire.core.state import read_json, write_json

BASE_URL = "https://api.whatsapp.test/agent/v1"
TOKEN = "secret-token"
OWNER = "user:111"
START = datetime(2026, 10, 15, 14, 0, tzinfo=UTC)


class Clock:
    def __init__(self, now: datetime = START):
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class Script:
    """Answers each request with the next scripted response and records the request."""

    def __init__(self, *steps):
        self.steps = list(steps)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        step = self.steps.pop(0)
        if callable(step):
            step = step(request)
        if isinstance(step, Exception):
            raise step
        return step

    def sends(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == "POST"]

    def polls(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == "GET"]


def sent(message_id: str = "wamid.1") -> httpx.Response:
    return httpx.Response(200, json={"messages": [{"id": message_id}]})


def error(status: int, code: int, message: str) -> httpx.Response:
    return httpx.Response(status, json={"error": {"message": message, "code": code}})


def updates(next_offset: int, *messages: tuple[str, datetime]) -> httpx.Response:
    value = {
        "messaging_product": "whatsapp",
        "contacts": [],
        "messages": [
            {
                "from": sender,
                "id": f"wamid.{i}",
                "timestamp": str(int(at.timestamp())),
                "type": "text",
            }
            for i, (sender, at) in enumerate(messages)
        ],
        "statuses": [],
    }
    body = {
        "object": "whatsapp_agent_platform",
        "entry": [{"id": "1", "changes": [{"field": "messages", "value": value}]}],
        "next_offset": next_offset,
    }
    return httpx.Response(200, json=body)


def empty() -> httpx.Response:
    return httpx.Response(204)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def stop():
    return threading.Event()


@pytest.fixture
def state_path(tmp_path):
    return tmp_path / "state" / "whatsapp.json"


@pytest.fixture
def build(state_path, stop, clock):
    def make(script: Script, paired: bool = True) -> WhatsAppMessenger:
        if paired:
            write_json(state_path, {"recipient_id": OWNER})
        messenger = WhatsAppMessenger(
            token=TOKEN,
            base_url=BASE_URL,
            state_path=state_path,
            stop=stop,
            connect_timeout=5,
            read_timeout=15,
            poll_read_timeout=30,
            clock=clock,
            transport=httpx.MockTransport(script),
            poll_retry_wait=0,
        )
        if paired:
            messenger.ensure_paired()
        return messenger

    return make


def test_2xx_returns_sent_with_message_id_and_posts_text(build):
    script = Script(sent("wamid.abc"))
    messenger = build(script)

    outcome = messenger.send("AAPL below 180")

    assert outcome.status == SendStatus.SENT
    assert "wamid.abc" in outcome.detail
    request = script.sends()[0]
    assert str(request.url) == f"{BASE_URL}/messages"
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert json.loads(request.content) == {
        "messaging_product": "whatsapp",
        "to": OWNER,
        "type": "text",
        "text": {"body": "AAPL below 180"},
    }


def test_after_2xx_the_next_send_waits_at_least_5_seconds(build, clock):
    messenger = build(Script(sent()))

    messenger.send("hi")

    assert not messenger.ready(clock.now + timedelta(seconds=4.9))
    assert messenger.ready(clock.now + timedelta(seconds=5))


def test_covers_ae11_429_backoff_doubles_caps_at_60_and_resets_after_2xx(build, clock):
    script = Script(*[httpx.Response(429)] * 6, sent(), httpx.Response(429))
    messenger = build(script)
    expected = [5, 10, 20, 40, 60, 60]

    for seconds in expected:
        outcome = messenger.send("hi")
        assert outcome.status == SendStatus.RATE_LIMITED
        assert "429" in outcome.detail
        assert not messenger.ready(clock.now + timedelta(seconds=seconds - 0.1))
        assert messenger.ready(clock.now + timedelta(seconds=seconds))
        clock.advance(seconds)

    assert messenger.send("hi").status == SendStatus.SENT
    clock.advance(5)
    assert messenger.send("hi").status == SendStatus.RATE_LIMITED
    assert messenger.ready(clock.now + timedelta(seconds=5))


def test_503_with_131016_is_not_accepted_with_backoff(build, clock):
    messenger = build(Script(error(503, 131016, "(#131016) Service unavailable")))

    outcome = messenger.send("hi")

    assert outcome.status == SendStatus.NOT_ACCEPTED
    assert "503" in outcome.detail and "131016" in outcome.detail
    assert not messenger.ready(clock.now + timedelta(seconds=4.9))


@pytest.mark.parametrize(
    "step",
    [
        httpx.Response(500, text="oops"),
        httpx.Response(503, text="no code"),
        httpx.ReadTimeout("read timed out"),
        httpx.RemoteProtocolError("connection reset"),
    ],
)
def test_failures_after_the_request_left_are_unknown(build, step):
    messenger = build(Script(step))

    assert messenger.send("hi").status == SendStatus.UNKNOWN


@pytest.mark.parametrize(
    "step",
    [httpx.ConnectError("refused"), httpx.ConnectTimeout("connect timed out")],
)
def test_connect_failures_are_not_accepted_with_backoff(build, clock, step):
    messenger = build(Script(step))

    outcome = messenger.send("hi")

    assert outcome.status == SendStatus.NOT_ACCEPTED
    assert not messenger.ready(clock.now + timedelta(seconds=4.9))
    assert messenger.ready(clock.now + timedelta(seconds=5))


@pytest.mark.parametrize(
    "response",
    [
        error(401, 190, "(#190) Missing authorization header"),
        error(400, 100, "(#100) Invalid OAuth access token"),
    ],
)
def test_invalid_token_raises_fatal_error_with_the_response_message(build, response):
    messenger = build(Script(response))

    with pytest.raises(FatalError) as raised:
        messenger.send("hi")

    message = str(raised.value)
    assert response.json()["error"]["message"] in message
    assert "token" in message
    assert TOKEN not in message


def test_400_code_100_with_a_field_name_is_the_length_cap_and_does_not_raise(build):
    messenger = build(Script(error(400, 100, "text.body")))

    outcome = messenger.send("x" * 5000)

    assert outcome.status == SendStatus.OTHER_CLIENT_ERROR
    assert "400" in outcome.detail and "100" in outcome.detail


def test_400_code_131009_is_other_client_error(build):
    messenger = build(Script(error(400, 131009, "(#131009) Missing or malformed required fields")))

    assert messenger.send("hi").status == SendStatus.OTHER_CLIENT_ERROR


def test_403_clears_the_id_and_pairs_again(build, state_path, clock):
    new_owner = "user:222"
    script = Script(
        error(403, 10, "(#10) Recipient is not the agent's creator"),
        updates(5, (OWNER, START - timedelta(seconds=30))),
        updates(6, (new_owner, START + timedelta(seconds=2))),
        sent(),
    )
    messenger = build(script)

    outcome = messenger.send("hi")

    assert outcome.status == SendStatus.REJECTED_RECIPIENT
    assert "403" in outcome.detail
    assert messenger.recipient_id == new_owner
    assert read_json(state_path) == {"recipient_id": new_owner}
    assert [r.url.params["offset"] for r in script.polls()] == ["0", "5"]
    assert json.loads(script.sends()[1].content)["to"] == new_owner


def test_403_clears_the_stored_id_when_pairing_is_stopped(build, state_path, stop):
    def stop_and_return_nothing(request):
        stop.set()
        return empty()

    messenger = build(Script(error(403, 10, "not the creator"), stop_and_return_nothing))

    assert messenger.send("hi").status == SendStatus.REJECTED_RECIPIENT
    assert messenger.recipient_id is None
    assert read_json(state_path) is None


@pytest.mark.parametrize(
    "step",
    [
        sent(),
        httpx.Response(429),
        error(503, 131016, "busy"),
        httpx.Response(500),
        error(400, 100, "text.body"),
        httpx.ConnectError("refused"),
        httpx.ReadTimeout("slow"),
    ],
)
def test_send_never_sleeps(build, monkeypatch, step):
    messenger = build(Script(step))

    def refuse(*args, **kwargs):
        raise AssertionError("send must not sleep")

    monkeypatch.setattr(time, "sleep", refuse)
    monkeypatch.setattr(threading.Event, "wait", refuse)

    messenger.send("hi")


def test_ensure_paired_reuses_a_stored_id_without_polling(build):
    script = Script()
    messenger = build(script)

    assert messenger.recipient_id == OWNER
    assert script.requests == []


def test_covers_ae13_pairing_ignores_older_messages_and_greets_the_new_sender(
    build, state_path, clock
):
    script = Script(
        updates(7, ("user:old", START - timedelta(minutes=5))),
        empty(),
        updates(8, (OWNER, START + timedelta(seconds=3))),
        sent(),
    )
    messenger = build(script, paired=False)

    messenger.ensure_paired()

    assert messenger.recipient_id == OWNER
    assert read_json(state_path) == {"recipient_id": OWNER}
    assert [r.url.params["offset"] for r in script.polls()] == ["0", "7", "7"]
    greeting = json.loads(script.sends()[0].content)
    assert greeting["to"] == OWNER
    assert "lookout-wire" in greeting["text"]["body"]


def test_poll_asks_for_25_seconds_and_reads_for_longer(build):
    script = Script(updates(1, (OWNER, START)), sent())
    messenger = build(script, paired=False)

    messenger.ensure_paired()

    poll = script.polls()[0]
    assert poll.url.params["timeout"] == "25"
    assert poll.url.params["limit"] == "50"
    assert poll.extensions["timeout"]["read"] == 30
    assert poll.extensions["timeout"]["read"] > 25


def test_setting_stop_ends_pairing_within_one_poll(build, state_path, stop):
    def stop_and_return_nothing(request):
        stop.set()
        return empty()

    script = Script(stop_and_return_nothing, empty())
    messenger = build(script, paired=False)

    messenger.ensure_paired()

    assert len(script.requests) == 1
    assert messenger.recipient_id is None
    assert read_json(state_path) is None


@pytest.mark.parametrize("status", [409, 429, 500])
def test_poll_retries_after_conflict_rate_limit_or_server_error(build, status):
    script = Script(httpx.Response(status), updates(1, (OWNER, START)), sent())
    messenger = build(script, paired=False)

    messenger.ensure_paired()

    assert messenger.recipient_id == OWNER
    assert [r.url.params["offset"] for r in script.polls()] == ["0", "0"]


def test_greeting_rejected_three_times_raises_fatal_error(build, state_path):
    rejected = error(403, 10, "not the creator")
    script = Script(
        updates(1, ("user:a", START)),
        rejected,
        updates(2, ("user:b", START)),
        rejected,
        updates(3, ("user:c", START)),
        rejected,
    )
    messenger = build(script, paired=False)

    with pytest.raises(FatalError):
        messenger.ensure_paired()

    assert read_json(state_path) is None


def test_greeting_with_unknown_outcome_completes_pairing(build):
    script = Script(updates(1, (OWNER, START)), httpx.ReadTimeout("slow"))
    messenger = build(script, paired=False)

    messenger.ensure_paired()

    assert messenger.recipient_id == OWNER
