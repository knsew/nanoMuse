"""How a code reaches the person: the log (development) or SMTP.

`send(identifier, code)` raises `SendError` when the message could not go out
so the API can say so instead of letting the user wait for nothing.
"""

from __future__ import annotations

import ipaddress
import logging
import smtplib
import urllib.parse
from email.message import EmailMessage
from typing import Protocol

from .config import Settings
from .identifiers import Identifier

log = logging.getLogger("nanomuse_cloud.send")


class SendError(RuntimeError):
    """A code that did not go out. `reason` is `too_often` when the carrier's own limit for
    the number refused it, so the person is told to wait rather than that the relay failed;
    it is empty for everything else."""

    def __init__(self, message: str, reason: str = "") -> None:
        super().__init__(message)
        self.reason = reason


class CodeSender(Protocol):
    def send(self, ident: Identifier, code: str) -> None: ...

    def accepts(self, ident: Identifier) -> bool:
        """Can a code reach this identifier at all? Asked before one is made, so the
        answer is a plain 400 and not a failed send."""
        ...


class LogSender:
    """Development: the code goes to the log, and stays reachable for tests."""

    def __init__(self) -> None:
        self.sent: list[tuple[Identifier, str]] = []

    def accepts(self, ident: Identifier) -> bool:
        return True

    def send(self, ident: Identifier, code: str) -> None:
        self.sent.append((ident, code))
        log.warning("verification code for %s (%s): %s", ident.hint, ident.channel, code)


class SmtpSender:
    def __init__(self, s: Settings) -> None:
        if not (s.smtp_host and s.smtp_from):
            raise ValueError("SMTP_HOST and SMTP_FROM are required for CODE_SENDER=smtp")
        self.s = s

    def accepts(self, ident: Identifier) -> bool:
        return ident.channel == "email"

    def send(self, ident: Identifier, code: str) -> None:
        if ident.channel != "email":
            raise SendError("this deployment sends codes by e-mail only")
        # a lifetime under a minute still reads as one
        msg = compose_code_mail(self.s.smtp_from, ident.value, code, max(1, self.s.code_ttl_s // 60))
        smtp_send(self.s, msg)


def smtp_send(s: Settings, msg: EmailMessage) -> None:
    """One message out through the relay's SMTP settings; SendError when it did not go."""
    if not (s.smtp_host and s.smtp_from):
        raise SendError("mail: SMTP_HOST and SMTP_FROM are not set")
    try:
        if s.smtp_port == 465:
            server = smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=20)
        else:
            server = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=20)
            server.starttls()
        with server:
            if s.smtp_user:
                server.login(s.smtp_user, s.smtp_password)
            server.send_message(msg)
    except (smtplib.SMTPException, OSError) as e:
        log.error("smtp send failed: %s", smtp_error_text(e))
        raise SendError("mail") from e


def smtp_error_text(e: BaseException) -> str:
    """What the log may say about a failed send: the error's kind and the server's code and
    words, never the recipient. ``str()`` of an SMTPRecipientsRefused is a dict keyed by the
    addresses refused, and SMTPSenderRefused carries the From address."""
    if isinstance(e, smtplib.SMTPRecipientsRefused):
        codes = sorted({str(v[0]) for v in e.recipients.values()} if isinstance(e.recipients, dict) else set())
        return f"SMTPRecipientsRefused ({len(e.recipients)} recipient(s); {', '.join(codes) or 'no code'})"
    if isinstance(e, smtplib.SMTPResponseException):
        error = e.smtp_error.decode("utf-8", "replace") if isinstance(e.smtp_error, bytes) else str(e.smtp_error)
        return f"{type(e).__name__} {e.smtp_code} {error[:120]}"
    return f"{type(e).__name__}: {str(e)[:160]}"


def compose_notice_mail(sender: str, to: str, subject: str, lines: list[str]) -> EmailMessage:
    """A plain note to the operator (0.22: a threshold rule fired). Text only, both
    languages' worth of facts in the lines the caller gives; nothing personal in it."""
    msg = EmailMessage()
    msg["From"] = sender if "<" in sender else f"nanoMuse <{sender}>"
    msg["To"] = to
    msg["Subject"] = f"nanoMuse Cloud · {subject}"
    msg["Auto-Submitted"] = "auto-generated"
    msg["X-Auto-Response-Suppress"] = "All"
    msg.set_content("\n".join(lines) + "\n\nnanoMuse Cloud · automated, no reply / 自动发送，请勿回复\n")
    return msg


