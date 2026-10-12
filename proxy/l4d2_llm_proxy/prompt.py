"""System prompt and compact state rendering for the model."""

import json
from collections import deque
from dataclasses import dataclass

from .schema import GameState, Order

SYSTEM_PROMPT = """\
You are the tactical brain of survivor bots in Left 4 Dead 2.
The game AI already aims, shoots and walks. You only make high-level decisions
every couple of seconds. Your orders are short-lived intents; the bots fall back
to their normal AI when an order finishes or times out.

You receive the game state as JSON:
- survivors: name, hp, temp_hp, incap, ledge, black_white, pinned_by, pos, flow
  (map progress in %), inventory slots, current order. "controlled": true means
  you command that bot.
- infected: nearby special infected with id, type, pos, dist (to nearest survivor).
- items: pickups with id (e.g. "medkit_212"), pos, dist.

Reply with ONLY a JSON object, no prose:
{"orders": [ ... ]}

Allowed orders (anything else is ignored):
- {"bot": "<controlled bot>", "action": "move", "target": "<item id or survivor name>", "timeout": <1-20 seconds>}
- {"bot": "<controlled bot>", "action": "reset"}   (cancel order, back to normal AI)
- {"bot": "<controlled bot>", "action": "say", "text": "<short chat line>"}   (rarely)

Guidelines:
- Give no order for a bot that is fine on its own; an empty list is valid.
- Do not send bots away from the group while special infected are close.
- Prefer grabbing a medkit or pills when a bot has none and one is near.
- Stay close to the humans; regroup when a bot falls far behind or runs ahead.
"""


@dataclass
class HistoryEntry:
    seq: int
    time: float
    orders: list[Order]


class DecisionHistory:
    """The last few decisions, so the model can stay consistent between turns."""

    def __init__(self, size: int = 5):
        self._entries: deque[HistoryEntry] = deque(maxlen=size)

    def add(self, state: GameState, orders: list[Order]) -> None:
        self._entries.append(HistoryEntry(state.seq, state.time, orders))

    def clear(self) -> None:
        self._entries.clear()

    def render(self) -> str:
        if not self._entries:
            return "none"
        lines = []
        for entry in self._entries:
            orders = json.dumps([o.to_wire() for o in entry.orders], separators=(",", ":"))
            lines.append(f"t={entry.time:.0f} {orders}")
        return "\n".join(lines)


def render_state(state: GameState) -> str:
    """Compact JSON of the state: defaults dropped to save tokens."""
    data = state.model_dump(by_alias=True, exclude_defaults=True, exclude={"v", "seq"})
    return json.dumps(data, separators=(",", ":"))


def build_messages(state: GameState, history: DecisionHistory) -> list[dict[str, str]]:
    user = f"Recent decisions:\n{history.render()}\n\nState:\n{render_state(state)}"
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]
