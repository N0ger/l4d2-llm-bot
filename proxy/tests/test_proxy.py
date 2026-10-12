import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from l4d2_llm_proxy.app import create_app
from l4d2_llm_proxy.backends import MockBackend
from l4d2_llm_proxy.prompt import DecisionHistory, build_messages, render_state
from l4d2_llm_proxy.schema import GameState
from l4d2_llm_proxy.validate import InvalidOutput, parse_model_output, validate_orders

SAMPLE = json.loads((Path(__file__).parent / "sample_state.json").read_text())


@pytest.fixture
def state() -> GameState:
    return GameState.model_validate(SAMPLE)


class FixedBackend:
    """Returns a canned reply, to drive the app with arbitrary model output."""

    name = "fixed"

    def __init__(self, reply: str):
        self.reply = reply

    async def complete(self, messages, state):
        return self.reply


class FailingBackend:
    name = "failing"

    async def complete(self, messages, state):
        raise ConnectionError("model offline")


# --- parsing ----------------------------------------------------------------


def test_parse_plain_json():
    assert parse_model_output('{"orders": []}') == {"orders": []}


def test_parse_code_fence_and_chatter():
    text = 'Sure! ```json\n{"orders": [{"bot": "Ellis", "action": "reset"}]}\n``` hope that helps'
    assert parse_model_output(text)["orders"][0]["action"] == "reset"


def test_parse_garbage_raises():
    with pytest.raises(InvalidOutput):
        parse_model_output("I think Ellis should go left")


# --- validation -------------------------------------------------------------


def test_valid_move_to_item(state):
    orders, rejected = validate_orders({"orders": [{"bot": "ellis", "action": "move", "target": "medkit_212"}]}, state)
    assert rejected == []
    assert [o.to_wire() for o in orders] == [{"bot": "Ellis", "action": "move", "target": "medkit_212", "timeout": 8.0}]


def test_move_to_survivor_normalizes_name(state):
    orders, _ = validate_orders({"bot": "Coach", "action": "move", "target": "nick", "timeout": 99}, state)
    assert orders[0].target == "Nick"
    assert orders[0].timeout == 20.0


@pytest.mark.parametrize(
    "order",
    [
        {"bot": "Ellis", "action": "exec", "target": "sv_cheats 1"},  # not whitelisted
        {"bot": "Nick", "action": "move", "target": "medkit_212"},  # human, not controlled
        {"bot": "Rochelle", "action": "reset"},  # dead
        {"bot": "Ellis", "action": "move", "target": "medkit_999"},  # unknown item
        {"bot": "Ellis", "action": "move", "target": "Rochelle"},  # dead survivor
        {"bot": "Ellis", "action": "move", "target": "Ellis"},  # itself
        {"bot": "Ellis", "action": "move"},  # no target
        {"bot": "Ellis", "action": "say", "text": "   "},  # empty text
        "move Ellis",
    ],
)
def test_rejected_orders(state, order):
    orders, rejected = validate_orders({"orders": [order]}, state)
    assert orders == []
    assert len(rejected) == 1


def test_one_command_per_bot_and_one_say(state):
    raw = [
        {"bot": "Ellis", "action": "move", "target": "medkit_212"},
        {"bot": "Ellis", "action": "reset"},
        {"bot": "Ellis", "action": "say", "text": "Got a medkit!"},
        {"bot": "Coach", "action": "say", "text": "Me too"},
    ]
    orders, rejected = validate_orders(raw, state)
    assert [(o.bot, o.action) for o in orders] == [("Ellis", "move"), ("Ellis", "say")]
    assert len(rejected) == 2


def test_say_text_is_sanitized(state):
    orders, _ = validate_orders({"bot": "Ellis", "action": "say", "text": "hi;\nquit\x00" + "x" * 300}, state)
    assert "\n" not in orders[0].text and "\x00" not in orders[0].text
    assert len(orders[0].text) <= 120


def test_say_can_be_disabled(state):
    orders, _ = validate_orders({"bot": "Ellis", "action": "say", "text": "hello"}, state, allow_say=False)
    assert orders == []


# --- prompt -----------------------------------------------------------------


def test_render_state_is_compact(state):
    rendered = render_state(state)
    assert '"medkit_212"' in rendered
    assert '"class":"rifle"' in rendered
    assert '"incap"' not in rendered  # defaults dropped
    assert len(rendered) < len(json.dumps(SAMPLE))


def test_build_messages_includes_history(state):
    history = DecisionHistory()
    orders, _ = validate_orders({"bot": "Ellis", "action": "reset"}, state)
    history.add(state, orders)
    messages = build_messages(state, history)
    assert messages[0]["role"] == "system"
    assert '"reset"' in messages[1]["content"]


# --- mock backend -----------------------------------------------------------


async def _mock_orders(state):
    reply = await MockBackend().complete([], state)
    return validate_orders(parse_model_output(reply), state)


def test_mock_sends_bot_without_medkit_to_medkit_and_regroups_straggler(state):
    import asyncio

    orders, rejected = asyncio.run(_mock_orders(state))
    assert rejected == []
    by_bot = {o.bot: o for o in orders}
    assert by_bot["Ellis"].target == "medkit_212"
    assert by_bot["Coach"].target == "Nick"


# --- HTTP -------------------------------------------------------------------


def test_health():
    client = TestClient(create_app(MockBackend()))
    assert client.get("/health").json() == {"ok": True, "backend": "mock"}


def test_decide_end_to_end_with_mock():
    client = TestClient(create_app(MockBackend()))
    body = client.post("/decide", json=SAMPLE).json()
    assert {o["bot"] for o in body["orders"]} == {"Ellis", "Coach"}
    assert all(o["action"] == "move" for o in body["orders"])
    assert "error" not in body


def test_decide_with_invalid_output_returns_no_orders():
    client = TestClient(create_app(FixedBackend("no idea")))
    body = client.post("/decide", json=SAMPLE).json()
    assert body["orders"] == []
    assert body["error"].startswith("invalid model output")


def test_decide_with_backend_failure_returns_no_orders():
    client = TestClient(create_app(FailingBackend()))
    body = client.post("/decide", json=SAMPLE).json()
    assert body["orders"] == []
    assert "model offline" in body["error"]


def test_say_cooldown_uses_game_time():
    reply = json.dumps({"orders": [{"bot": "Ellis", "action": "say", "text": "Heads up"}]})
    client = TestClient(create_app(FixedBackend(reply)))

    def say_count(time, seq):
        payload = dict(SAMPLE, time=time, seq=seq)
        return len(client.post("/decide", json=payload).json()["orders"])

    assert say_count(100, 1) == 1
    assert say_count(105, 2) == 0
    assert say_count(111, 3) == 1


def test_unknown_fields_from_plugin_are_ignored():
    client = TestClient(create_app(MockBackend()))
    payload = dict(SAMPLE, future_field={"x": 1})
    assert client.post("/decide", json=payload).status_code == 200
