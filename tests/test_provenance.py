"""Where a model call sits, told to the relay (relay 0.31): the run that set it going, the
turn and the call within it, the screen-operation trace and step, the platform; the id the
relay gives a turn, kept for the notes about it. A call to any other provider carries
nothing of the turn: no headers, no upload."""

from __future__ import annotations

import importlib
import json
import sys
from collections.abc import Iterator
from types import ModuleType
from typing import Any

import pytest
from openai import AsyncOpenAI

from nanomuse.config import LLMSettings, Settings
from nanomuse.llm import openai_chat as chat_mod
from nanomuse.llm import openai_responses as responses_mod
from nanomuse.llm import session
from nanomuse.llm.factory import create_llm
from nanomuse.schema import LLMResponse, Message

RELAY = "https://relay.example.test/v1"
ELSEWHERE = "https://api.example-vendor.test/v1"


def _httpx_of(client: Any) -> ModuleType:
    """The httpx the SDK's client is built from (the SDK may ship its own copy)."""
    for cls in type(client).__mro__:
        root = cls.__module__.split(".")[0]
        if root.startswith("httpx"):
            return sys.modules.get(root) or importlib.import_module(root)
    raise AssertionError("no httpx in the client's bases")


class Wire:
    """Every request the clients send, answered with a short reply; the relay's answers
    carry the id of the turn it kept."""

    def __init__(self) -> None:
        self.requests: list[Any] = []
        self.httpx: ModuleType | None = None

    def handler(self, request: Any) -> Any:
        httpx = self.httpx
        assert httpx is not None
        self.requests.append(request)
        body = json.loads(request.content)
        headers = {}
        if request.url.host == "relay.example.test":
            headers["x-nanomuse-request"] = f"req-{len(self.requests)}"
        if request.url.path.endswith("/responses"):
            return httpx.Response(
                200,
                headers=headers,
                json={
                    "id": "r",
                    "object": "response",
                    "created_at": 0,
                    "model": body["model"],
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "id": "m",
                            "role": "assistant",
                            "status": "completed",
                            "content": [{"type": "output_text", "text": "ok", "annotations": []}],
                        }
                    ],
                    "parallel_tool_calls": False,
                    "tool_choice": "auto",
                    "tools": [],
                },
            )
        return httpx.Response(
            200,
            headers=headers,
            json={
                "id": "x",
                "object": "chat.completion",
                "created": 0,
                "model": body["model"],
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 7, "completion_tokens": 1},
            },
        )

    def header(self, name: str) -> list[str | None]:
        return [r.headers.get(name) for r in self.requests]


@pytest.fixture()
def wire(monkeypatch: pytest.MonkeyPatch) -> Wire:
    """The clients' own HTTP client (with its response hook), answered by :class:`Wire`."""
    w = Wire()

    def make(**kw: Any) -> AsyncOpenAI:
        http = kw["http_client"]
        w.httpx = _httpx_of(http)
        # the mock rides in at construction: an assignment to the private _transport
        # stopped being read once httpx2 grew its mounts plumbing, and the wire's requests
        # went to the real network instead (the timeout and the response hook that keeps
        # the relay's turn id stay the client's own)
        kw["http_client"] = w.httpx.AsyncClient(
            transport=w.httpx.MockTransport(w.handler),
            timeout=http.timeout,
            event_hooks=http.event_hooks,
        )
        return AsyncOpenAI(**kw)

    monkeypatch.setattr(chat_mod, "AsyncOpenAI", make)
    monkeypatch.setattr(responses_mod, "AsyncOpenAI", make)
    return w


@pytest.fixture()
def cloud() -> Iterator[None]:
    """A signed-in runtime: the device named, the relay's address set; undone after."""
    session.set_device("pc-test0001")
    session.set_cloud_base(RELAY)
    yield
    session.set_device("")


