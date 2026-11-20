"""Passwords, sign-ins, the usage breakdown, calls and the operator's detail views."""

from __future__ import annotations

import concurrent.futures
import threading

import httpx
import pytest
from test_cloud import fake_upstream, sign_up

from nanomuse_cloud.api import create_app
from nanomuse_cloud.config import Settings
from nanomuse_cloud.db import Database
from nanomuse_cloud.identifiers import BadIdentifier, parse
from nanomuse_cloud.senders import LogSender
from nanomuse_cloud.service import Cloud, CloudError, check_password, hash_password

# the tests call the models as one of the apps would; `apps_only` (0.28) refuses other software
APP_UA = {"User-Agent": "nanoMuse/0.0 (tests)"}


def make(**overrides):
    up = fake_upstream()
    kw = dict(
        database=":memory:",
        secret="test-secret",
        admin_token="admin",
        upstream_base="http://upstream/compat/v1",
        upstream_key="sk-upstream",
        signup_tokens=0,
        daily_cap_tokens=0,
        per_minute_requests=100,
        allowance_cny=25,
        public_base="http://cloud.test",
        password_max_attempts=3,
        lockout_s=600,
    )
    kw.update(overrides)
    settings = Settings(**kw)
    sender = LogSender()
    cloud = Cloud(settings, Database(":memory:"), sender)
    app = create_app(settings, cloud, upstream_transport=httpx.ASGITransport(app=up))
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://cloud.test", headers=APP_UA)
    return app, client, sender, up, cloud, settings


