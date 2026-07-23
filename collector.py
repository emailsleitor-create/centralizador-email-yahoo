#!/usr/bin/env python3
"""Copia e-mails novos de várias contas Yahoo para um Gmail central."""

from __future__ import annotations

import html
import imaplib
import json
import logging
import os
import re
import smtplib
import ssl
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email import policy
from email.header import decode_header, make_header
from email.message import EmailMessage, Message
from email.parser import BytesParser
from pathlib import Path
from typing import Any


IMAP_HOST = "imap.mail.yahoo.com"
IMAP_PORT = 993
GMAIL_SMTP_HOST = "smtp.gmail.com"
GMAIL_SMTP_PORT = 465
STATE_PATH = Path(os.getenv("STATE_PATH", "state/state.json"))
INITIAL_LOOKBACK_DAYS = int(os.getenv("INITIAL_LOOKBACK_DAYS", "2"))
MAX_MESSAGES_PER_ACCOUNT = int(os.getenv("MAX_MESSAGES_PER_ACCOUNT", "100"))

LOG = logging.getLogger("coletor")


@dataclass(frozen=True)
class YahooAccount:
    email: str
    app_password: str
    label: str
    position: int = 1

    @property
    def state_key(self) -> str:
        return f"account_{self.position:03d}"


def decode_text(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except (LookupError, UnicodeDecodeError):
        return value


def strip_html(value: str) -> str:
    value = re.sub(r"(?is)<(script|style).*?>.*?</\1>", "", value)
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</p\s*>", "\n\n", value)
    value = re.sub(r"(?s)<[^>]+>", "", value)
    return html.unescape(value).strip()


def extract_body(message: Message) -> str:
    plain_parts: list[str] = []
    html_parts: list[str] = []

    for part in message.walk():
        if part.is_multipart():
            continue
        if part.get_content_disposition() == "attachment":
            continue
        content_type = part.get_content_type()
        if content_type not in {"text/plain", "text/html"}:
            continue
        try:
            content = part.get_content()
        except (LookupError, UnicodeDecodeError):
            payload = part.get_payload(decode=True) or b""
            content = payload.decode("utf-8", errors="replace")
        if content_type == "text/plain":
            plain_parts.append(str(content))
        else:
            html_parts.append(strip_html(str(content)))

    body = "\n\n".join(item.strip() for item in plain_parts if item.strip())
    if not body:
        body = "\n\n".join(item for item in html_parts if item)
    return body or "(Mensagem sem conteúdo de texto.)"


def safe_filename(name: str | None, index: int) -> str:
    candidate = decode_text(name).strip() if name else f"anexo-{index}"
    candidate = re.sub(r"[\\/\x00-\x1f]+", "_", candidate)
    return candidate[:180] or f"anexo-{index}"


def build_forward(
    original: Message,
    account: YahooAccount,
    central_gmail: str,
) -> EmailMessage:
    sender = decode_text(original.get("From")) or "(remetente não informado)"
    recipient = decode_text(original.get("To")) or account.email
    date = decode_text(original.get("Date")) or "(data não informada)"
    subject = decode_text(original.get("Subject")) or "(sem assunto)"
    message_id = decode_text(original.get("Message-ID"))

    forwarded = EmailMessage()
    forwarded["From"] = central_gmail
    forwarded["To"] = central_gmail
    forwarded["Reply-To"] = sender
    forwarded["Subject"] = f"[Yahoo: {account.label}] {subject}"
    forwarded["X-Yahoo-Source"] = account.email
    forwarded["X-Original-From"] = sender
    forwarded["X-Original-To"] = recipient
    if message_id:
        forwarded["X-Original-Message-ID"] = message_id

    body = extract_body(original)
    forwarded.set_content(
        "\n".join(
            [
                "MENSAGEM RECEBIDA NO YAHOO",
                "",
                f"Conta de origem: {account.label} <{account.email}>",
                f"Remetente original: {sender}",
                f"Destinatário original: {recipient}",
                f"Data original: {date}",
                f"Assunto original: {subject}",
                "",
                "CONTEÚDO",
                "--------",
                body,
            ]
        )
    )

    attachment_index = 0
    for part in original.walk():
        if part.is_multipart():
            continue
        disposition = part.get_content_disposition()
        filename = part.get_filename()
        if disposition != "attachment" and not filename:
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        attachment_index += 1
        maintype, subtype = part.get_content_type().split("/", 1)
        forwarded.add_attachment(
            payload,
            maintype=maintype,
            subtype=subtype,
            filename=safe_filename(filename, attachment_index),
        )

    return forwarded


def load_accounts() -> list[YahooAccount]:
    raw = os.getenv("YAHOO_ACCOUNTS_JSON", "").strip()
    if not raw:
        raise ValueError("O segredo YAHOO_ACCOUNTS_JSON não foi configurado.")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("YAHOO_ACCOUNTS_JSON não contém um JSON válido.") from exc
    if not isinstance(data, list) or not data:
        raise ValueError("YAHOO_ACCOUNTS_JSON precisa ser uma lista não vazia.")

    accounts: list[YahooAccount] = []
    seen: set[str] = set()
    for index, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Conta {index}: configuração inválida.")
        email_address = str(item.get("email", "")).strip().lower()
        app_password = str(item.get("app_password", "")).replace(" ", "").strip()
        label = str(item.get("label", "")).strip() or f"Conta {index}"
        if "@" not in email_address or not app_password:
            raise ValueError(f"Conta {index}: informe email e app_password.")
        if email_address in seen:
            raise ValueError(f"Conta repetida: {email_address}")
        seen.add(email_address)
        accounts.append(YahooAccount(email_address, app_password, label, index))
    return accounts


def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {"version": 1, "accounts": {}}
    with STATE_PATH.open("r", encoding="utf-8") as handle:
        state = json.load(handle)
    if not isinstance(state, dict):
        raise ValueError("O arquivo de estado está inválido.")
    state.setdefault("version", 1)
    state.setdefault("accounts", {})
    return state


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE_PATH.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    temporary.replace(STATE_PATH)


def search_uids(imap: imaplib.IMAP4_SSL, last_uid: int | None) -> list[int]:
    if last_uid is not None:
        criterion = f"UID {last_uid + 1}:*"
        status, result = imap.uid("search", None, criterion)
    else:
        since = (datetime.now(timezone.utc) - timedelta(days=INITIAL_LOOKBACK_DAYS))
        status, result = imap.uid("search", None, "SINCE", since.strftime("%d-%b-%Y"))
    if status != "OK":
        raise RuntimeError("O Yahoo não conseguiu pesquisar as mensagens.")
    raw_uids = result[0].split() if result and result[0] else []
    uids = [int(value) for value in raw_uids]
    return uids[:MAX_MESSAGES_PER_ACCOUNT]


def fetch_message(imap: imaplib.IMAP4_SSL, uid: int) -> bytes:
    status, result = imap.uid("fetch", str(uid), "(BODY.PEEK[])")
    if status != "OK" or not result:
        raise RuntimeError(f"Não foi possível baixar a mensagem UID {uid}.")
    for item in result:
        if isinstance(item, tuple) and isinstance(item[1], bytes):
            return item[1]
    raise RuntimeError(f"Conteúdo ausente na mensagem UID {uid}.")


def process_account(
    account: YahooAccount,
    central_gmail: str,
    state: dict[str, Any],
    smtp: smtplib.SMTP_SSL,
) -> tuple[int, int]:
    account_state = state["accounts"].setdefault(account.state_key, {})
    last_uid_raw = account_state.get("last_uid")
    last_uid = int(last_uid_raw) if last_uid_raw is not None else None
    copied = 0
    errors = 0

    context = ssl.create_default_context()
    with imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, ssl_context=context) as imap:
        imap.login(account.email, account.app_password)
        status, _ = imap.select("INBOX", readonly=True)
        if status != "OK":
            raise RuntimeError("Não foi possível abrir a caixa de entrada.")
        uids = search_uids(imap, last_uid)
        LOG.info("%s: %d mensagem(ns) nova(s).", account.state_key, len(uids))

        if not uids:
            return copied, errors

        for uid in uids:
            try:
                raw_message = fetch_message(imap, uid)
                original = BytesParser(policy=policy.default).parsebytes(raw_message)
                forwarded = build_forward(original, account, central_gmail)
                smtp.send_message(
                    forwarded,
                    from_addr=central_gmail,
                    to_addrs=[central_gmail],
                )
            except Exception:
                errors += 1
                LOG.exception(
                    "%s: falha ao copiar a mensagem UID %d.",
                    account.state_key,
                    uid,
                )
                break
            else:
                copied += 1
                account_state["last_uid"] = uid
                account_state["updated_at"] = datetime.now(timezone.utc).isoformat()
    return copied, errors


