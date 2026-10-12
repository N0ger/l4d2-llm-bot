"""Turns raw model output into a safe, whitelisted list of orders.

Nothing from the model is ever passed through as a console command: every order
is rebuilt from scratch out of known actions and ids that exist in the state.
"""

import json
import re
from typing import Any

from .schema import (
    ALLOWED_ACTIONS,
    DEFAULT_ORDER_TIMEOUT,
    MAX_ORDER_TIMEOUT,
    MIN_ORDER_TIMEOUT,
    SAY_MAX_LEN,
    GameState,
    Order,
)

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class InvalidOutput(ValueError):
    pass


def parse_model_output(text: str) -> Any:
    """Extract the JSON value from a model reply (tolerates code fences and chatter)."""
    cleaned = _FENCE.sub("", text.strip())
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise InvalidOutput("no JSON object in model output")
    try:
        return json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        raise InvalidOutput(f"malformed JSON: {exc.msg}") from exc


def _raw_orders(raw: Any) -> list[Any]:
    if isinstance(raw, dict):
        orders = raw.get("orders")
        if orders is None:
            return [raw]  # a single bare order
        return orders if isinstance(orders, list) else []
    if isinstance(raw, list):
        return raw
    return []


def clean_text(text: str) -> str:
    text = _CONTROL.sub(" ", text)
    text = " ".join(text.split())
    return text[:SAY_MAX_LEN].strip()


def validate_orders(raw: Any, state: GameState, *, allow_say: bool = True) -> tuple[list[Order], list[str]]:
    """Return (accepted orders, reasons for rejected ones)."""
    controlled = {s.name.lower(): s.name for s in state.controlled_bots()}
    item_ids = {i.id for i in state.items}
    alive_survivors = {s.name.lower(): s.name for s in state.survivors if s.alive}

    accepted: list[Order] = []
    rejected: list[str] = []
    commanded: set[str] = set()
    said = False

    for entry in _raw_orders(raw):
        if not isinstance(entry, dict):
            rejected.append("order is not an object")
            continue

        action = entry.get("action")
        bot_raw = entry.get("bot")
        if action not in ALLOWED_ACTIONS:
            rejected.append(f"action {action!r} not allowed")
            continue
        if not isinstance(bot_raw, str) or bot_raw.lower() not in controlled:
            rejected.append(f"bot {bot_raw!r} is not a controlled bot")
            continue
        bot = controlled[bot_raw.lower()]

        if action == "say":
            text = entry.get("text")
            if not allow_say or said:
                rejected.append("say rate limited")
                continue
            if not isinstance(text, str) or not clean_text(text):
                rejected.append("say without text")
                continue
            accepted.append(Order(bot=bot, action="say", text=clean_text(text)))
            said = True
            continue

        # move / reset: at most one movement command per bot, first one wins.
        if bot in commanded:
            rejected.append(f"duplicate command for {bot}")
            continue

        if action == "reset":
            accepted.append(Order(bot=bot, action="reset"))
            commanded.add(bot)
            continue

        target = entry.get("target")
        if not isinstance(target, str):
            rejected.append("move without target")
            continue
        if target in item_ids:
            pass
        elif target.lower() in alive_survivors and target.lower() != bot.lower():
            target = alive_survivors[target.lower()]
        else:
            rejected.append(f"unknown move target {target!r}")
            continue

        timeout = entry.get("timeout", DEFAULT_ORDER_TIMEOUT)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            timeout = DEFAULT_ORDER_TIMEOUT
        timeout = float(min(max(timeout, MIN_ORDER_TIMEOUT), MAX_ORDER_TIMEOUT))

        accepted.append(Order(bot=bot, action="move", target=target, timeout=timeout))
        commanded.add(bot)

    return accepted, rejected
