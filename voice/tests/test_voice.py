import sys
from pathlib import Path

import pytest

from l4d2_voice.__main__ import main
from l4d2_voice.bridge import GameBridge, UnsafeCommand, check_command
from l4d2_voice.commands import Action, CommandTable, Cooldown, normalize

ROOT = Path(__file__).resolve().parent.parent
TABLES = sorted((ROOT / "commands").glob("*.toml"))
VSCRIPT = ROOT.parent / "vscript"

SMALL_TABLE = {
    "bots": {"coach": ["coach", "trener"], "ellis": ["ellis"]},
    "command": [
        {"name": "wait", "say": ["czekajcie"], "run": ["vocalize PlayerWaitHere"]},
        {"name": "follow", "say": ["za mną"], "run": ["vocalize PlayerFollowMe"]},
        {"name": "follow_all", "say": ["wszyscy za mną"], "run": ["scripted_user_func l4b,bots,follow,me"]},
    ],
    "bot_command": [
        {"name": "wait", "say": ["czekaj"], "run": ["scripted_user_func l4b,{bot},wait"]},
        {"name": "heal_me", "say": ["ulecz mnie"], "run": ["scripted_user_func l4b,{bot},heal,me"]},
    ],
}


@pytest.fixture
def table() -> CommandTable:
    return CommandTable.from_dict(SMALL_TABLE)


# --- matching ---------------------------------------------------------------


def test_normalize_keeps_polish_letters_and_drops_punctuation():
    assert normalize("Trener, ULECZ mnie!") == ("trener", "ulecz", "mnie")


@pytest.mark.parametrize(
    "text, expected",
    [
        ("czekajcie", ("wait", None)),
        ("trener czekaj", ("wait", "coach")),
        ("czekaj trener", ("wait", "coach")),  # either word order
        ("coach ulecz mnie", ("heal_me", "coach")),
        ("za mną", ("follow", None)),
        ("wszyscy za mną", ("follow_all", None)),  # longest phrase wins
        ("hej ellis czekaj chwilę", ("wait", "ellis")),  # surrounding words are fine
    ],
)
def test_match(table, text, expected):
    action = table.match(text)
    assert (action.name, action.bot) == expected


def test_bot_name_is_substituted(table):
    assert table.match("ellis czekaj").commands == ("scripted_user_func l4b,ellis,wait",)


@pytest.mark.parametrize("text", ["", "czekaj", "trener", "ulecz", "jakieś zdanie"])
def test_no_match(table, text):
    assert table.match(text) is None


def test_grammar_lists_every_phrase_plus_unk(table):
    grammar = table.grammar()
    assert grammar[-1] == "[unk]"
    assert "trener czekaj" in grammar and "czekaj trener" in grammar
    assert len(grammar) == len(table.phrases) + 1


def test_same_phrase_for_two_actions_is_an_error():
    data = {"command": [{"name": "a", "say": ["stop"], "run": ["vocalize A"]}, {"name": "b", "say": ["stop"], "run": ["vocalize B"]}]}
    with pytest.raises(ValueError, match="stop"):
        CommandTable.from_dict(data)


def test_unsafe_command_in_table_is_rejected():
    data = {"command": [{"name": "x", "say": ["boom"], "run": ["sv_cheats 1"]}]}
    with pytest.raises(UnsafeCommand):
        CommandTable.from_dict(data)


L4B2_BOTS = {"nick", "rochelle", "coach", "ellis", "bill", "zoey", "francis", "louis"}


@pytest.mark.parametrize("path", TABLES, ids=lambda p: p.name)
def test_shipped_tables_load(path):
    table = CommandTable.load(path)
    bots = {a.bot for a in table.phrases.values() if a.bot}
    assert bots == L4B2_BOTS
    assert any(a.bot is None for a in table.phrases.values())


def test_polish_table_examples():
    table = CommandTable.load(ROOT / "commands" / "pl.toml")
    assert table.match("trener czekaj tutaj").commands == ("scripted_user_func l4b,coach,wait,here",)
    assert table.match("czekajcie").commands == ("vocalize PlayerWaitHere",)
    assert table.match("franek ulecz mnie").commands == ("scripted_user_func l4b,francis,heal,me",)


