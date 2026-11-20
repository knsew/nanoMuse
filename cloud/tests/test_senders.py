"""The code senders: SMTP mail and the log. (The Aliyun SMS sender was removed:
this deployment sends codes by e-mail only.)"""

from __future__ import annotations

import pytest

from nanomuse_cloud import senders
from nanomuse_cloud.config import Settings
from nanomuse_cloud.identifiers import parse
from nanomuse_cloud.senders import LogSender, SendError, SmtpSender, make_sender

# the tests call the models as one of the apps would; `apps_only` (0.28) refuses other software
APP_UA = {"User-Agent": "nanoMuse/0.0 (tests)"}


def mail_settings(**overrides) -> Settings:
    kw = dict(
        database=":memory:",
        secret="s",
        sender="smtp",
        smtp_host="smtp.test",
        smtp_from="nanoMuse <no-reply@test>",
        code_ttl_s=600,
    )
    kw.update(overrides)
    return Settings(**kw)


def test_codes_in_the_log_are_an_error_on_a_public_relay_only(caplog):
    """CODE_SENDER=log with open sign-up is a mistake on a relay the internet reaches, and the
    expected setup on a machine of one's own: localhost, a LAN address (self-host.sh --local
    --bind 0.0.0.0 writes that), a Tailscale address. Only the first is an error at start."""
    public = (
        "https://cloud.example.com",
        "http://11.22.33.44:8787",
        "http://[2400:1::1]:8787",
    )
    private = (
        "http://127.0.0.1:8787",
        "http://localhost:8787",
        "http://192.168.1.20:8787",
        "http://10.0.0.5:8787",
        "http://172.16.3.4:8787",
        "http://100.101.102.103:8787",
        "http://[fd7a:115c:a1e0::1]:8787",
    )
    for base in public:
        caplog.clear()
        make_sender(mail_settings(sender="log", public_base=base, signup_open=True))
        assert [r.levelname for r in caplog.records] == ["ERROR"], base
    for base in private:
        caplog.clear()
        make_sender(mail_settings(sender="log", public_base=base, signup_open=True))
        assert caplog.records == [], base
    caplog.clear()
    make_sender(mail_settings(sender="log", public_base=public[0], signup_open=False))
    assert caplog.records == []


def test_the_code_mail_states_the_lifetime_in_whole_minutes(monkeypatch):
    """The mail says how long the code is good for in minutes: CODE_TTL_S=600 reads as ten,
    and a lifetime under a minute still reads as one, never as zero, in both languages."""
    sent = []
    monkeypatch.setattr(senders, "smtp_send", lambda s, msg: sent.append(msg))
    mail = SmtpSender(mail_settings())
    mail.send(parse("dev-a@example.com"), "482913")
    body = sent[-1].get_body(("plain",)).get_content()
    assert "482913" in body and "10 分钟内有效" in body and "expires in 10 minutes" in body
    short = SmtpSender(mail_settings(code_ttl_s=30))
    short.send(parse("dev-a@example.com"), "482913")
    body = sent[-1].get_body(("plain",)).get_content()
    assert "1 分钟内有效" in body and "expires in 1 minutes" in body and "0 分钟" not in body


def test_unknown_sender_and_missing_settings_are_refused_at_startup():
    with pytest.raises(ValueError):
        make_sender(mail_settings(sender="carrier-pigeon"))
    with pytest.raises(ValueError):
        make_sender(mail_settings(sender="aliyun"))  # the SMS sender was removed
    with pytest.raises(ValueError):
        make_sender(mail_settings(sender="both"))
    with pytest.raises(ValueError):
        SmtpSender(mail_settings(smtp_host=""))
    with pytest.raises(ValueError):
        SmtpSender(mail_settings(smtp_from=""))


def test_senders_say_up_front_whom_they_can_reach():
    mail = SmtpSender(mail_settings())
    assert mail.accepts(parse("dev-a@example.com")) and not mail.accepts(parse("13800138000"))
    assert not mail.accepts(parse("+14155550100"))
    log = LogSender()
    assert log.accepts(parse("13800138000")) and log.accepts(parse("dev-a@example.com"))
    with pytest.raises(SendError):  # mail never sends to a phone
        mail.send(parse("13800138000"), "000000")


def test_a_number_the_sender_cannot_reach_is_a_400_before_any_code_exists(monkeypatch):
    """A phone number is refused right away, not a code that never arrives, not a
    502, and nothing is counted against it: an overseas number gets `phone_region`,
    a mainland one `channel_unsupported`, since mail is the only channel now."""
    import asyncio

    import httpx

    from nanomuse_cloud.api import create_app
    from nanomuse_cloud.db import Database
    from nanomuse_cloud.service import Cloud

    s = mail_settings(public_base="http://cloud.test")
    monkeypatch.setattr(senders, "smtp_send", lambda s_, msg: None)
    cloud = Cloud(s, Database(":memory:"), make_sender(s))
    app = create_app(s, cloud)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://cloud.test", headers=APP_UA) as c:
            r = await c.post("/v1/auth/code", json={"identifier": "+1 415 555 0100"})
            assert r.status_code == 400 and r.json()["error"]["code"] == "phone_region"
            assert "e-mail" in r.json()["error"]["message"]
            r = await c.post("/v1/auth/code", json={"identifier": "+852 1234 5678"})
            assert r.status_code == 400 and r.json()["error"]["code"] == "phone_region"
            r = await c.post("/v1/auth/code", json={"identifier": "138 0013 8000"})
            assert r.status_code == 400 and r.json()["error"]["code"] == "channel_unsupported"
            r = await c.post("/v1/auth/code", json={"identifier": "dev-a@example.com"})
            assert r.status_code == 204

    asyncio.run(run())
    assert cloud.db.codes_recent_for(parse("+14155550100").hash(s.hmac_key), 0) == 0


def test_smtp_failures_are_logged_without_the_address():
    import smtplib

    refused = smtplib.SMTPRecipientsRefused({"someone@example.com": (550, b"no such user")})
    text = senders.smtp_error_text(refused)
    assert "someone@example.com" not in text and "550" in text and "1 recipient" in text
    sender_refused = smtplib.SMTPSenderRefused(553, b"relay denied", "relay@example.com")
    text = senders.smtp_error_text(sender_refused)
    assert "relay@example.com" not in text and "553" in text and "relay denied" in text
    assert senders.smtp_error_text(OSError("connection refused")).startswith("OSError: connection refused")
