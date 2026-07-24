# Unraid Integration (ohne Docker Hub)

Diese Anleitung beschreibt Schritt für Schritt, wie das **MC-Router-UI**-Projekt direkt in der Unraid-Benutzeroberfläche (UI) als lokaler Docker-Container genutzt werden kann, ohne das Image auf Docker Hub hochzuladen.

---

## 🛠️ Schritt 1: Projekt-Dateien auf Unraid übertragen

Um das Image auf Unraid bauen zu können, müssen die Projektdateien auf dem Unraid-Server liegen.

1. Erstelle ein Verzeichnis auf deinem Unraid-Server (z. B. unter appdata):
   ```bash
   mkdir -p /mnt/user/appdata/mc-router-ui-src
   ```
2. Übertrage den gesamten Projektordner (mit `app/`, `Dockerfile`, `requirements.txt`, `supervisord.conf`, etc.) in diesen Ordner. 
   * **Über Windows (SMB):** Verbinde dich mit dem Share `appdata` auf Unraid und erstelle/kopiere den Ordner `mc-router-ui-src`.
   * **Über SCP/SFTP:**
     ```bash
     scp -r C:\Users\leona\Documents\02_Development\Coding\Python_Projects\mc-router-ui\* root@UNRAID_IP:/mnt/user/appdata/mc-router-ui-src/
     ```

---

## 🏗️ Schritt 2: Docker-Image lokal bauen

Da das Image nicht auf Docker Hub liegt, bauen wir es manuell auf dem Unraid-Server:

1. Verbinde dich per **SSH** mit deinem Unraid-Server.
2. Navigiere in das Quellcode-Verzeichnis:
   ```bash
   cd /mnt/user/appdata/mc-router-ui-src
   ```
3. Baue das Docker-Image mit dem Tag `mc-router-ui:latest`:
   ```bash
   docker build -t mc-router-ui:latest .
   ```

> [!TIP]
> **Automatisierung mit dem "User Scripts" Plugin:**
> Wenn du das Unraid-Plugin **User Scripts** installiert hast, kannst du dort ein neues Skript namens `Build MC-Router-UI` anlegen und folgenden Inhalt einfügen:
> ```bash
> #!/bin/bash
> cd /mnt/user/appdata/mc-router-ui-src
> docker build -t mc-router-ui:latest .
> ```
> Damit kannst du das Image jederzeit bequem per Klick über die Unraid-Weboberfläche neu bauen.

---

## 💾 Schritt 3: Image-Persistierung bei Unraid-Neustarts

Unraid bereinigt beim Systemneustart gelegentlich nicht registrierte lokale Docker-Images (da sie keinem Online-Repository zugeordnet sind). Um zu verhindern, dass das Image nach einem Neustart des Servers neu gebaut werden muss, kannst du es als `.tar`-Archiv persistieren:

1. Speicher das Image im appdata-Ordner:
   ```bash
   docker save mc-router-ui:latest | gzip > /mnt/user/appdata/mc-router-ui/mc-router-ui.tar.gz
   ```
2. **Automatisches Laden beim Systemstart:**
   Erstelle im **User Scripts**-Plugin ein Skript, das bei **"At Startup of Array"** (beim Start des Arrays) ausgeführt wird:
   ```bash
   #!/bin/bash
   if [ -f /mnt/user/appdata/mc-router-ui/mc-router-ui.tar.gz ]; then
       echo "Lade lokales MC-Router-UI Docker-Image..."
       docker load < /mnt/user/appdata/mc-router-ui/mc-router-ui.tar.gz
   fi
   ```

---

## 📋 Schritt 4: XML-Template in Unraid hinzufügen

Damit du den Container bequem über die Unraid-WebUI verwalten und konfigurieren kannst, importieren wir die Konfigurationsvorlage (`unraid-template.xml`):

1. Kopiere die Datei `unraid-template.xml` aus deinem Projekt in das Flash-Template-Verzeichnis von Unraid:
   ```bash
   cp /mnt/user/appdata/mc-router-ui-src/unraid-template.xml /boot/config/plugins/docker/templates-user/my-MC-Router-UI.xml
   ```
   *(Alternativ kannst du die Datei auch über SMB im Flash-Laufwerk unter `/config/plugins/docker/templates-user/` ablegen.)*

2. Navigiere in deiner Unraid-Weboberfläche auf den Reiter **Docker**.
3. Klicke ganz unten auf **Add Container** (Container hinzufügen).
4. Wähle im Dropdown **Template** (Vorlage) den Eintrag **MC-Router-UI** aus.
5. Das Template befüllt nun alle Einstellungen (Ports, Pfade, Environment-Variablen wie Admin-Username/Passwort, Crafty-Integration und Cloudflare DDNS).
6. Klicke auf **Apply** (Anwenden), um den Container zu erstellen und zu starten.
