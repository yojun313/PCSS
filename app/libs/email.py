import pandas as pd
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
import smtplib
import json


def send_email(receiver, title, text, file_path=None):
    mail_json = os.path.join(os.path.dirname(__file__), "..", "data", "mail_lock.json")

    # JSON 파일에서 메일 계정 정보 읽기
    with open(mail_json, "r", encoding="utf-8") as jsonfile:
        mail_data = json.load(jsonfile)

    sender = mail_data["sender"]
    MailPassword = mail_data["password"]

    # 이메일 메시지 생성
    msg = MIMEMultipart()
    msg["Subject"] = title
    msg["From"] = sender
    msg["To"] = receiver

    # 이메일 본문 추가
    msg.attach(MIMEText(text, "plain"))

    # 파일 첨부 (file_path가 제공된 경우)
    if file_path and os.path.exists(file_path):
        with open(file_path, "rb") as f:
            file_attachment = MIMEApplication(
                f.read(), Name=os.path.basename(file_path)
            )
            file_attachment["Content-Disposition"] = (
                f'attachment; filename="{os.path.basename(file_path)}"'
            )
            msg.attach(file_attachment)

    smtp_server = "smtp.gmail.com"
    smtp_port = 587

    # SMTP 연결 및 메일 보내기
    with smtplib.SMTP(smtp_server, smtp_port) as server:
        server.starttls()
        server.login(sender, MailPassword)
        server.sendmail(sender, receiver, msg.as_string())
