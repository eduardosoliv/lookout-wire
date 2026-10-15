"""Alert text sent to WhatsApp, and the question asked to the explainer."""

from collections.abc import Sequence
from datetime import date

from lookout_wire.capabilities.watchlist.rules import Alert, AlertKind, Kind
from lookout_wire.core.ports import Explanation

MAX_LENGTH = 4096
LABELS = {AlertKind.CROSSED: "crossed", AlertKind.NEW_LOW: "new low"}
ICONS = {Kind.DIP: "⚠️", Kind.BUY: "🔥"}


def render(
    symbol: str, price: float, alerts: Sequence[Alert], explanation: Explanation | None
) -> str:
    header = "\n".join([f"{symbol} {price:.2f}", *(_level_line(a, price) for a in alerts)])
    if explanation is None:
        return header
    links = "\n".join(explanation.links)
    room = MAX_LENGTH - len(header) - len(links) - 4
    parts = [header, explanation.text[: max(room, 0)].rstrip(), links]
    return "\n\n".join(part for part in parts if part)


def build_question(symbol: str, price: float, alerts: Sequence[Alert], day: date) -> str:
    alert = alerts[0]
    return (
        f"Why is {symbol} stock down today, {day.isoformat()}? It trades at {price:.2f}, "
        f"{_percent_below(alert.level, price)} below {alert.level:.2f}. Answer in three or four "
        "plain sentences for a WhatsApp message, based on today's news."
    )


def _level_line(alert: Alert, price: float) -> str:
    name = f"{ICONS[alert.kind]} {alert.kind.value.capitalize()}"
    label = LABELS[alert.alert]
    return f"{name} {alert.level:.2f} {label}, {_percent_below(alert.level, price)} below"


def _percent_below(level: float, price: float) -> str:
    return f"{(level - price) / level * 100:.2f}%"