def auth(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


def test_password_hashing_roundtrip():
    h = hash_password("correct horse")
    assert h.startswith("scrypt$") and check_password("correct horse", h)
    assert not check_password("wrong", h)
    assert not check_password("x", "garbage")
    assert hash_password("a") != hash_password("a")  # salted


async def test_password_set_login_change_and_lockout():
    app, client, sender, up, cloud, settings = make()
    first = await sign_up(client, sender, "13800138000", "pixel")
    key1 = first["api_key"]
    assert first["account"]["has_password"] is False and first["account"]["signed_in_via"] == "code"

    # No password yet is indistinguishable from an unknown account or a wrong password.
    r = await client.post("/v1/auth/login", json={"identifier": "13800138000", "password": "whatever1", "device": "mac"})
    no_password = (r.status_code, r.json())
    assert no_password[0] == 401 and no_password[1]["error"]["code"] == "bad_credentials"

    # Weak ones are refused; a proper one is set without `current` (none exists).
    r = await client.post("/v1/auth/password", json={"password": "short"}, headers=auth(key1))
    assert r.status_code == 400 and r.json()["error"]["code"] == "password_short"
    r = await client.post("/v1/auth/password", json={"password": "aaaaaaaaaa"}, headers=auth(key1))
    assert r.status_code == 400 and r.json()["error"]["code"] == "password_weak"
    r = await client.post("/v1/auth/password", json={"password": "correct horse 1"}, headers=auth(key1))
    assert r.status_code == 204
    me = (await client.get("/v1/me", headers=auth(key1))).json()
    assert me["account"]["has_password"] is True and me["account"]["password_set_at"]

    # A second device signs in with the password: a new key, same account, via=password.
    r = await client.post("/v1/auth/login", json={"identifier": "13800138000", "password": "correct horse 1", "device": "mac"})
    assert r.status_code == 200, r.text
    second = r.json()
    key2 = second["api_key"]
    assert key2 != key1 and second["account"]["id"] == first["account"]["id"]
    assert second["account"]["signed_in_via"] == "password" and second["account"]["sessions"] == 2

    # Unknown numbers and wrong passwords get the same answer.
    r = await client.post("/v1/auth/login", json={"identifier": "13900001111", "password": "correct horse 1"})
    unknown = (r.status_code, r.json())
    r = await client.post("/v1/auth/login", json={"identifier": "13800138000", "password": "nope nope nope"})
    wrong = (r.status_code, r.json())
    assert unknown == wrong == no_password

    # Changing it needs the current one — this key is well past the reset window.
    cloud.db._conn.execute("UPDATE api_keys SET created_at=created_at-7200")
    r = await client.post("/v1/auth/password", json={"password": "another one 2"}, headers=auth(key1))
    assert r.status_code == 400 and r.json()["error"]["code"] == "password_required"
    r = await client.post("/v1/auth/password", json={"password": "another one 2", "current": "wrong"}, headers=auth(key1))
    assert r.status_code == 400 and r.json()["error"]["code"] == "password_wrong"
    r = await client.post("/v1/auth/password", json={"password": "another one 2", "current": "correct horse 1"}, headers=auth(key1))
    assert r.status_code == 204

    # Three wrong tries lock the password (the code still works); a fresh code
    # sign-in may then set a new password without the old one.
    for _ in range(2):
        r = await client.post("/v1/auth/login", json={"identifier": "13800138000", "password": "bad"})
        assert r.status_code == 401
    r = await client.post("/v1/auth/login", json={"identifier": "13800138000", "password": "bad"})
    assert r.status_code == 429 and r.json()["error"]["code"] == "locked"
    r = await client.post("/v1/auth/login", json={"identifier": "13800138000", "password": "another one 2"})
    assert r.status_code == 429
    third = await sign_up(client, sender, "13800138000", "ipad")
    r = await client.post("/v1/auth/password", json={"password": "reset by code 3"}, headers=auth(third["api_key"]))
    assert r.status_code == 204
    r = await client.post("/v1/auth/login", json={"identifier": "13800138000", "password": "reset by code 3", "device": "win"})
    assert r.status_code == 200

    # The timeline shows what happened, never what was said.
    events = (await client.get("/v1/me/events", headers=auth(key1))).json()["events"]
    kinds = [e["kind"] for e in events]
    assert "password.set" in kinds and "password.changed" in kinds and "sign_in.password" in kinds and "sign_in.failed" in kinds
    assert all("detail" in e and len(e["detail"]) <= 200 for e in events)


async def test_a_disabled_account_is_told_so_only_with_the_right_password():
    """A probe at /v1/auth/login must not learn that an account exists and is disabled: a
    wrong password gets the answer any wrong password gets, the right one hears disabled."""
    app, client, sender, up, cloud, settings = make()
    first = await sign_up(client, sender, "someone@example.com", "pixel")
    r = await client.post("/v1/auth/password", json={"password": "correct horse 1"}, headers=auth(first["api_key"]))
    assert r.status_code == 204
    admin = {"X-Admin-Token": "admin"}
    r = await client.post("/v1/admin/disable", headers=admin, json={"identifier": "someone@example.com"})
    assert r.status_code == 204
    r = await client.post("/v1/auth/login", json={"identifier": "someone@example.com", "password": "nope nope nope"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "bad_credentials"
    r = await client.post("/v1/auth/login", json={"identifier": "someone@example.com", "password": "correct horse 1"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "account_disabled"


async def test_sessions_can_be_listed_and_revoked():
    app, client, sender, up, cloud, settings = make()
    a = await sign_up(client, sender, "dev-a@example.com", "pixel")
    b = await sign_up(client, sender, "dev-a@example.com", "mac")
    c = await sign_up(client, sender, "dev-a@example.com", "ipad")
    r = await client.get("/v1/me/sessions", headers=auth(a["api_key"]))
    sessions = r.json()["sessions"]
    assert [s["device"] for s in sessions][0] == "pixel" and sessions[0]["current"] is True
    assert {s["device"] for s in sessions} == {"pixel", "mac", "ipad"}
    assert all(s["via"] == "code" and len(s["prefix"]) == 10 for s in sessions)

    # Sign the tablet out from the phone; it stops working at once.
    ipad = next(s for s in sessions if s["device"] == "ipad")
    r = await client.delete(f"/v1/me/sessions/{ipad['prefix']}", headers=auth(a["api_key"]))
    assert r.status_code == 204
    assert (await client.get("/v1/me", headers=auth(c["api_key"]))).status_code == 401
    r = await client.delete("/v1/me/sessions/nm_nothere", headers=auth(a["api_key"]))
    assert r.status_code == 404

    # Everyone else out: only the phone stays.
    r = await client.post("/v1/auth/sign-out-all", json={}, headers=auth(a["api_key"]))
    assert r.status_code == 200 and r.json()["signed_out"] == 1
    assert (await client.get("/v1/me", headers=auth(b["api_key"]))).status_code == 401
    assert (await client.get("/v1/me", headers=auth(a["api_key"]))).status_code == 200
    r = await client.post("/v1/auth/sign-out-all", json={"all": True}, headers=auth(a["api_key"]))
    assert r.json()["signed_out"] == 1
    assert (await client.get("/v1/me", headers=auth(a["api_key"]))).status_code == 401


async def test_usage_is_broken_down_by_kind_and_model():
    app, client, sender, up, cloud, settings = make()
    data = await sign_up(client, sender)
    headers = auth(data["api_key"])
    for _ in range(2):
        r = await client.post(
            "/v1/chat/completions", json={"model": "qwen3.8-27b", "messages": [{"role": "user", "content": "hi"}]}, headers=headers
        )
        assert r.status_code == 200
    r = await client.post(
        "/v1/chat/completions", json={"model": "qwen3.8-flash", "messages": [{"role": "user", "content": "hi"}]}, headers=headers
    )
    assert r.status_code == 200
    # the hosted image route is gone; the breakdown is chat only now
    assert (
        await client.post("/v1/images/generations", json={"model": "qwen-image-3.0", "prompt": "a cat", "n": 1}, headers=headers)
    ).status_code == 404

    me = (await client.get("/v1/me", headers=headers)).json()
    usage = me["usage"]
    assert usage["kinds"] == ["chat", "image", "video", "realtime"]
    by_kind = {row["kind"]: row for row in usage["today"]["by_kind"]}
    assert by_kind["chat"]["requests"] == 3 and by_kind["chat"]["prompt_tokens"] == 300 and by_kind["chat"]["completion_tokens"] == 150
    assert "image" not in by_kind
    by_model = {(row["model"], row["kind"]): row for row in usage["total"]["by_model"]}
    assert by_model[("qwen3.8-27b", "chat")]["requests"] == 2 and by_model[("qwen3.8-flash", "chat")]["requests"] == 1
    # Money adds up: 2 × (100×3 + 50×12) + (100×0.8 + 50×2.7) micro-yuan
    total_cny = sum(row["cost_cny"] for row in usage["total"]["by_kind"])
    assert round(total_cny, 4) == round(2 * 0.0009 + 0.000215, 4)


async def test_admin_overview_and_account_detail():
    app, client, sender, up, cloud, settings = make()
    a = await sign_up(client, sender, "13800138000", "pixel")
    b = await sign_up(client, sender, "dev-a@example.com", "mac")
    ha, hb = auth(a["api_key"]), auth(b["api_key"])
    await client.post("/v1/chat/completions", json={"model": "qwen3.8-27b", "messages": [{"role": "user", "content": "hi"}]}, headers=ha)
    await client.post("/v1/chat/completions", json={"model": "qwen3.8-flash", "messages": [{"role": "user", "content": "hi"}]}, headers=hb)
    await client.post(
        "/v1/chat/completions", json={"model": "qwen3.8-27b", "messages": [{"role": "user", "content": "hi again"}]}, headers=hb
    )
    await client.post("/v1/auth/password", json={"password": "correct horse 1"}, headers=hb)
    await client.post("/v1/auth/login", json={"identifier": "dev-a@example.com", "password": "wrong wrong"})
    admin = {"X-Admin-Token": "admin"}

    assert (await client.get("/v1/admin/overview")).status_code == 401
    r = await client.get("/v1/admin/overview", headers=admin)
    assert r.status_code == 200, r.text
    ov = r.json()
    assert ov["accounts"]["total"] == 2 and ov["accounts"]["with_password"] == 1 and ov["accounts"]["live_keys"] == 2
    assert ov["today"]["requests"] == 3 and ov["today"]["active_accounts"] == 2 and ov["today"]["new_accounts"] == 2
    kinds = {row["kind"]: row for row in ov["today"]["by_kind"]}
    assert kinds["chat"]["requests"] == 3 and "image" not in kinds
    models = {row["model"] for row in ov["period"]["by_model"]}
    assert models == {"qwen3.8-27b", "qwen3.8-flash"}
    assert ov["signals_today"]["sign_ins"] == 2 and ov["signals_today"]["sign_in_failures"] == 1
    assert ov["top_accounts"][0]["hint"] == "de***@example.com" and ov["top_accounts"][0]["requests"] == 2
    # 0.22: the console shows accounts in full — the identifier rides beside the masked hint
    # (the hint stays for logs, mail and anything that leaves the console)
    assert ov["top_accounts"][0]["identifier"] == "dev-a@example.com"
    assert ov["events"][0]["kind"] == "sign_in.failed" and ov["events"][0]["hint"] == "de***@example.com"
    assert ov["events"][0]["identifier"] == "dev-a@example.com"

    r = await client.get(f"/v1/admin/accounts/{b['account']['id']}", headers=admin)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["account"]["identifier"] == "dev-a@example.com" and d["account"]["has_password"] is True
    assert "password_hash" not in d["account"]
    assert (
        d["spend"]["requests_total"] == 2
        and d["spend"]["grant_cny"] == 25
        and d["spend"]["left_cny"] == round(25 - d["spend"]["total_cny"], 4)
    )
    assert {row["kind"] for row in d["usage"]["period"]["by_kind"]} == {"chat"}
    assert len(d["usage"]["period"]["by_day"]) == 1  # one row: today, chat only
    assert d["sessions"][0]["device"] == "mac" and d["sessions"][0]["revoked_at"] is None
    assert [e["kind"] for e in d["events"]][:3] == ["sign_in.failed", "password.set", "sign_in.code"]
    # the statement: the two requests and, first of all, the allowance itself
    assert len(d["recent"]) == 3 and {x["kind"] for x in d["recent"]} == {"chat", "credit"}
    assert d["recent"][-1]["detail"] == {"credit_uy": 25_000_000, "from": "signup"}
    assert (await client.get("/v1/admin/accounts/nope", headers=admin)).status_code == 404

    r = await client.get("/v1/admin/events?kind=sign_in.failed,password.set", headers=admin)
    assert [e["kind"] for e in r.json()["events"]] == ["sign_in.failed", "password.set"]

    # The list view carries the new flags too.
    accounts = (await client.get("/v1/admin/accounts", headers=admin)).json()["accounts"]
    byid = {x["id"]: x for x in accounts}
    assert byid[b["account"]["id"]]["has_password"] is True and byid[a["account"]["id"]]["has_password"] is False
    assert all("password_hash" not in x for x in accounts)


async def test_the_accounts_list_is_one_grouped_query_with_the_old_numbers():
    """0.23: `admin_accounts` joins grouped sums instead of seven correlated subqueries per
    account. The numbers must be the ones the correlated form gave, row for row."""
    app, client, sender, up, cloud, settings = make()
    a = await sign_up(client, sender, "13800138000", "pixel")
    b = await sign_up(client, sender, "dev-a@example.com", "mac")
    c = await sign_up(client, sender, "dev-b@example.com", "pc")
    ha, hb = auth(a["api_key"]), auth(b["api_key"])
    for _ in range(3):
        await client.post(
            "/v1/chat/completions", json={"model": "qwen3.8-27b", "messages": [{"role": "user", "content": "hi"}]}, headers=ha
        )
    await client.post("/v1/chat/completions", json={"model": "qwen3.8-27b", "messages": [{"role": "user", "content": "hi"}]}, headers=hb)
    await client.post("/v1/auth/session-key", json={"device": "mac-2"}, headers=hb)
    await client.delete("/v1/me/sessions/" + b["api_key"][:12], headers=hb)
    day_start = cloud.s.day_start(int(cloud.clock()))
    rows = {r["id"]: dict(r) for r in cloud.db.admin_accounts(day_start, limit=1000)}
    assert set(rows) == {a["account"]["id"], b["account"]["id"], c["account"]["id"]}
    old = cloud.db._conn.execute(
        """SELECT a.id,
                  (SELECT COALESCE(SUM(l.charged),0) FROM ledger l WHERE l.account_id=a.id AND l.ts>=? AND l.charged>0) AS used_today,
                  (SELECT COALESCE(SUM(l.cost_uy),0) FROM ledger l WHERE l.account_id=a.id AND l.ts>=? AND l.cost_uy>0) AS spent_today_uy,
                  (SELECT COALESCE(SUM(l.cost_uy),0) FROM ledger l WHERE l.account_id=a.id AND l.cost_uy>0) AS spent_uy,
                  (SELECT COUNT(*) FROM ledger l WHERE l.account_id=a.id AND l.kind IN ('chat','image','video','realtime')) AS requests,
                  (SELECT MAX(k.last_used_at) FROM api_keys k WHERE k.account_id=a.id) AS last_active_at,
                  (SELECT COUNT(*) FROM api_keys k WHERE k.account_id=a.id AND k.revoked_at IS NULL) AS live_keys,
                  (SELECT COUNT(*) FROM devices d WHERE d.account_id=a.id) AS device_count
           FROM accounts a""",
        (day_start, day_start),
    ).fetchall()
    fields = ("used_today", "spent_today_uy", "spent_uy", "requests", "last_active_at", "live_keys", "device_count")
    for o in old:
        assert {k: rows[o["id"]][k] for k in fields} == {k: o[k] for k in fields}, o["id"]
    ra, rb, rc = rows[a["account"]["id"]], rows[b["account"]["id"]], rows[c["account"]["id"]]
    assert ra["requests"] == 3 and ra["used_today"] > 0 and ra["spent_uy"] == ra["spent_today_uy"] > 0
    assert rb["requests"] == 1 and rb["live_keys"] == 1  # the first key revoked, the session key live
    assert rc["requests"] == 0 and rc["spent_uy"] == 0 and rc["live_keys"] == 1 and rc["last_active_at"] is None

    # labels come from the ids a page shows, not from the newest N accounts
    brief = {r["id"]: r for r in cloud.db.accounts_brief([a["account"]["id"], "no-such-account", ""])}
    assert set(brief) == {a["account"]["id"]} and brief[a["account"]["id"]]["hint"] == "138****8000"
    hints, idents = cloud._labels([a["account"]["id"], c["account"]["id"]])
    assert hints[c["account"]["id"]] == "de***@example.com" and idents[a["account"]["id"]] == "+8613800138000"
    assert cloud.db.accounts_brief([]) == []

    # the allowance distribution reads four columns per account and the same spend
    allowance = {r["id_hash"]: dict(r) for r in cloud.db.allowance_rows()}
    assert len(allowance) == 3 and sorted(r["spent_uy"] for r in allowance.values()) == sorted(r["spent_uy"] for r in rows.values())
    assert all(r["grant_uy"] == 25_000_000 and r["unlimited"] == 0 for r in allowance.values())


async def chat(client, key: str, text: str = "hi", headers: dict | None = None):
    """One charged chat turn on the cheap model."""
    r = await client.post(
        "/v1/chat/completions",
        json={"model": "qwen3.8-flash", "messages": [{"role": "user", "content": text}]},
        headers={**auth(key), **(headers or {})},
    )
    assert r.status_code == 200, r.text
    return r


async def test_the_operator_grows_the_one_pool_and_the_estimate_says_so():
    """Invites are gone: the invite route is 404 and `/v1/me` carries the empty invite
    shape, while an unknown code at sign-up is still not an error. The operator can add
    to the pool; the estimate says what a new face costs before it is made."""
    app, client, sender, up, cloud, settings = make(allowance_cny=0.6)
    a = await sign_up(client, sender, "dev-a@example.com", "pixel")
    ka = a["api_key"]
    assert (await client.get("/v1/me/invite", headers=auth(ka))).status_code == 404
    assert (await client.get("/v1/admin/invites", headers={"X-Admin-Token": "admin"})).status_code == 404
    me_a = (await client.get("/v1/me", headers=auth(ka))).json()
    assert me_a["invite"] == {
        "code": "",
        "url": "",
        "invites": 0,
        "bonus_cny": 0,
        "invitee_bonus_cny": 0,
        "earned_cny": 0,
        "friends": [],
        "bonus_when": "first_use",
        "settled": 0,
        "pending": 0,
        "clips_per_invite": 0,
        "credit_cny": 0,
        "credit_left_cny": 0,
    }
    assert a["clips"]["unlimited"] is True and a["spend"]["grant"] == 0.6 and a["spend"]["left"] == 0.6
    # there is no co-creation bonus (0.9): the 0.5 fields say so for the apps of the time
    assert a["spend"]["contribute_bonus_available"] is False and a["contribute"]["bonus_available"] is False

    # an unknown code (or one's own) is not an error: the person is signed in, nobody is paid
    c = await sign_up(client, sender, "13900001111", "pixel", invite="NOPE1234")
    assert c["created"] is True and c["spend"]["grant"] == 0.6
    assert (await client.get("/v1/me", headers=auth(ka))).json()["invite"]["invites"] == 0

    # chats are charged after the fact and nothing refuses for lack of pool
    await chat(client, ka)
    me_a = (await client.get("/v1/me", headers=auth(ka))).json()
    assert me_a["spend"]["total"] > 0 and me_a["spend"]["left"] == round(0.6 - me_a["spend"]["total"], 4)

    # C has ¥0.6: the estimate said beforehand what five pictures and four clips cost
    kc = c["api_key"]
    est = (await client.get("/v1/estimate", params={"images": 5, "clips": 4}, headers=auth(kc))).json()
    assert est["cny"] == 2.9 and est["left_cny"] == 0.6 and est["grant_cny"] == 0.6 and est["affordable"] is False
    assert est["unlimited"] is False and est["clips_ok"] is True and est["left_today_cny"] == 0.6
    assert [p["kind"] for p in est["parts"]] == ["image", "video"] and est["parts"][1]["seconds"] == 5

    # The operator credits C for a pull request: straight into the pool.
    admin = {"X-Admin-Token": "admin"}
    r = await client.post("/v1/admin/credit", headers=admin, json={"identifier": "13900001111", "cny": 10, "note": "PR #12"})
    assert r.status_code == 200 and r.json()["grant_cny"] == 10.6 and r.json()["left_cny"] == 10.6 and "grant_uy" not in r.json()
    est = (await client.get("/v1/estimate", params={"images": 5, "clips": 4}, headers=auth(kc))).json()
    assert est["affordable"] is True and est["left_cny"] == 10.6
    me_c = (await client.get("/v1/me", headers=auth(kc))).json()
    assert me_c["spend"]["total"] == 0 and me_c["spend"]["left"] == 10.6
    assert any(row["kind"] == "credit" and row["detail"].get("note") == "PR #12" for row in me_c["recent"])
    r = await client.post("/v1/admin/credit", headers=admin, json={"identifier": "13900001111", "cny": 5000})
    assert r.status_code == 400

    # The operator's views carry it all, without any invite fields.
    listing = (await client.get("/v1/admin/accounts", headers=admin)).json()["accounts"]
    row_a = next(x for x in listing if x["identifier"] == "dev-a@example.com")
    assert row_a["invites"] == 0 and row_a["grant_cny"] == 0.6 and "credit_uy" not in row_a
    detail = (await client.get(f"/v1/admin/accounts/{row_a['id']}", headers=admin)).json()
    assert "clips_used" not in detail["account"]
    s = (await client.get("/v1/admin/accounts", headers=admin)).json()["settings"]
    assert s["allowance_cny"] == 0.6 and s["contribute_bonus_cny"] == 0
    assert "invite_bonus_cny" not in s and "invitee_bonus_cny" not in s
    kinds = {e["kind"] for e in (await client.get("/v1/admin/events", headers=admin)).json()["events"]}
    assert "credit.granted" in kinds


async def test_admin_series_comes_from_the_relays_own_tables():
    """The operator's time series come from the relay's own tables. The site-traffic
    panel is gone with the demo gateway: `/v1/admin/traffic` is 404 and the series
    carries no `web` or `invites` keys."""
    app, client, sender, up, cloud, settings = make(signup_tokens=1000, allowance_cny=10)
    a = await sign_up(client, sender, "13800138000", "pixel")
    await sign_up(client, sender, "dev-a@example.com", "mac")
    await client.post(
        "/v1/chat/completions", json={"model": "qwen3.8-27b", "messages": [{"role": "user", "content": "hi"}]}, headers=auth(a["api_key"])
    )
    admin = {"X-Admin-Token": "admin"}

    assert (await client.get("/v1/admin/series")).status_code == 401
    r = await client.get("/v1/admin/series?days=7", headers=admin)
    assert r.status_code == 200, r.text
    series = r.json()
    assert len(series["days"]) == 7 and series["online_devices"] == 0
    assert "web" not in series and "invites" not in series
    today = series["days"][-1]
    assert today["sign_ins"] == 2 and today["new_accounts"] == 2 and today["active_accounts"] == 1
    assert series["days"][0]["sign_ins"] == 0 and series["contributors"] == 0
    assert "13800138000" not in r.text and "dev-a@example.com" not in r.text

    assert (await client.get("/v1/admin/traffic", headers=admin)).status_code == 404


async def test_an_operator_zero_survives_a_restart_and_old_rows_still_get_seeded(tmp_path):
    """seed_grants used to pick every account with grant_uy = 0, so an account the operator
    had set to zero from the page got the allowance back at the next restart. Only rows no
    0.15+ relay has seen (allowance_uy < 0) are seeded; the operator's zero stays."""
    path = str(tmp_path / "relay.sqlite")
    app, client, sender, up, cloud, settings = make(database=path, allowance_cny=0.6)
    cloud.db.close()
    cloud = Cloud(settings, Database(path), sender)
    app = create_app(settings, cloud, upstream_transport=httpx.ASGITransport(app=up))
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://cloud.test", headers=APP_UA)
    a = await sign_up(client, sender, "dev-a@example.com", "pixel")
    b = await sign_up(client, sender, "dev-b@example.com", "mac")
    r = await client.post(
        "/v1/admin/pool", headers={"X-Admin-Token": "admin"}, json={"account_id": a["account"]["id"], "grant_cny": 0, "note": "abuse"}
    )
    assert r.status_code == 200 and r.json()["grant_cny"] == 0
    # a row from before 0.15 that never got its pool: the column default says so
    with cloud.db.tx() as c:
        c.execute("UPDATE accounts SET allowance_uy=-1, grant_uy=0 WHERE id=?", (b["account"]["id"],))
    cloud.db.close()
    again = Cloud(settings, Database(path), sender)
    assert again.db.account(a["account"]["id"])["grant_uy"] == 0, "the operator's zero came back"
    assert again.db.account(b["account"]["id"])["grant_uy"] == settings.allowance_uy
    assert again.db.seed_grants(settings.allowance_uy) == 0  # idempotent
    again.db.close()


async def test_new_accounts_of_an_unknown_domain_are_limited_per_day():
    """At most SIGNUP_PER_DOMAIN_DAY new accounts per e-mail domain per rolling day, for a
    domain that is not a well known provider or on SIGNUP_DOMAIN_ALLOW: the next new address
    of it is refused a code (`429 domain_limited`, an event without the address); an account
    of that domain that already exists always gets its code; phone numbers, gmail.com and an
    allow-listed domain are never limited; 0 switches the limit off."""
    app, client, sender, up, cloud, settings = make(
        signup_per_domain_day=3, signup_domain_allow="Friends.example.org, @club.example.net", code_per_ip_hour=1000
    )
    admin = {"X-Admin-Token": "admin"}
    for i in range(3):
        await sign_up(client, sender, f"farm{i}@farm.example.com", "mac")
    r = await client.post("/v1/auth/code", json={"identifier": "farm3@farm.example.com"})
    assert r.status_code == 429 and r.json()["error"]["code"] == "domain_limited", r.text
    assert "tomorrow" in r.json()["error"]["message"] and "farm3" not in r.text
    events = (await client.get("/v1/admin/events", params={"kind": "signup.domain_limited"}, headers=admin)).json()["events"]
    assert len(events) == 1 and events[0]["detail"] == "farm.example.com" and events[0]["account_id"] == ""
    # the domain is matched whole: a sub-domain, or a domain that merely ends the same, is its own
    assert (await client.post("/v1/auth/code", json={"identifier": "x@notfarm.example.com"})).status_code == 204
    # an existing account of the limited domain still gets its code
    assert (await client.post("/v1/auth/code", json={"identifier": "farm0@farm.example.com"})).status_code == 204
    # the well known providers, the allow list (case and a leading @ do not matter) and phone numbers are never limited
    for i in range(4):
        await sign_up(client, sender, f"person{i}@gmail.com", "mac")
        await sign_up(client, sender, f"friend{i}@friends.example.org", "mac")
        await sign_up(client, sender, f"member{i}@club.example.net", "mac")
        await sign_up(client, sender, f"1390000000{i}", "mac")
    settings_view = (await client.get("/v1/admin/accounts", headers=admin)).json()["settings"]
    assert settings_view["signup_per_domain_day"] == 3 and settings_view["signup_domain_allow"] == [
        "Friends.example.org",
        "@club.example.net",
    ]
    # off
    app, client, sender, up, cloud, settings = make(signup_per_domain_day=0, code_per_ip_hour=1000)
    for i in range(5):
        await sign_up(client, sender, f"farm{i}@farm.example.com", "mac")


async def test_gmail_dot_plus_and_googlemail_canonicalisation_and_legacy_lookup():
    """Gmail and Googlemail addresses have dots and `+tag` stripped and `googlemail.com`
    folded to `gmail.com`, so those aliases cannot open extra accounts. Plus addresses at
    other providers remain distinct. An existing account whose `id_hash` was recorded from
    a dotted Gmail address before canonicalisation still matches on sign-in and upgrades
    its `id_hash` in place."""
    assert parse("First.Last+tag@GoogleMail.COM").value == "firstlast@gmail.com"
    assert parse("First.Last+tag@GoogleMail.COM").raw_value == "first.last+tag@googlemail.com"
    assert parse("a.b.c@gmail.com").value == "abc@gmail.com"
    assert parse("dev.a+1@example.com").value == "dev.a+1@example.com"
    assert parse("dev.a+2@example.com").hash(b"k") != parse("dev.a@example.com").hash(b"k")
    for bad in ("...@gmail.com", "+tag@gmail.com"):
        with pytest.raises(BadIdentifier):
            parse(bad)

    app, client, sender, up, cloud, settings = make(allowance_cny=10, code_per_identifier_10m=10, code_per_ip_hour=1000)
    first = await sign_up(client, sender, "a.b.c@gmail.com", "pixel")
    assert first["created"] is True
    for alias in ("ab.c@gmail.com", "abc@gmail.com", "a.b.c+2@gmail.com", "abc@googlemail.com"):
        again = await sign_up(client, sender, alias, "mac")
        assert again["created"] is False and again["account"]["id"] == first["account"]["id"]

    # An account created before canonicalisation (stored with the legacy dotted hash)
    # still matches when the holder signs in with dots or with password, and upgrades its
    # id_hash to the canonical hash.
    legacy_raw = "old.user@gmail.com"
    legacy_ident = parse(legacy_raw)
    legacy_hash = legacy_ident.legacy_hash(cloud.s.hmac_key)
    assert legacy_hash is not None and legacy_hash != legacy_ident.hash(cloud.s.hmac_key)
    legacy_id = cloud.db.new_account_id()
    enc = cloud.crypto.encrypt(legacy_id, legacy_raw)
    cloud.db.create_account(legacy_hash, "email", "ol***@gmail.com", 0, enc, account_id=legacy_id, grant_uy=10_000_000)
    cloud.db.set_password(legacy_id, hash_password("correct horse 1"))

    r = await client.post("/v1/auth/login", json={"identifier": "old.user@gmail.com", "password": "correct horse 1", "device": "mac"})
    assert r.status_code == 200 and r.json()["account"]["id"] == legacy_id
    # The row's id_hash is now upgraded to the canonical hash, so undotted sign-in matches too.
    again_login = await sign_up(client, sender, "olduser@gmail.com", "pixel")
    assert again_login["created"] is False and again_login["account"]["id"] == legacy_id

    # Elsewhere plus addressing is the provider's business: two accounts from now on...
    plus = await sign_up(client, sender, "dev.a+1@example.com", "mac")
    base = await sign_up(client, sender, "dev.a@example.com", "pixel")
    assert base["created"] is True and base["account"]["id"] != plus["account"]["id"]
    # ...but an account relay 0.26 kept under the stripped form is still the tagged
    # address's when it exists, and the plain address's as well: nobody loses one
    merged_raw = "team@example.org"
    merged_id = cloud.db.new_account_id()
    cloud.db.create_account(
        parse(merged_raw).hash(cloud.s.hmac_key),
        "email",
        "te***@example.org",
        0,
        cloud.crypto.encrypt(merged_id, merged_raw),
        account_id=merged_id,
        grant_uy=10_000_000,
    )
    tagged = await sign_up(client, sender, "team+alerts@example.org", "mac")
    assert tagged["created"] is False and tagged["account"]["id"] == merged_id
    plain = await sign_up(client, sender, "team@example.org", "pixel")
    assert plain["created"] is False and plain["account"]["id"] == merged_id


async def test_the_carriers_limit_for_a_number_reads_as_too_often_not_as_a_fault(monkeypatch):
    """A sender that refuses a number for asking too often reads as the same
    `429 code_too_often` as for our own limit, and the relay keeps `502 send_failed`
    for a send that actually failed."""
    from nanomuse_cloud.senders import SendError

    app, client, sender, up, cloud, settings = make()
    monkeypatch.setattr(sender, "send", lambda ident, code: (_ for _ in ()).throw(SendError("sms", reason="too_often")))
    r = await client.post("/v1/auth/code", json={"identifier": "13800138000"})
    assert r.status_code == 429 and r.json()["error"]["code"] == "code_too_often", r.text
    assert "wait" in r.json()["error"]["message"] and "138" not in r.text
    monkeypatch.setattr(sender, "send", lambda ident, code: (_ for _ in ()).throw(SendError("sms")))
    r = await client.post("/v1/auth/code", json={"identifier": "13800138001"})
    assert r.status_code == 502 and r.json()["error"]["code"] == "send_failed", r.text


def test_code_limits_and_consumption_are_atomic_under_concurrency():
    """A barrier makes requests reach each atomic boundary together: one code occupies the
    only rate-limit slot, and one verification consumes it exactly once."""
    app, client, sender, up, cloud, settings = make(
        code_per_identifier_10m=1,
        code_per_ip_hour=100,
    )
    ident = parse("parallel@example.com")
    issue_barrier = threading.Barrier(8)

    def issue() -> str:
        issue_barrier.wait()
        try:
            cloud.request_code(ident, "198.51.100.20")
            return "ok"
        except CloudError as exc:
            return exc.code

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        issued = list(pool.map(lambda _: issue(), range(8)))
    assert issued.count("ok") == 1
    assert issued.count("code_too_often") == 7
    code = sender.sent[-1][1]

    verify_barrier = threading.Barrier(8)

    def verify() -> str:
        verify_barrier.wait()
        try:
            cloud.verify_code(ident, code, "parallel")
            return "ok"
        except CloudError as exc:
            return exc.code

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        verified = list(pool.map(lambda _: verify(), range(8)))
    assert verified.count("ok") == 1
    assert verified.count("code_expired") == 7


def test_domain_signup_limit_is_atomic_under_concurrency():
    """All workers pass the advisory pre-check before any creates; the transaction still
    permits only one account at a one-account daily domain limit."""
    app, client, sender, up, cloud, settings = make(
        signup_per_domain_day=1,
        code_per_ip_hour=100,
    )
    barrier = threading.Barrier(8)
    original = cloud._refuse_if_domain_limited

    def checked_together(ident):
        original(ident)
        barrier.wait()

    cloud._refuse_if_domain_limited = checked_together  # type: ignore[method-assign]

    def create(i: int) -> str:
        try:
            cloud.sign_in(parse(f"parallel{i}@race.example"), "worker", "code")
            return "ok"
        except CloudError as exc:
            return exc.code

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        created = list(pool.map(create, range(8)))
    assert created.count("ok") == 1
    assert created.count("domain_limited") == 7
    assert cloud.db.signups_from_domain_since("race.example", 0) == 1


async def test_blocked_disposable_domains_are_refused():
    """Domains in DEFAULT_SIGNUP_DOMAIN_BLOCK and SIGNUP_DOMAIN_BLOCK are refused for new
    sign-ups with `429 domain_limited` even when `signup_per_domain_day` is not yet reached
    or is 0; an entry in `SIGNUP_DOMAIN_ALLOW` overrides the blocklist."""
    app, client, sender, up, cloud, settings = make(
        signup_per_domain_day=0,
        signup_domain_block="throwaway.example.org",
        signup_domain_allow="allowed.maxxspace.com",
        code_per_ip_hour=1000,
    )
    for blocked in ("bot1@maxxspace.com", "bot2@duckmail.sbs", "sub@mail.duckmail.sbs", "x@throwaway.example.org"):
        r = await client.post("/v1/auth/code", json={"identifier": blocked})
        assert r.status_code == 429 and r.json()["error"]["code"] == "domain_limited", blocked
    ok = await sign_up(client, sender, "friend@allowed.maxxspace.com", "mac")
    assert ok["created"] is True
