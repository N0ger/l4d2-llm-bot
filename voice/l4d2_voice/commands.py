"""Phrase table: spoken phrases -> console commands.

A command file (see voice/commands/*.toml) has three parts:

    [bots]                    # survivor name -> how it is spoken
    coach = ["coach", "kołcz"]

    [[command]]               # phrase without a bot name
    name = "wait"
    say = ["czekajcie"]
    run = ["vocalize PlayerWaitHere"]

    [[bot_command]]           # phrase combined with a bot name, either order
    name = "wait"
    say = ["czekaj"]
    run = ["scripted_user_func l4b,{bot},wait"]
"""

import re
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .bridge import check_command

_WORD = re.compile(r"[^\W\d_]+(?:'[^\W\d_]+)?", re.UNICODE)


def normalize(text: str) -> tuple[str, ...]:
    return tuple(w.lower() for w in _WORD.findall(text))


@dataclass(frozen=True)
class Action:
    name: str
    commands: tuple[str, ...]
    bot: str | None = None

    @property
    def label(self) -> str:
        return f"{self.bot} {self.name}" if self.bot else self.name


class CommandTable:
    def __init__(self, phrases: dict[tuple[str, ...], Action]):
        if not phrases:
            raise ValueError("command table is empty")
        self.phrases = phrases
        # Longest phrases first, so "coach czekaj" wins over "czekaj".
        self._by_length = sorted(phrases, key=len, reverse=True)

    @classmethod
    def load(cls, path: Path) -> "CommandTable":
        with open(path, "rb") as f:
            return cls.from_dict(tomllib.load(f))

    @classmethod
    def from_dict(cls, data: dict) -> "CommandTable":
        phrases: dict[tuple[str, ...], Action] = {}

        def add(phrase: tuple[str, ...], action: Action) -> None:
            if not phrase:
                return
            existing = phrases.get(phrase)
            if existing and existing != action:
                raise ValueError(f"phrase {' '.join(phrase)!r} is used by {existing.label} and {action.label}")
            phrases[phrase] = action

        for entry in data.get("command", []):
            action = Action(entry["name"], tuple(check_command(c) for c in entry["run"]))
            for say in entry["say"]:
                add(normalize(say), action)

        bots: dict[str, list[str]] = data.get("bots", {})
        for entry in data.get("bot_command", []):
            for bot, aliases in bots.items():
                commands = tuple(check_command(c.replace("{bot}", bot.lower())) for c in entry["run"])
                action = Action(entry["name"], commands, bot=bot.lower())
                for alias in aliases:
                    for say in entry["say"]:
                        add(normalize(f"{alias} {say}"), action)
                        add(normalize(f"{say} {alias}"), action)

        return cls(phrases)

    def match(self, text: str) -> Action | None:
        words = normalize(text)
        for phrase in self._by_length:
            n = len(phrase)
            for i in range(len(words) - n + 1):
                if words[i : i + n] == phrase:
                    return self.phrases[phrase]
        return None

    def grammar(self) -> list[str]:
        """Phrase list for a Vosk recognizer; [unk] soaks up everything else."""
        return sorted(" ".join(p) for p in self.phrases) + ["[unk]"]

    def vocabulary(self) -> set[str]:
        return {w for phrase in self.phrases for w in phrase}


class Cooldown:
    """Drops the same action repeated within `seconds` (echo, double recognition)."""

    def __init__(self, seconds: float, clock=time.monotonic):
        self.seconds = seconds
        self._clock = clock
        self._last: dict[Action, float] = {}

    def ready(self, action: Action) -> bool:
        now = self._clock()
        last = self._last.get(action)
        if last is not None and now - last < self.seconds:
            return False
        self._last[action] = now
        return True
