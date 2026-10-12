"""Hands recognized commands to the game through a file in left4dead2/ems/.

The VScript add-on (vscript/ in the repo) polls the file and runs new entries
with SendToConsole. Format, one entry per line:

    S|<session id>
    <seq>|<command>

The last few commands are kept in the file so a quick burst is not lost
between two polls; the add-on skips anything with seq <= the last one it ran.
"""

import os
import random
import sys
from collections import deque
from pathlib import Path

EMS_SUBDIR = "l4d2_voicecmd"
COMMAND_FILE = "commands.txt"

# Must match AllowedPrefixes / IsAllowed in l4d2_voicecmd.nut.
ALLOWED_PREFIXES = ("vocalize ", "scripted_user_func l4b,")
FORBIDDEN_CHARS = (";", '"', "\n", "\r")
MAX_COMMAND_LENGTH = 160

_STEAM_LIBRARIES = [
    Path.home() / ".local/share/Steam",
    Path.home() / ".steam/steam",
    Path.home() / ".var/app/com.valvesoftware.Steam/.local/share/Steam",  # Flatpak
    Path("C:/Program Files (x86)/Steam"),
    Path("C:/Program Files/Steam"),
]
_GAME_SUBPATH = Path("steamapps/common/Left 4 Dead 2/left4dead2")


class UnsafeCommand(ValueError):
    pass


def check_command(command: str) -> str:
    if not command or len(command) > MAX_COMMAND_LENGTH:
        raise UnsafeCommand(f"bad length: {command!r}")
    if any(c in command for c in FORBIDDEN_CHARS):
        raise UnsafeCommand(f"forbidden character in {command!r}")
    if not command.startswith(ALLOWED_PREFIXES):
        raise UnsafeCommand(f"{command!r} must start with one of {ALLOWED_PREFIXES}")
    return command


def find_game_dir() -> Path | None:
    """Best guess at the left4dead2/ directory of a Steam install."""
    for library in _STEAM_LIBRARIES:
        candidate = library / _GAME_SUBPATH
        if candidate.is_dir():
            return candidate
    return None


class GameBridge:
    def __init__(self, game_dir: Path, keep: int = 8):
        self.path = Path(game_dir) / "ems" / EMS_SUBDIR / COMMAND_FILE
        self.session = f"{random.getrandbits(31):x}"
        self.seq = 0
        self._recent: deque[tuple[int, str]] = deque(maxlen=keep)

    def send(self, commands: list[str]) -> None:
        for command in commands:
            check_command(command)
        for command in commands:
            self.seq += 1
            self._recent.append((self.seq, command))
        self._write()

    def _write(self) -> None:
        lines = [f"S|{self.session}"] + [f"{seq}|{cmd}" for seq, cmd in self._recent]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)  # atomic, the game never sees half a file


class PrintBridge:
    """Dry run: print the commands instead of sending them to the game."""

    def send(self, commands: list[str]) -> None:
        for command in commands:
            check_command(command)
            print(f"  -> {command}", file=sys.stderr)
