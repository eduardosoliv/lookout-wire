"""Alert text sent to WhatsApp, and the question asked to the explainer."""

from collections.abc import Sequence
from datetime import date

from lookout_wire.capabilities.watchlist.rules import Alert, AlertKind, Kind
from lookout_wire.core.ports import Explanation

MAX_LENGTH = 4096
ICONS = {Kind.DIP: "⚠️", Kind.BUY: "🔥"}


def heading(symbol: str, name: str | None) -> str:
    """The alert's first line before the price: the ticker in WhatsApp bold, then the company."""
    return title(f"*{symbol}*", name)


def title(symbol: str, name: str | None) -> str:
    """The ticker followed by the company name, without formatting."""
    if not name:
        return symbol
    return f"{symbol} ({name})"


def render(
    heading: str, price: float, alerts: Sequence[Alert], explanation: Explanation | None
) -> str:
    header = f"{heading} {_dollars(price)}\n{_level_line(headline(alerts), price)}"
    if explanation is None:
        return header
    links = "\n".join(explanation.links)
    room = MAX_LENGTH - len(header) - len(links) - 4
    parts = [header, explanation.text[: max(room, 0)].rstrip(), links]
    return "\n\n".join(part for part in parts if part)


def headline(alerts: Sequence[Alert]) -> Alert:
    """The deepest level that fired: Buy when it fired, since it implies the Dip."""
    buys = [alert for alert in alerts if alert.kind == Kind.BUY]
    return buys[0] if buys else alerts[0]


def build_question(heading: str, price: float, alerts: Sequence[Alert], day: date) -> str:
    alert = headline(alerts)
    return (
        f"Why is {heading} stock down today, {day.isoformat()}? It trades at {_dollars(price)}, "
        f"{_percent_below(alert.level, price)} below {_dollars(alert.level)}. Answer in three or "
        "four plain sentences for a WhatsApp message, based on today's news."
    )


def _level_line(alert: Alert, price: float) -> str:
    name = f"{ICONS[alert.kind]} {alert.kind.value.capitalize()}"
    line = f"{name} {_percent_below(alert.level, price)} below {_dollars(alert.level)}"
    if alert.alert == AlertKind.NEW_LOW:
        return f"{line}, new low"
    return line


def _dollars(amount: float) -> str:
    return f"${amount:,.2f}"


def _percent_below(level: float, price: float) -> str:
    return f"{(level - price) / level * 100:.2f}%"
