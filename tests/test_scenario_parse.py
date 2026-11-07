import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import scenario.chat as chat
from scenario.parse import click_context, parse_event
from scenario.schema import validate
from scenario.serve import Handler

ROOT = Path(__file__).resolve().parents[1]
SCENE_PATH = "fixtures/tiny_office.scene.json"
SCENE = json.loads((ROOT / SCENE_PATH).read_text())
NEAR_N1_DOOR = {"point": [2.5, 4.3], "level": "L1"}
IN_N1 = {"point": [2.5, 2.0], "level": "L1"}


def _call(i, name, args):
    return {"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def scripted(*turns):
    """Fake LLM returning one scripted assistant message per call; records what it was sent."""
    sent = []

    def llm(messages, tools, tool_choice):
        sent.append([dict(m) for m in messages])
        return {"role": "assistant", "tool_calls": [_call(len(sent), *turns[len(sent) - 1])]}, 0.001
    llm.sent = sent
    return llm


def test_click_context_finds_room_and_door():
    ctx = click_context(SCENE, NEAR_N1_DOOR)
    assert ctx["room"]["id"] == "L1_corridor" and ctx["nearest_door"]["id"] == "door_01"
    exit_ctx = click_context(SCENE, {"point": [15.6, 5.0], "level": "L1"})
    assert exit_ctx["nearest_exit"]["id"] == "exit_02"
    assert click_context(SCENE, None) == {}


def test_parse_fire_here_uses_clicked_room_and_time():
    llm = scripted(("submit_edit", {"events": [{"id": "x", "type": "fire", "t_start_s": 42, "at": {"room": "L1_office_n1"}}],
                                    "occupants": [], "assumptions": ["fire in the clicked room"]}))
    out = parse_event(SCENE, "fire spreading from here", IN_N1, 42, SCENE_PATH, llm=llm)
    assert "L1_office_n1" in llm.sent[0][0]["content"]  # the click context reached the system prompt
    assert out["questions"] == [] and len(out["events"]) == 1
    ev = out["events"][0]
    assert ev["type"] == "fire" and ev["at"] == {"room": "L1_office_n1"} and ev["t_start_s"] == 42
    assert ev["id"].startswith("u") and ev["id"].endswith("_0")
    assert validate({"scene": SCENE_PATH, "duration_s": 600, "events": out["events"]}, SCENE) == []


def test_parse_block_door_repairs_after_validator_error():
    llm = scripted(
        ("submit_edit", {"events": [{"id": "b", "type": "blocked_exit", "t_start_s": 10, "at": {"room": "L1_corridor"}}],
                         "occupants": [], "assumptions": []}),
        ("submit_edit", {"events": [{"id": "b", "type": "blocked_exit", "t_start_s": 10, "at": {"door": "door_01"}}],
                         "occupants": [], "assumptions": ["this door = door_01"]}))
    out = parse_event(SCENE, "block this door", NEAR_N1_DOOR, 10, SCENE_PATH, llm=llm)
    repair = json.loads(llm.sent[1][-1]["content"])
    assert repair["ok"] is False and any("blocked_exit needs" in e for e in repair["errors"])
    assert out["events"][0]["at"] == {"door": "door_01"} and out["cost_usd"] == pytest.approx(0.002)


def test_parse_people_here():
    llm = scripted(("submit_edit", {"events": [], "assumptions": [], "occupants": [
        {"where": {"room": "L1_office_n1"}, "count": 10, "personas": {"older_adult": 0.7, "first_time_visitor": 0.3}}]}))
    out = parse_event(SCENE, "10 visitors here, mostly older adults", IN_N1, 0, SCENE_PATH, llm=llm)
    assert out["occupants"][0]["count"] == 10 and out["occupants"][0]["personas"]["older_adult"] == 0.7


def test_parse_ambiguous_place_returns_question_not_events():
    llm = scripted(("resolve_location", {"text": "office", "kind": "room"}),
                   ("ask_user", {"question": "Which office?", "options": ["Office N1", "Office N2"]}))
    out = parse_event(SCENE, "smoke in the office", None, 5, SCENE_PATH, llm=llm)
    assert out["events"] == [] and out["questions"] == [{"question": "Which office?", "options": ["Office N1", "Office N2"]}]
    assert out["cost_usd"] == pytest.approx(0.002)


@pytest.fixture
def server(monkeypatch):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", monkeypatch
    srv.shutdown()


def _post(url, body, origin="http://localhost:5173"):
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json", "Origin": origin})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, dict(r.headers), json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), json.load(e)


def test_http_parse_with_cors(server):
    base, mp = server
    mp.setattr(chat, "call_llm", scripted(("submit_edit", {
        "events": [{"id": "r", "type": "rumor", "t_start_s": 60, "at": {"room": "L1_corridor"},
                    "params": {"text": "east exit locked", "claim": {"exit_blocked": "exit_02"}}}],
        "occupants": [], "assumptions": []})))
    code, headers, body = _post(base + "/parse", {"scene_path": SCENE_PATH, "text": "rumor: the east exit is locked",
                                                   "at": {"point": [8.0, 5.0], "level": "L1"}, "t_s": 60})
    assert code == 200 and headers["Access-Control-Allow-Origin"] == "http://localhost:5173"
    assert body["events"][0]["params"]["claim"] == {"exit_blocked": "exit_02"}
    code, headers, _ = _post(base + "/parse", {"scene_path": "../secrets.json", "text": "x"}, origin="https://evil.example")
    assert code == 400 and "Access-Control-Allow-Origin" not in headers


def test_http_chat_question_then_answer(server):
    base, mp = server
    mp.setattr(chat, "call_llm", scripted(("ask_user", {"question": "Which office?", "options": ["Office N2"]})))
    msgs = [{"role": "user", "content": "fire in an office"}]
    code, _, body = _post(base + "/chat", {"scene_path": SCENE_PATH, "messages": msgs})
    assert code == 200 and body["questions"][0]["question"] == "Which office?" and "scenario" not in body
    msgs = body["messages"] + [{"role": "user", "content": "Office N2"}]
    mp.setattr(chat, "call_llm", scripted(("submit_scenario", {
        "duration_s": 300, "assumptions": [], "occupants": [{"where": {"all": True}, "count": 6}],
        "events": [{"id": "f", "type": "fire", "t_start_s": 0, "at": {"room": "L1_office_n2"}}]})))
    code, _, body = _post(base + "/chat", {"scene_path": SCENE_PATH, "messages": msgs})
    assert code == 200 and body["questions"] == [] and validate(body["scenario"], SCENE) == []
    assert sum(o["count"] for o in body["occupants"]) == 6 and body["messages"][-1]["role"] == "assistant"
    code, _, _ = _post(base + "/chat", {"scene_path": SCENE_PATH, "messages": [{"role": "system", "content": "x"}]})
    assert code == 400
