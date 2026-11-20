"""Invio del notiziario: Telegram, email e salvataggio su file."""
from __future__ import annotations

import html
import os
import re
import smtplib
from datetime import date
from email.mime.text import MIMEText
from pathlib import Path

import requests

TELEGRAM_LIMIT = 4000  # Telegram accetta al massimo 4096 caratteri per messaggio


def split_message(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    """Divide un testo lungo in pezzi, tagliando sugli a capo."""
    chunks, current = [], ""
    for line in text.splitlines(keepends=True):
        if len(current) + len(line) > limit and current:
            chunks.append(current)
            current = ""
        while len(line) > limit:
            chunks.append(line[:limit])
            line = line[limit:]
        current += line
    if current:
        chunks.append(current)
    return chunks


def markdown_to_telegram(text: str) -> str:
    """Converte il Markdown del notiziario nell'HTML di Telegram: grassetti veri e link cliccabili."""
    out = []
    for line in text.splitlines():
        line = html.escape(line.rstrip(), quote=False)
        line = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)",
                      lambda m: f'<a href="{html.escape(html.unescape(m.group(2)))}">{m.group(1)}</a>', line)
        line = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", line)
        if m := re.match(r"#{1,6}\s+(.*)", line):
            line = ("\n" if out else "") + f"<b>{m.group(1)}</b>"
        elif m := re.match(r"\s*[-*]\s+(.*)", line):
            line = "• " + m.group(1)
        out.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def send_telegram(text: str) -> bool:
    token, chat_id = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not (token and chat_id):
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for chunk in split_message(markdown_to_telegram(text)):
        r = requests.post(url, json={"chat_id": chat_id, "text": chunk, "parse_mode": "HTML",
                                     "disable_web_page_preview": True}, timeout=20)
        if r.status_code == 400:  # HTML non valido: meglio un messaggio semplice che nessun messaggio
            r = requests.post(url, json={"chat_id": chat_id, "text": re.sub(r"<[^>]+>", "", html.unescape(chunk)),
                                         "disable_web_page_preview": True}, timeout=20)
        r.raise_for_status()
    return True


def send_email(text: str) -> bool:
    user, password, to = os.getenv("EMAIL_USER"), os.getenv("EMAIL_PASSWORD"), os.getenv("EMAIL_TO")
    if not (user and password and to):
        return False
    msg = MIMEText(text, "plain", "utf-8")
    msg["Subject"] = f"🗞️ Notiziario del {date.today():%d/%m/%Y}"
    msg["From"], msg["To"] = user, to
    host = os.getenv("EMAIL_SMTP", "smtp.gmail.com")
    with smtplib.SMTP_SSL(host, 465) as s:
        s.login(user, password)
        s.send_message(msg)
    return True


def save_markdown(text: str, folder: str = "digests") -> Path:
    path = Path(folder) / f"{date.today():%Y-%m-%d}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path