def slot(**kw: Any) -> LLMSettings:
    base = {"api_key": "k", "model": "some-model", "stream": False, "tool_mode": "native"}
    return LLMSettings(**{**base, **kw})


# ------------------------------------------------------------------ the headers
def test_outside_a_turn_only_the_device_and_platform_are_said(cloud: None):
    headers = session.relay_headers()
    assert headers["X-NanoMuse-Device"] == "pc-test0001"
    assert headers["X-NanoMuse-Platform"] in ("macos", "windows", "linux", "runtime")
    assert "X-NanoMuse-Run" not in headers and "X-NanoMuse-Turn" not in headers


def test_a_turn_names_its_run_number_and_each_call(cloud: None):
    with session.turn("schedule", 4):
        first = session.relay_headers(session.next_call())
        with session.trajectory("pt-20261027-101010-ab12"):
            session.set_trajectory_step(3)
            second = session.relay_headers(session.next_call())
    after = session.relay_headers()
    assert first["X-NanoMuse-Run"] == "schedule"
    assert first["X-NanoMuse-Turn"] == "4" and first["X-NanoMuse-Step"] == "1"
    assert "X-NanoMuse-Trajectory" not in first
    assert second["X-NanoMuse-Step"] == "2"
    assert second["X-NanoMuse-Trajectory"] == "pt-20261027-101010-ab12"
    assert second["X-NanoMuse-Trajectory-Step"] == "3"
    assert "X-NanoMuse-Run" not in after


def test_a_run_without_a_turn_is_still_named(cloud: None):
    with session.run("feed"):
        headers = session.relay_headers()
    assert headers["X-NanoMuse-Run"] == "feed" and "X-NanoMuse-Turn" not in headers


def test_the_run_words_follow_the_message_sources():
    assert session.run_of("user") == "person"
    assert session.run_of("device") == "hub"
    assert session.run_of("goal") == "goal"
    assert session.run_of("reminder") == session.run_of("trigger") == "schedule"
    # a source nobody typed that has no word of its own yet is a run of the schedule's kind
    assert session.run_of("something-new") == "schedule"
    assert session.run_of("") == "person"


def test_an_id_that_is_no_header_value_is_not_sent():
    session.set_device("pc with spaces\n")
    try:
        assert "X-NanoMuse-Device" not in session.relay_headers()
    finally:
        session.set_device("")


# ------------------------------------------------------------------ the clients
async def test_a_call_to_the_relay_carries_the_provenance_and_keeps_its_id(wire: Wire, cloud: None):
    llm = create_llm(slot(base_url=RELAY))
    token = session.enter("thread-9")
    try:
        with session.turn("person", 2):
            resp = await llm.ask([Message.user("hi")])
    finally:
        session.leave(token)
    assert wire.header("x-nanomuse-run") == ["person"]
    assert wire.header("x-nanomuse-turn") == ["2"]
    assert wire.header("x-nanomuse-step") == ["1"]
    assert wire.header("x-nanomuse-conversation") == ["thread-9"]
    assert wire.header("x-nanomuse-device") == ["pc-test0001"]
    assert wire.header("x-nanomuse-platform")[0] in ("macos", "windows", "linux", "runtime")
    # the relay's id for the turn it kept, on the response and for the notes after it
    assert resp.request_id == "req-1" and resp.turn_source == "cloud"


async def test_the_responses_client_sends_the_same(wire: Wire, cloud: None):
    llm = create_llm(slot(base_url=RELAY, protocol="responses"))
    with session.turn("goal", 1):
        resp = await llm.ask([Message.user("hi")])
    assert wire.header("x-nanomuse-run") == ["goal"]
    assert wire.header("x-nanomuse-step") == ["1"]
    assert resp.request_id == "req-1"


