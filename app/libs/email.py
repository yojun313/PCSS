"""메일 발송 모듈.

MAIL_BACKEND 환경변수로 발송 경로를 고른다.

- gmail   : smtp.gmail.com:587 에 앱 비밀번호로 로그인해서 보낸다.
            계정 정보는 app/data/mail_lock.json (또는 MAIL_SENDER/MAIL_PASSWORD).
- postech : smtp.postech.ac.kr:25 무인증 릴레이로 보낸다.
            POSTECH 메일 관리자에게 '이메일 무인증 발송신청'으로 등록된 IP에서만 동작한다.
            등록 IP: 141.223.145.21 (= pcss.postech.ac.kr)
            등록되지 않은 IP에서 접속하면 '421 4.3.2 Service not available'로 거절되므로,
            POSTECH 서버가 아닌 곳에 배포할 때는 MAIL_BACKEND=gmail 을 써야 한다.
"""

import json
import os
import smtplib
import socket
from email.header import Header
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

from dotenv import load_dotenv

load_dotenv()

MAIL_BACKEND = os.getenv("MAIL_BACKEND", "gmail").strip().lower()

# 표시용 발신자 이름과 회신 주소 (두 백엔드 공통)
MAIL_SENDER_NAME = os.getenv("MAIL_SENDER_NAME", "PCSS")
MAIL_REPLY_TO = os.getenv("MAIL_REPLY_TO", "").strip()

# POSTECH 무인증 릴레이 설정
POSTECH_SMTP_HOST = os.getenv("MAIL_SMTP_HOST", "smtp.postech.ac.kr")
POSTECH_SMTP_PORT = int(os.getenv("MAIL_SMTP_PORT", "25"))
POSTECH_SENDER = os.getenv("MAIL_SENDER", "pcss@postech.ac.kr")
MAIL_STARTTLS = os.getenv("MAIL_STARTTLS", "true").strip().lower() == "true"

SMTP_TIMEOUT = int(os.getenv("MAIL_TIMEOUT", "30"))

_MAIL_LOCK_PATH = os.path.join(
    os.path.dirname(__file__), "..", "data", "mail_lock.json"
)


def _gmail_config() -> dict:
    """Gmail 계정 정보를 돌려준다. 환경변수가 있으면 그쪽을 우선한다."""
    sender = os.getenv("MAIL_SENDER", "")
    password = os.getenv("MAIL_PASSWORD", "")
    if sender and password:
        return {"sender": sender, "password": password}

    with open(_MAIL_LOCK_PATH, "r", encoding="utf-8") as jsonfile:
        return json.load(jsonfile)


def sender_address() -> str:
    """현재 백엔드에서 실제로 쓰는 발신 주소."""
    if MAIL_BACKEND == "postech":
        return POSTECH_SENDER
    return _gmail_config()["sender"]


def _build_message(sender, receiver, title, text=None, html=None, file_path=None):
    msg = MIMEMultipart("mixed")
    msg["Subject"] = Header(title, "utf-8")
    msg["From"] = formataddr((str(Header(MAIL_SENDER_NAME, "utf-8")), sender))
    msg["To"] = receiver
    if MAIL_REPLY_TO:
        msg["Reply-To"] = MAIL_REPLY_TO

    if html:
        alt = MIMEMultipart("alternative")
        if text:
            alt.attach(MIMEText(text, "plain", "utf-8"))
        alt.attach(MIMEText(html, "html", "utf-8"))
        msg.attach(alt)
    else:
        msg.attach(MIMEText(text or "", "plain", "utf-8"))

    if file_path and os.path.exists(file_path):
        with open(file_path, "rb") as f:
            attachment = MIMEApplication(f.read(), Name=os.path.basename(file_path))
        attachment["Content-Disposition"] = (
            f'attachment; filename="{os.path.basename(file_path)}"'
        )
        msg.attach(attachment)

    return msg


def _send_via_gmail(receiver: str, msg) -> None:
    cfg = _gmail_config()
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=SMTP_TIMEOUT) as server:
        server.starttls()
        server.login(cfg["sender"], cfg["password"])
        server.sendmail(cfg["sender"], receiver, msg.as_string())


def _send_via_postech(sender: str, receiver: str, msg) -> None:
    # 인증 없이 보낸다. 대신 발신 IP가 학교에 등록돼 있어야 한다.
    with smtplib.SMTP(
        POSTECH_SMTP_HOST, POSTECH_SMTP_PORT, timeout=SMTP_TIMEOUT
    ) as server:
        server.ehlo()
        if MAIL_STARTTLS and server.has_extn("starttls"):
            server.starttls()
            server.ehlo()
        server.sendmail(sender, receiver, msg.as_string())


