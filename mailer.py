"""Send mail from the coach's mailbox."""
import smtplib
from email.message import EmailMessage
from email.header import Header
import secrets_cm as creds

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465


def send(subject, body, to=None):
    to = to or creds.MY_EMAIL
    msg = EmailMessage()
    name = getattr(creds, "FROM_NAME", "Cornerman")
    msg["From"] = str(Header(name, "utf-8")) + " <" + creds.GMAIL_USER + ">"
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    s = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30)
    s.login(creds.GMAIL_USER, creds.GMAIL_APP_PASSWORD)
    s.send_message(msg)
    s.quit()
    return to


if __name__ == "__main__":
    where = send("Cornerman test", "If you are reading this, the coach can reach you.")
    print("Sent to " + where)


def send_threaded(subject, body, to=None):
    import convo
    from email.utils import make_msgid
    to = to or creds.MY_EMAIL
    msg = EmailMessage()
    mid = make_msgid(domain="cornerman.mail")
    msg["Message-ID"] = mid
    last = convo.last_msg_id()
    root = convo.root_msg_id()
    if last:
        msg["In-Reply-To"] = last
        refs = []
        if root and root != last:
            refs.append(root)
        refs.append(last)
        msg["References"] = " ".join(refs)
    name = getattr(creds, "FROM_NAME", "Cornerman")
    msg["From"] = str(Header(name, "utf-8")) + " <" + creds.GMAIL_USER + ">"
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    s = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30)
    s.login(creds.GMAIL_USER, creds.GMAIL_APP_PASSWORD)
    s.send_message(msg)
    s.quit()
    return mid
