"""Game state sent by the plugin and orders sent back (protocol v1)."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ALLOWED_ACTIONS = ("move", "reset", "say")

DEFAULT_ORDER_TIMEOUT = 8.0
MAX_ORDER_TIMEOUT = 20.0
MIN_ORDER_TIMEOUT = 1.0
SAY_MAX_LEN = 120


class _Lenient(BaseModel):
    # The plugin may add fields before the proxy knows about them.
    model_config = ConfigDict(extra="ignore")


class Survivor(_Lenient):
    name: str
    player: str = ""
    bot: bool = False
    controlled: bool = False
    alive: bool = True
    hp: int = 0
    temp_hp: int = 0
    incap: bool = False
    ledge: bool = False
    black_white: bool = False
    pinned_by: str = ""
    pos: list[int] = Field(default_factory=lambda: [0, 0, 0])
    flow: int | None = None
    primary: str = ""
    secondary: str = ""
    throwable: str = ""
    medkit: str = ""
    pills: str = ""
    order: str = ""


class Entity(_Lenient):
    id: str
    type: str = ""
    pos: list[int] = Field(default_factory=lambda: [0, 0, 0])
    dist: int = 0
    # Only set for "weapon" items, e.g. "rifle_ak47".
    class_: str = Field(default="", alias="class")


class GameState(_Lenient):
    v: int = 1
    seq: int = 0
    time: float = 0.0
    map: str = ""
    survivors: list[Survivor] = Field(default_factory=list)
    infected: list[Entity] = Field(default_factory=list)
    items: list[Entity] = Field(default_factory=list)

    def controlled_bots(self) -> list[Survivor]:
        return [s for s in self.survivors if s.controlled and s.alive]

    def survivor(self, name: str) -> Survivor | None:
        for s in self.survivors:
            if s.name.lower() == name.lower():
                return s
        return None


class Order(BaseModel):
    bot: str
    action: Literal["move", "reset", "say"]
    target: str | None = None
    timeout: float | None = None
    text: str | None = None

    def to_wire(self) -> dict:
        return self.model_dump(exclude_none=True)


class Decision(BaseModel):
    orders: list[Order] = Field(default_factory=list)
    error: str | None = None
    latency_ms: int = 0

    def to_wire(self) -> dict:
        wire: dict = {"orders": [o.to_wire() for o in self.orders], "latency_ms": self.latency_ms}
        if self.error:
            wire["error"] = self.error
        return wire