# --- cooldown ---------------------------------------------------------------


def test_cooldown():
    now = [0.0]
    cooldown = Cooldown(1.5, clock=lambda: now[0])
    a, b = Action("wait", ("vocalize A",)), Action("lead", ("vocalize B",))
    assert cooldown.ready(a)
    assert not cooldown.ready(a)
    assert cooldown.ready(b)
    now[0] = 1.6
    assert cooldown.ready(a)


# --- bridge -----------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    ["vocalize PlayerWaitHere", "scripted_user_func l4b,coach,heal,me"],
)
def test_allowed_commands(command):
    assert check_command(command) == command


@pytest.mark.parametrize(
    "command",
    ["", "quit", "vocalize X;quit", 'vocalize "X"', "vocalize X\nquit", "scripted_user_func other,x", "x" * 200],
)
def test_forbidden_commands(command):
    with pytest.raises(UnsafeCommand):
        check_command(command)


def read_entries(bridge):
    lines = bridge.path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == f"S|{bridge.session}"
    return [tuple(line.split("|", 1)) for line in lines[1:]]


def test_bridge_writes_sequenced_commands(tmp_path):
    bridge = GameBridge(tmp_path)
    bridge.send(["vocalize PlayerWaitHere"])
    bridge.send(["scripted_user_func l4b,coach,heal", "vocalize PlayerHurryUp"])
    assert bridge.path == tmp_path / "ems" / "l4d2_voicecmd" / "commands.txt"
    assert read_entries(bridge) == [
        ("1", "vocalize PlayerWaitHere"),
        ("2", "scripted_user_func l4b,coach,heal"),
        ("3", "vocalize PlayerHurryUp"),
    ]
    assert not bridge.path.with_suffix(".tmp").exists()


def test_bridge_keeps_only_recent_commands(tmp_path):
    bridge = GameBridge(tmp_path, keep=3)
    for i in range(5):
        bridge.send([f"vocalize Line{i}"])
    assert [seq for seq, _ in read_entries(bridge)] == ["3", "4", "5"]


def test_bridge_rejects_whole_batch_with_unsafe_command(tmp_path):
    bridge = GameBridge(tmp_path)
    with pytest.raises(UnsafeCommand):
        bridge.send(["vocalize PlayerWaitHere", "kill"])
    assert not bridge.path.exists()


# --- CLI --------------------------------------------------------------------


def test_text_mode_end_to_end(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", iter(["trener czekaj\n", "nic\n", "czekajcie\n"]))
    assert main(["--text", "--game-dir", str(tmp_path)]) == 0
    entries = read_entries(GameBridgeView(tmp_path))
    assert [cmd for _, cmd in entries] == ["scripted_user_func l4b,coach,wait", "vocalize PlayerWaitHere"]


class GameBridgeView:
    """Reads the file a GameBridge wrote, whatever its session id."""

    def __init__(self, game_dir):
        self.path = Path(game_dir) / "ems" / "l4d2_voicecmd" / "commands.txt"
        self.session = self.path.read_text(encoding="utf-8").splitlines()[0][2:]


# --- VScript side stays in sync ------------------------------------------------


def test_vscript_whitelist_matches_bridge():
    from l4d2_voice.bridge import ALLOWED_PREFIXES, MAX_COMMAND_LENGTH

    nut = (VSCRIPT / "addon" / "scripts" / "vscripts" / "l4d2_voicecmd.nut").read_text(encoding="utf-8")
    for prefix in ALLOWED_PREFIXES:
        assert f'"{prefix}"' in nut
    assert f"MaxCommandLength = {MAX_COMMAND_LENGTH}" in nut
    assert 'File = "l4d2_voicecmd/commands.txt"' in nut


def test_vpk_build(tmp_path):
    sys.path.insert(0, str(VSCRIPT))
    try:
        import build_vpk
    finally:
        sys.path.pop(0)

    data = build_vpk.build(VSCRIPT / "addon")
    assert data[:4] == (0x55AA1234).to_bytes(4, "little")
    for name in (b"addoninfo", b"mapspawn_addon", b"l4d2_voicecmd", b"scripts/vscripts"):
        assert name in data
