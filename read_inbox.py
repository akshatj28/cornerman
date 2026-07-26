"""Read workout emails from Cornerman's mailbox."""
from __future__ import annotations

import email
import imaplib
import sys
from email.header import decode_header, make_header

import secrets_cm as creds
from parse_hevy_email import parse_workout

IMAP_HOST = "imap.gmail.com"


def _subject(msg) -> str:
    raw = msg.get("Subject", "")
    try:
        return str(make_header(decode_header(raw)))
    except Exception:
        return raw


def _plain_body(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
        return ""
    payload = msg.get_payload(decode=True)
    if payload:
        charset = msg.get_content_charset() or "utf-8"
        return payload.decode(charset, errors="replace")
    return str(msg.get_payload())


def fetch(search: str = "UNSEEN", peek: bool = True):
    conn = imaplib.IMAP4_SSL(IMAP_HOST)
    conn.login(creds.GMAIL_USER, creds.GMAIL_APP_PASSWORD)
    conn.select("INBOX")

    typ, data = conn.search(None, search)
    ids = data[0].split() if data and data[0] else []
    print(f"Found {len(ids)} message(s) matching {search}\n")

    fetch_cmd = "(BODY.PEEK[])" if peek else "(RFC822)"

    results = []
    for mid in ids:
        typ, raw = conn.fetch(mid, fetch_cmd)
        if not raw or not raw[0]:
            continue
        msg = email.message_from_bytes(raw[0][1])
        subj = _subject(msg)
        sender = msg.get("From", "")
        body = _plain_body(msg)

        parsed = parse_workout(body)
        if parsed:
            n_sets = sum(len(e["sets"]) for e in parsed["exercises"])
            print(f"  [WORKOUT] {parsed['title']} - {parsed['start_time']}")
            print(f"            {len(parsed['exercises'])} exercises, {n_sets} sets")
            results.append({"id": mid.decode(), "subject": subj,
                            "from": sender, "workout": parsed})
        else:
            preview = body.strip().replace("\n", " ")[:60]
            print(f"  [other]   {subj!r} from {sender[:40]}")

    conn.close()
    conn.logout()
    return results


if __name__ == "__main__":
    search = "ALL" if "--all" in sys.argv else "UNSEEN"
    found = fetch(search=search)
    print(f"\nParsed {len(found)} workout(s).")



def fetch_raw(search="UNSEEN", peek=False):
    conn = imaplib.IMAP4_SSL(IMAP_HOST)
    conn.login(creds.GMAIL_USER, creds.GMAIL_APP_PASSWORD)
    conn.select("INBOX")
    typ, data = conn.search(None, search)
    ids = data[0].split() if data and data[0] else []
    cmd = "(BODY.PEEK[])" if peek else "(RFC822)"
    out = []
    for mid in ids:
        typ, raw = conn.fetch(mid, cmd)
        if not raw or not raw[0]:
            continue
        msg = email.message_from_bytes(raw[0][1])
        body = _plain_body(msg)
        out.append({"id": mid.decode(), "subject": _subject(msg),
                    "from": msg.get("From", ""), "body": body,
                    "rfc_id": msg.get("Message-ID"),
                    "workout": parse_workout(body)})
    conn.close()
    conn.logout()
    return out


def mark_read(uid):
    conn = imaplib.IMAP4_SSL(IMAP_HOST)
    conn.login(creds.GMAIL_USER, creds.GMAIL_APP_PASSWORD)
    conn.select("INBOX")
    conn.store(uid, "+FLAGS", "\\Seen")
    conn.close()
    conn.logout()
    return uid