def compose_code_mail(sender: str, to: str, code: str, minutes: int) -> EmailMessage:
    """The verification mail: plain text first (what every client can show),
    an HTML part on top with the code large enough to read off a phone.
    Chinese and English in one message — the relay does not know the reader's
    language, and a code mail should not need a translation either way."""
    msg = EmailMessage()
    msg["From"] = sender if "<" in sender else f"nanoMuse <{sender}>"
    msg["To"] = to
    msg["Subject"] = f"nanoMuse 验证码 {code} · Your nanoMuse code"
    msg["Auto-Submitted"] = "auto-generated"
    msg["X-Auto-Response-Suppress"] = "All"
    msg.set_content(
        f"你的 nanoMuse 验证码是 {code}，{minutes} 分钟内有效。\n"
        "在 nanoMuse App 里填入即可登录；不要把它告诉任何人。\n\n"
        f"Your nanoMuse code is {code}; it expires in {minutes} minutes.\n"
        "Enter it in the nanoMuse app to sign in. Do not share it with anyone.\n\n"
        "如果这不是你本人的操作，忽略这封邮件即可。\n"
        "If you did not ask for this, you can ignore this message.\n\n"
        "nanoMuse · https://github.com/knsew/nanoMuse · 自动发送，请勿回复 / automated, no reply\n"
    )
    spaced = " ".join(code)
    html = f"""<!doctype html>
<html><body style="margin:0;padding:24px;background:#f5f5f7;font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Helvetica Neue',Arial,sans-serif;color:#1d1d1f">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">
<table role="presentation" width="420" cellpadding="0" cellspacing="0" style="max-width:420px;background:#ffffff;border-radius:16px;padding:32px 28px">
<tr><td style="font-size:15px;font-weight:600;color:#6e6e73;padding-bottom:18px">nanoMuse</td></tr>
<tr><td style="font-size:16px;line-height:24px">你的验证码 / Your code</td></tr>
<tr><td style="font-size:40px;font-weight:700;letter-spacing:6px;padding:14px 0 18px;font-variant-numeric:tabular-nums">{spaced}</td></tr>
<tr><td style="font-size:14px;line-height:22px;color:#3a3a3c">{minutes} 分钟内有效。在 nanoMuse App 里填入即可登录；不要把它告诉任何人。</td></tr>
<tr><td style="font-size:14px;line-height:22px;color:#3a3a3c;padding-top:8px">It expires in {minutes} minutes. Enter it in the nanoMuse app to sign in; do not share it with anyone.</td></tr>
<tr><td style="font-size:12px;line-height:18px;color:#8e8e93;padding-top:22px">如果这不是你本人的操作，忽略这封邮件即可。<br>If you did not ask for this, you can ignore this message.</td></tr>
</table>
<div style="font-size:12px;color:#8e8e93;padding-top:16px">nanoMuse · <a href="https://github.com/knsew/nanoMuse" style="color:#8e8e93">github.com/knsew/nanoMuse</a> · 自动发送，请勿回复 / automated, no reply</div>
</td></tr></table>
</body></html>
"""
    msg.add_alternative(html, subtype="html")
    return msg


def is_private_base(base: str) -> bool:
    """A relay nobody reaches from the internet: localhost, a loopback, LAN, link-local or
    Tailscale address (self-host.sh --local writes those). Its log is read by the person who
    runs it, so codes in the log are what they asked for."""
    host = urllib.parse.urlsplit(base).hostname or ""
    if host == "localhost":
        return True
    try:
        return not ipaddress.ip_address(host).is_global
    except ValueError:
        return False


def make_sender(s: Settings) -> CodeSender:
    if s.sender == "log":
        if s.signup_open and not is_private_base(s.public_base):
            # a relay with a public address and open sign-up that writes the codes to its log:
            # nobody receives a code, and whoever reads the log can sign in as anyone — say so at startup
            log.error(
                "CODE_SENDER=log with SIGNUP_OPEN at %s: verification codes go to this log and nobody receives them. Set CODE_SENDER=smtp.",
                s.public_base,
            )
        return LogSender()
    if s.sender == "smtp":
        return SmtpSender(s)
    raise ValueError(f"unknown CODE_SENDER {s.sender!r}")