def send_mail(receiver, title, text=None, html=None, file_path=None) -> None:
    """백엔드 설정에 따라 메일을 보낸다. 실패하면 예외가 그대로 올라온다."""
    sender = sender_address()
    msg = _build_message(
        sender, receiver, title, text=text, html=html, file_path=file_path
    )

    if MAIL_BACKEND == "postech":
        _send_via_postech(sender, receiver, msg)
    elif MAIL_BACKEND == "gmail":
        _send_via_gmail(receiver, msg)
    else:
        raise ValueError(
            f"알 수 없는 MAIL_BACKEND: {MAIL_BACKEND!r} (gmail 또는 postech)"
        )


def _backend_host_port() -> tuple[str, int]:
    if MAIL_BACKEND == "postech":
        return POSTECH_SMTP_HOST, POSTECH_SMTP_PORT
    return "smtp.gmail.com", 587


def config_summary() -> dict:
    """관리자 화면에 보여줄 현재 메일 설정. 비밀번호는 절대 담지 않는다."""
    host, port = _backend_host_port()
    try:
        local_ip = socket.gethostbyname(socket.gethostname())
    except Exception:
        local_ip = "(확인 불가)"

    try:
        sender = sender_address()
    except Exception as e:
        sender = f"(확인 실패: {e})"

    return {
        "backend": MAIL_BACKEND,
        "host": host,
        "port": port,
        "sender": sender,
        "sender_name": MAIL_SENDER_NAME,
        "reply_to": MAIL_REPLY_TO or "(설정 안 함)",
        "starttls": MAIL_STARTTLS,
        "auth": "무인증 (등록된 IP만 허용)"
        if MAIL_BACKEND == "postech"
        else "앱 비밀번호 로그인",
        "hostname": socket.gethostname(),
        "local_ip": local_ip,
    }


def probe(receiver: str) -> dict:
    """실제 발송 없이 SMTP 핸드셰이크만 확인한다.

    RCPT까지만 진행하고 RSET으로 되돌리므로 수신자에게 메일이 가지 않는다.
    """
    steps = []
    try:
        sender = sender_address()
        steps.append(("발신 주소", sender))

        if MAIL_BACKEND not in ("gmail", "postech"):
            return {
                "ok": False,
                "steps": steps,
                "error": f"알 수 없는 MAIL_BACKEND: {MAIL_BACKEND!r} (gmail 또는 postech)",
            }

        host, port = _backend_host_port()
        server = smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT)
        try:
            steps.append(("SMTP 연결", f"{host}:{port} 연결 성공"))
            code, banner = server.ehlo()
            first = banner.decode(errors="replace").splitlines()[0]
            steps.append(("EHLO", f"{code} {first}"))

            if MAIL_BACKEND == "gmail":
                server.starttls()
                server.ehlo()
                steps.append(("STARTTLS", "적용됨"))
                cfg = _gmail_config()
                server.login(cfg["sender"], cfg["password"])
                steps.append(("로그인", "성공"))
            elif MAIL_STARTTLS and server.has_extn("starttls"):
                server.starttls()
                server.ehlo()
                steps.append(("STARTTLS", "적용됨"))
            else:
                steps.append(("STARTTLS", "사용하지 않음"))

            c1, m1 = server.mail(sender)
            steps.append(("MAIL FROM", f"{c1} {m1.decode(errors='replace')}"))
            c2, m2 = server.rcpt(receiver)
            steps.append(("RCPT TO", f"{c2} {m2.decode(errors='replace')}"))

            server.rset()
            steps.append(("RSET", "실제 발송 없이 되돌림"))

            ok = c1 == 250 and c2 in (250, 251)
            return {
                "ok": ok,
                "steps": steps,
                "error": None if ok else "수신자가 거부되었습니다.",
            }
        finally:
            try:
                server.quit()
            except Exception:
                pass

    except smtplib.SMTPConnectError as e:
        hint = ""
        if MAIL_BACKEND == "postech":
            hint = (
                " — 이 서버 IP가 학교에 등록돼 있지 않을 가능성이 큽니다."
                " 무인증 릴레이는 등록된 IP(141.223.145.21)에서만 동작합니다."
            )
        return {"ok": False, "steps": steps, "error": f"연결 거부: {e}{hint}"}
    except Exception as e:
        return {"ok": False, "steps": steps, "error": f"{type(e).__name__}: {e}"}


def send_email(receiver, title, text, file_path=None) -> None:
    """평문 메일 발송 (기존 호출부 호환용)."""
    send_mail(receiver, title, text=text, file_path=file_path)


def send_html_email(receiver, title, html_body, text=None) -> None:
    """HTML 메일 발송."""
    send_mail(receiver, title, text=text, html=html_body)
