import imapclient
import pyzmail
import logging
from dotenv import load_dotenv
import os
import subprocess
import time
import smtplib
import re
import socket
from email.message import EmailMessage

# ---------------- CONFIG ----------------

load_dotenv()

EMAIL_ACCOUNT = os.getenv("EMAIL_ACCOUNT")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL")
ADMIN_EMAIL_NORMALIZED = (ADMIN_EMAIL or "").strip().lower()

IMAP_SERVER = os.getenv("IMAP_SERVER", "imap.gmail.com")
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))

IMAP_FOLDER = os.getenv("IMAP_FOLDER", "INBOX")

PRINTER_NAME = os.getenv("PRINTER_NAME", "Brother_DCP-J1310DW")
TMP_DIR = os.getenv("TMP_DIR", "/tmp/mailtoprint")
WHITELIST_FILE = os.getenv("WHITELIST_FILE", "whitelist.txt")

DEFAULT_WHITELIST = [ADMIN_EMAIL_NORMALIZED] if ADMIN_EMAIL_NORMALIZED else []


def _parse_whitelist(raw_whitelist):
    if not raw_whitelist:
        return DEFAULT_WHITELIST
    tokens = re.split(r"[,;\n]+", raw_whitelist)
    return [mail.strip().lower() for mail in tokens if mail.strip()]


def _load_whitelist_from_file(file_path):
    if not file_path or not os.path.exists(file_path):
        return []

    entries = []
    with open(file_path, "r", encoding="utf-8") as file:
        for line in file:
            clean_line = line.split("#", 1)[0].strip().lower()
            if not clean_line:
                continue
            entries.extend(_parse_whitelist(clean_line))

    return entries


def _load_whitelist():
    raw_whitelist = os.getenv("WHITELIST")

    if raw_whitelist:
        whitelist = _parse_whitelist(raw_whitelist)
        source = "env"
    else:
        whitelist = _load_whitelist_from_file(WHITELIST_FILE)
        source = f"file:{WHITELIST_FILE}" if whitelist else "default"

    # Admin immer zulassen, auch wenn er in Datei/Env vergessen wurde
    if ADMIN_EMAIL_NORMALIZED and ADMIN_EMAIL_NORMALIZED not in whitelist:
        whitelist.append(ADMIN_EMAIL_NORMALIZED)

    # Doppelte Einträge entfernen, Reihenfolge beibehalten
    unique_whitelist = []
    for email in whitelist:
        if email not in unique_whitelist:
            unique_whitelist.append(email)

    return unique_whitelist, source


WHITELIST, WHITELIST_SOURCE = _load_whitelist()

LOG_DIR = os.getenv("LOG_DIR", "/home/pi/mailtoprint/logs")
MAX_QUANTITY = int(os.getenv("MAX_QUANTITY", "10"))

# ----------------------------------------

os.makedirs(TMP_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    filename=os.path.join(LOG_DIR, "mailtoprint.log"),
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

logging.info(f"Whitelist geladen aus: {WHITELIST_SOURCE} ({len(WHITELIST)} Eintraege)")

# ---------------------------------------------------
# Hilfe
# ---------------------------------------------------

HELP_TEXT = """
Print at Home - Hilfe

Parameter im Mailtext:

color=true
    Druck in Farbe

color=false
    Schwarzweißdruck

duplex=true
    Beidseitiger Druck

duplex=false
    Einseitiger Druck

pages=1-3,5,7
    Druck nur bestimmter Seiten (z.B. 1-3,5,7)

quantity=NUMBER
    Anzahl Kopien (maximal 10)

feedback=true
    Sender bekommt Rückmeldung

feedback=false
    Sender bekommt keine Rückmeldung

gethelp
    sendet diese Hilfe zurück
"""

# ---------------------------------------------------
# Mail senden
# ---------------------------------------------------


def send_mail(subject, body, recipient, bcc_admin=True):

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = EMAIL_ACCOUNT
    msg["To"] = recipient

    if bcc_admin and ADMIN_EMAIL:
        msg["Bcc"] = ADMIN_EMAIL

    msg.set_content(body)

    with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT) as smtp:
        smtp.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
        smtp.send_message(msg)

    logging.info(
        f"Mail gesendet an {recipient} (BCC: {ADMIN_EMAIL if bcc_admin else 'Nein'}), Betreff: {subject})"
    )