def main() -> int:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    central_gmail = os.getenv("CENTRAL_GMAIL", "").strip().lower()
    if "@" not in central_gmail:
        LOG.error("O segredo CENTRAL_GMAIL não foi configurado corretamente.")
        return 2
    gmail_app_password = (
        os.getenv("GMAIL_APP_PASSWORD", "").replace(" ", "").strip()
    )
    if not gmail_app_password:
        LOG.error("O segredo GMAIL_APP_PASSWORD não foi configurado.")
        return 2

    try:
        accounts = load_accounts()
        state = load_state()
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        LOG.error("%s", exc)
        return 2

    total_copied = 0
    total_errors = 0
    context = ssl.create_default_context()
    try:
        with smtplib.SMTP_SSL(
            GMAIL_SMTP_HOST,
            GMAIL_SMTP_PORT,
            context=context,
            timeout=60,
        ) as smtp:
            smtp.login(central_gmail, gmail_app_password)
            for account in accounts:
                try:
                    copied, errors = process_account(
                        account,
                        central_gmail,
                        state,
                        smtp,
                    )
                except (
                    imaplib.IMAP4.error,
                    smtplib.SMTPException,
                    OSError,
                    RuntimeError,
                ):
                    total_errors += 1
                    LOG.exception("Falha ao processar %s.", account.state_key)
                else:
                    total_copied += copied
                    total_errors += errors
                finally:
                    save_state(state)
    except (smtplib.SMTPException, OSError):
        LOG.exception("Falha ao conectar ao Gmail para enviar as mensagens.")
        return 1

    LOG.info(
        "Finalizado: %d mensagem(ns) copiada(s); %d erro(s).",
        total_copied,
        total_errors,
    )
    return 1 if total_errors else 0


if __name__ == "__main__":
    sys.exit(main())
