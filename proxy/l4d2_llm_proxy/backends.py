"""LLM backends. Each one takes chat messages and returns the raw reply text."""

import json
import math
from typing import Protocol

from .schema import GameState, Survivor


class Backend(Protocol):
    name: str

    async def complete(self, messages: list[dict[str, str]], state: GameState) -> str: ...


MEDKIT_SEARCH_RADIUS = 900
REGROUP_DISTANCE = 700


def _distance(a: list[int], b: list[int]) -> float:
    return math.dist(a, b)


class MockBackend:
    """Rule-based stand-in for a model, used to test the loop end to end.

    It answers in the same text format a real model would, so the parsing and
    validation path is exercised too.
    """

    name = "mock"

    async def complete(self, messages: list[dict[str, str]], state: GameState) -> str:
        orders = []
        for bot in state.controlled_bots():
            order = self._decide(bot, state)
            if order:
                orders.append(order)
        return json.dumps({"orders": orders})

    def _decide(self, bot: Survivor, state: GameState) -> dict | None:
        if bot.incap or bot.pinned_by or bot.ledge:
            return None
        if bot.order:
            return None  # let the current order run out

        if not bot.medkit:
            medkits = [i for i in state.items if i.id.startswith("medkit_")]
            if medkits:
                nearest = min(medkits, key=lambda i: _distance(bot.pos, i.pos))
                if _distance(bot.pos, nearest.pos) <= MEDKIT_SEARCH_RADIUS:
                    return {"bot": bot.name, "action": "move", "target": nearest.id, "timeout": 8}

        others = [s for s in state.survivors if s.alive and s.name != bot.name]
        humans = [s for s in others if not s.bot] or others
        if humans:
            anchor = min(humans, key=lambda s: _distance(bot.pos, s.pos))
            if _distance(bot.pos, anchor.pos) > REGROUP_DISTANCE:
                return {"bot": bot.name, "action": "move", "target": anchor.name, "timeout": 10}

        return None


def make_backend(name: str) -> Backend:
    if name == "mock":
        return MockBackend()
    raise ValueError(f"unknown backend {name!r} (available: mock)")
