# Print at Home

Ein Python-Dienst für Raspberry Pi, der E-Mails mit PDF-Anhängen empfängt und diese automatisch auf einem angeschlossenen Drucker ausdruckt.

## Was macht das Script?

- Verbindet sich mit einem Gmail-Konto via IMAP
- Überwacht das Postfach auf neue E-Mails
- Extrahiert PDF-Anhänge
- Druckt diese auf einem CUPS-kompatiblen Drucker
- Sendet Bestätigungsmails an den Absender
- Protokolliert alle Vorgänge

## Voraussetzungen

- Raspberry Pi mit Python 3.7+
- CUPS-kompatibler Drucker
- Gmail-Konto mit App-Passwort

## Setup

### 1. Dependencies installieren

```bash
pip install -r requirements.txt
```

### 2. CUPS installieren

```bash
sudo apt install cups cups-client
sudo systemctl start cups
sudo systemctl enable cups
```

### 3. Drucker hinzufügen

```bash
lpstat -p                    # Verfügbare Drucker anzeigen
lpadmin -p <PRINTER_NAME> -E -v ipp://... -m everywhere
```

### 4. Konfiguration

Erstelle `.env`:
```
EMAIL_ACCOUNT=your-email@gmail.com
EMAIL_PASSWORD=app-password
ADMIN_EMAIL=your-email@gmail.com
```

Passe die `WHITELIST` im Code an und setze `PRINTER_NAME`.

### 5. Starten

```bash
python mail_to_print.py
```

Oder als Systemd-Service:
```bash
sudo cp mailtoprint.service /etc/systemd/system/
sudo systemctl enable mailtoprint
sudo systemctl start mailtoprint
```

## Druckoptionen

In der Mail-Nachricht können folgende Parameter gesetzt werden:

- `color=false` → Schwarzweiß
- `duplex=false` → Einseitig
- `pages=1-3,5` → Nur diese Seiten
- `quantity=2` → Anzahl Kopien
- `feedback=false` → Keine Bestätigung

Beispiel:
```
color=false
duplex=true
pages=1-10
quantity=2
```

## Admin-Befehle

Nur der Admin (ADMIN_EMAIL) kann diese nutzen:

- `gethelp` → Hilfe anfordern
- `getstatus` → Service-Status abfragen

## Logs

```bash
tail -f /home/pi/mailtoprint/logs/mailtoprint.log
```
