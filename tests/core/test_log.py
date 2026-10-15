import logging

import httpx

from lookout_wire.core.log import configure_logging


class ListHandler(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.messages: list[str] = []

    def emit(self, record):
        self.messages.append(record.getMessage())


def test_http_requests_do_not_log_their_url():
    configure_logging()
    handler = ListHandler()
    logging.getLogger().addHandler(handler)
    transport = httpx.MockTransport(lambda request: httpx.Response(200))
    try:
        with httpx.Client(transport=transport) as client:
            client.get("https://example.test/v2/stocks/snapshots?symbols=AAPL")
    finally:
        logging.getLogger().removeHandler(handler)

    assert not [m for m in handler.messages if "example.test" in m]


def test_google_sdk_info_lines_are_silenced():
    configure_logging()
    handler = ListHandler()
    logging.getLogger().addHandler(handler)
    try:
        logging.getLogger("google_genai.models").info("AFC is enabled with max remote calls: 10.")
    finally:
        logging.getLogger().removeHandler(handler)

    assert handler.messages == []