# ---------------------------------------------------
# Flags parsen
# ---------------------------------------------------


def parse_flags(text):

    flags = {
        "color": True,
        "duplex": True,
        "pages": "all",
        "quantity": 1,
        "feedback": True,
    }

    text = text.lower()

    if "color=false" in text:
        flags["color"] = False

    if "duplex=false" in text:
        flags["duplex"] = False

    if "pages=" in text:
        p_match = re.search(r"pages=(\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*)", text)
        if p_match:
            flags["pages"] = p_match.group(1)

    q_match = re.search(r"quantity=(\d+)", text)
    if q_match:
        q = int(q_match.group(1))
        flags["quantity"] = min(q, MAX_QUANTITY)

    if "feedback=false" in text:
        flags["feedback"] = False

    return flags


# ---------------------------------------------------
# Drucken
# ---------------------------------------------------


def print_pdf(file_path, flags):

    cmd = ["lp", "-d", PRINTER_NAME]

    if not flags["color"]:
        cmd += ["-o", "ColorModel=Gray"]

    if flags["duplex"]:
        cmd += ["-o", "sides=two-sided-long-edge"]

    if flags["pages"] != "all":
        cmd += ["-P", flags["pages"]]

    cmd += ["-n", str(flags["quantity"])]

    cmd.append(file_path)

    logging.info(f"Starte Druck: {' '.join(cmd)}")

    result = subprocess.run(cmd, capture_output=True)

    if result.returncode != 0:
        logging.error(f"Druckfehler: {result.stderr.decode()}")
    else:
        logging.info("Druck erfolgreich")

    return result.returncode == 0


# ---------------------------------------------------
# Mail verarbeiten
# ---------------------------------------------------


def process_mail_with_retry(max_retries=3, max_wait=30):
    """
    Versucht process_mail() mehrmals mit exponentiellem Backoff.
    Bei DNS-Fehlern wird aggressiver retry't.
    """
    retry_count = 0
    base_wait = 2

    while retry_count < max_retries:
        try:
            process_mail()
            return True

        except (socket.gaierror, OSError) as e:
            # DNS oder Netzwerkfehler
            if "Name resolution" in str(e) or "gaierror" in str(type(e).__name__):
                retry_count += 1
                if retry_count < max_retries:
                    wait_time = min(base_wait**retry_count, max_wait)
                    logging.warning(
                        f"DNS/Netzwerkfehler (Versuch {retry_count}/{max_retries}): {e}. "
                        f"Warte {wait_time}s bevor Retry..."
                    )
                    time.sleep(wait_time)
                else:
                    logging.error(
                        f"DNS/Netzwerkfehler nach {max_retries} Versuchen. Gebe auf."
                    )
                    raise
            else:
                # Anderer Fehler - nicht retry'en
                raise

        except Exception as e:
            # Andere Fehler werden direkt propagiert
            raise


