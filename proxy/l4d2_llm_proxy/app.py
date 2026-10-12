"""HTTP proxy between the SourceMod plugin and the LLM."""

import logging
import os
import time

from fastapi import FastAPI

from .backends import Backend, make_backend
from .prompt import DecisionHistory, build_messages
from .schema import Decision, GameState
from .validate import InvalidOutput, parse_model_output, validate_orders

log = logging.getLogger("l4d2_llm_proxy")

SAY_COOLDOWN_SECONDS = 10.0


def create_app(backend: Backend | None = None) -> FastAPI:
    backend = backend or make_backend(os.environ.get("L4D2_LLM_BACKEND", "mock"))
    history = DecisionHistory()
    app = FastAPI(title="L4D2 LLM proxy")
    last = {"map": None, "seq": 0, "say_time": -SAY_COOLDOWN_SECONDS}

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True, "backend": backend.name}

    @app.post("/decide")
    async def decide(state: GameState) -> dict:
        started = time.monotonic()

        # New map or a restarted server: old decisions no longer mean anything.
        if state.map != last["map"] or state.seq < last["seq"]:
            history.clear()
            last["say_time"] = -SAY_COOLDOWN_SECONDS
        last["map"], last["seq"] = state.map, state.seq

        decision = Decision()
        try:
            reply = await backend.complete(build_messages(state, history), state)
            allow_say = state.time - last["say_time"] >= SAY_COOLDOWN_SECONDS
            orders, rejected = validate_orders(parse_model_output(reply), state, allow_say=allow_say)
            decision.orders = orders
            if rejected:
                log.info("seq=%d rejected: %s", state.seq, "; ".join(rejected))
            if any(o.action == "say" for o in orders):
                last["say_time"] = state.time
            history.add(state, orders)
        except InvalidOutput as exc:
            # Empty orders: whatever the bots are doing keeps running until its timeout.
            decision.error = f"invalid model output: {exc}"
            log.warning("seq=%d %s", state.seq, decision.error)
        except Exception as exc:  # backend down, network error, ...
            decision.error = f"backend error: {exc}"
            log.exception("seq=%d backend failed", state.seq)

        decision.latency_ms = round((time.monotonic() - started) * 1000)
        log.debug("seq=%d orders=%s", state.seq, decision.to_wire())
        return decision.to_wire()

    return app