async def test_another_vendor_hears_nothing_of_it(wire: Wire, cloud: None):
    llm = create_llm(slot(base_url=ELSEWHERE))
    with session.turn("person", 1):
        resp = await llm.ask([Message.user("hi")])
    assert wire.header("x-nanomuse-run") == [None]
    assert wire.header("x-nanomuse-device") == [None]
    assert wire.header("x-nanomuse-platform") == [None]
    # and the response carries no turn id: nothing was sent up anywhere
    assert resp.request_id == "" and resp.turn_source == ""


def test_a_call_with_no_endpoint_is_not_the_relay() -> None:
    """No relay configured yet means no slot is the relay: an empty cloud host must not
    match an empty base URL (``begin_call(None)`` for one), or the provenance headers
    would ride along to whatever the SDK's own default endpoint is."""
    session.set_cloud_base("")
    assert session.is_cloud(None) is False
    assert session.is_cloud("") is False
    assert session.is_cloud("https://api.openai.com/v1") is False
    session.set_cloud_base(RELAY)
    assert session.is_cloud(RELAY) is True
    assert session.is_cloud(None) is False
    session.set_cloud_base("")


# ------------------------------------------------------------------ the agent loop
async def test_the_agent_loop_counts_its_turns_and_names_the_run(settings: Settings):
    from nanomuse.agent import MuseAgent
    from nanomuse.llm import MockLLM
    from nanomuse.sentinel import AuditLog, Sentinel
    from nanomuse.tools import Terminate, ToolCollection
    from nanomuse.ui import HeadlessUI

    seen: list[tuple[str, int, int]] = []

    def reply(_: list[Message]) -> LLMResponse:
        t = session.current_turn()
        assert t is not None
        seen.append((t.run, t.number, t.calls))
        return LLMResponse(content="done", finish_reason="stop")

    ui = HeadlessUI()
    audit = AuditLog(settings.audit_file)
    sentinel = Sentinel(settings.sentinel, audit, ui)
    agent = MuseAgent(
        settings, MockLLM([reply, reply, reply]), ToolCollection(Terminate()), sentinel, ui, audit
    )
    await agent.run("hi")
    await agent.run("again", run="goal")
    await agent.run("once more")
    # MockLLM does not go through the clients, so the call count stays at what the loop saw
    assert seen == [("person", 1, 0), ("goal", 2, 0), ("person", 3, 0)]
    assert session.current_turn() is None


# ------------------------------------------------------------------ the server
def test_a_thumb_and_a_stop_reason_are_accepted(settings: Settings):
    import time

    from fastapi.testclient import TestClient

    from nanomuse.llm import MockLLM
    from nanomuse.server import create_app
    from nanomuse.server.service import MuseService

    settings.server.token = "secret-token"
    llm = MockLLM([LLMResponse(content="here you are")])
    service = MuseService(settings, llm=llm)
    app = create_app(settings, service)
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer secret-token"
        client.post("/api/threads/main/send", json={"text": "hi"})
        deadline = time.time() + 5
        assistant: list[dict[str, Any]] = []
        while time.time() < deadline and not assistant:
            assistant = [
                e
                for e in client.get("/api/threads/main/events").json()["events"]
                if e["type"] == "assistant"
            ]
            time.sleep(0.05)
        assert assistant
        r = client.post(
            "/api/threads/main/feedback", json={"event": assistant[0]["id"], "vote": "up"}
        )
        assert r.status_code == 200 and r.json()["event"]["feedback"] == "up"
        r = client.post(
            "/api/threads/main/feedback",
            json={"event": assistant[0]["id"], "vote": "down", "reason": "unhelpful"},
        )
        assert r.status_code == 200 and r.json()["event"]["feedback"] == "down"
        r = client.post("/api/threads/main/feedback", json={"stop_reason": "slow"})
        assert r.status_code == 200
        r = client.post("/api/threads/main/feedback", json={"stop_reason": "because"})
        assert r.status_code == 400
        r = client.post("/api/threads/main/feedback", json={"event": "nope", "vote": "up"})
        assert r.status_code == 404