def process_mail():

    with imapclient.IMAPClient(IMAP_SERVER, ssl=True) as client:

        client.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
        client.select_folder(IMAP_FOLDER)

        messages = client.search(["UNSEEN"])

        for msgid, data in client.fetch(messages, ["BODY[]"]).items():

            message = pyzmail.PyzMessage.factory(data[b"BODY[]"])
            sender = message.get_addresses("from")[0][1].strip().lower()

            logging.info(f"Neue Mail von {sender}")

            # ---------------------------------
            # Absender prüfen
            # ---------------------------------

            if sender not in WHITELIST:

                logging.info(
                    f"Absender nicht erlaubt: {sender} (Whitelist-Quelle: {WHITELIST_SOURCE})"
                )

                client.add_gmail_labels(msgid, ["IGNORED"])
                client.set_flags(msgid, ["\\Seen"])

                continue

            subject = message.get_subject()
            text_content = ""

            if message.text_part:
                text_content = message.text_part.get_payload().decode(
                    message.text_part.charset or "utf-8"
                )

            # ---------------------------------
            # Help Befehl
            # ---------------------------------

            if "gethelp" in text_content.lower() or "gethelp" in subject.lower():

                send_mail("Print at Home - Hilfe", HELP_TEXT, sender, bcc_admin=False)

                logging.info(f"Hilfe gesendet an {sender}")

                client.set_flags(msgid, ["\\Seen"])
                continue

            flags = parse_flags(text_content)

            printed_files = []
            failed_files = []

            # ---------------------------------
            # Status Befehl
            # ---------------------------------

            if "getstatus" in text_content.lower() or "getstatus" in subject.lower():

                if sender == ADMIN_EMAIL_NORMALIZED:
                    result = subprocess.run(
                        ["systemctl", "status", "mailtoprint.service"],
                        capture_output=True,
                        text=True,
                    )

                    send_mail(
                        "Print at Home - Systemstatus",
                        f"Systemstatus:\n\n{result.stdout}",
                        sender,
                        bcc_admin=False,
                    )

                    logging.info(f"Systemstatus gesendet an {sender}")

                else:
                    logging.info(f"getstatus ignoriert (kein Admin): {sender}")
                    client.add_gmail_labels(msgid, ["IGNORED"])

                client.set_flags(msgid, ["\\Seen"])
                continue

            # ---------------------------------
            # Anhänge verarbeiten
            # ---------------------------------

            for part in message.mailparts:

                if part.filename and part.filename.lower().endswith(".pdf"):

                    original_filename = part.filename
                    safe_filename = re.sub(r"[^a-zA-Z0-9_.-]", "_", original_filename)

                    filepath = os.path.join(TMP_DIR, safe_filename)

                    try:
                        payload = part.get_payload()
                        if isinstance(payload, str):
                            payload = payload.encode(part.charset or "utf-8", errors="replace")

                        with open(filepath, "wb") as f:
                            f.write(payload)
                    except Exception as e:
                        logging.error(f"Fehler beim Speichern der Datei: {e}")
                        failed_files.append(original_filename)
                        continue

                    if not os.path.exists(filepath):
                        logging.error(f"Datei existiert nicht: {filepath}")
                        failed_files.append(original_filename)
                        continue

                    success = print_pdf(filepath, flags)

                    if success:
                        printed_files.append(safe_filename)
                    else:
                        failed_files.append(safe_filename)

                    time.sleep(2)  # Kurze Pause zwischen Drucken
                    os.remove(filepath)

            # ---------------------------------
            # Label setzen wenn Fehler
            # ---------------------------------

            if failed_files:
                client.add_gmail_labels(msgid, ["FAILED"])

            # ---------------------------------
            # Bericht erzeugen
            # ---------------------------------

            if not printed_files and not failed_files:
                logging.info("Keine druckbaren Anhänge gefunden")
                client.set_flags(msgid, ["\\Seen"])
                continue

            status = "ERFOLGREICH" if not failed_files else "TEILWEISE FEHLER"

            report = f"""
Druckstatus: {status}

Absender: {sender}

Gedruckte Dateien:
{printed_files}

Fehlgeschlagene Dateien:
{failed_files}

Parameter:
Color: {flags["color"]}
Duplex: {flags["duplex"]}
Pages: {flags["pages"]}
Quantity: {flags["quantity"]}
Feedback: {flags["feedback"]}
"""

            # Admin Bericht
            if sender != ADMIN_EMAIL_NORMALIZED:
                send_mail(
                    "Print at Home - Bericht", report, ADMIN_EMAIL, bcc_admin=False
                )

            # Sender Feedback
            if flags["feedback"]:
                send_mail(
                    "Print at Home - Dein Druckauftrag", report, sender, bcc_admin=False
                )
                logging.info(f"Feedback gesendet an {sender}")

            client.set_flags(msgid, ["\\Seen"])


# ---------------------------------------------------
# Hauptloop
# ---------------------------------------------------

if __name__ == "__main__":

    logging.info("Starte Print at Home Dienst")

    while True:

        try:
            process_mail_with_retry()

        except Exception as e:

            logging.error(f"Fehler: {e}")

            send_mail("Print at Home - Fehler", str(e), ADMIN_EMAIL, bcc_admin=False)

            time.sleep(5 * 60)  # Bei Fehler 5 Minuten mehr warten

        time.sleep(5 * 60)
