# Implementation Plan & Review — MC Router UI Projekt

Dieses Dokument beschreibt den Status der Implementierung sowie die durchgeführte Senior-Developer-Review, identifizierte Bugs und deren Behebung.

## Status der Implementierung

Die in der vorherigen Version des Plans vorgesehenen Kernfunktionen wurden erfolgreich implementiert und im Code verifiziert:

- **Domain-Validierung (DNS/Zone-Einschränkung)**:
  - Backend-Logik (`get_zone_name_domain` und `validate_domain`) prüft eingehende Hostnames gegen die Cloudflare-Hauptdomain.
  - Integration in alle Endpunkte zum Hinzufügen, Bearbeiten und Validieren von Routen.
  - Frontend-Sperre des "Bestätigen & Speichern"-Buttons bei Validierungsfehlern (Status `error`).
- **Dateisystem-Zugriff & Port-Änderung in server.properties**:
  - Automatisches Auffinden der `server.properties` über standardisierte Pfade und Umgebungsvariablen.
  - Endpunkt `/api/crafty/servers/{server_id}/port` zur synchronen Aktualisierung im Dateisystem und per Crafty API.
- **UI-Redesign (Crafty-Look)**:
  - Anpassung der Farbpalette auf dunkles Anthrazit mit dem charakteristischen Crafty-Orange (`#ff7a00`).
  - Abgerundete Ecken, Hover-Effekte und Lade-Spinner.

---

## Senior-Developer-Review & Bugfixes

Bei der detaillierten Code-Review wurden folgende Bugs und Unstimmigkeiten gefunden und behoben:

### 1. JavaScript String-Splitting Bug (Frontend)
- **Problem**: In `index.html` wurde versucht, `backend.rsplit` (Python-Methode) in JavaScript abzufragen. Bei Backends ohne Port (z. B. nur `crafty`) führte dies dazu, dass die gesamte Host-Adresse fälschlicherweise als Port extrahiert wurde, was zu Validierungsfehlern führte.
- **Lösung**: Umstellung auf native JavaScript-String-Methoden (`lastIndexOf(':')`), um Host und Port bei der Bearbeitung sauber zu trennen.

### 2. Fehlende Transaktionssicherheit bei Routen-Updates (Backend)
- **Problem**: Bei `/routes/edit/{route_id}` wurde die alte Route bereits aus `mc-router` gelöscht, *bevor* die neue Route übertragen wurde. Schlug die Registrierung der neuen Route fehl, war die alte Route weg und das System verblieb in einem inkonsistenten Zustand.
- **Lösung**: Die neue Route wird nun zuerst übertragen. Erst bei Erfolg wird die alte Route gelöscht und der DB-Commit durchgeführt.

### 3. Redundante DB-Initialisierung (Performance)
- **Problem**: `init_db()` wurde sowohl beim Laden des Python-Moduls als auch im `lifespan`-Startup-Handler ausgeführt, was zu doppelten Festplatten-Schreibzugriffen auf SQLite beim Anwendungsstart führte.
- **Lösung**: Early-Exit in `init_db()`, falls `SECRET_KEY` bereits gesetzt ist.

### 4. Ungenaue lokale DB-Prüfung in der Live-Validierung
- **Problem**: Wenn das Hostname-Feld noch leer war (beim Tippen), prüfte die Live-Validierung fälschlicherweise gegen `__default__` und gab einen Validierungsfehler zurück, wenn bereits eine Standard-Route existierte.
- **Lösung**: Der lokale DB-Check wird nun nur noch ausgeführt, wenn ein Hostname eingetragen ist oder es sich explizit um eine Standard-Route handelt.

### 5. Standard-Route Namens-Erzwingung
- **Problem**: Wurde die Checkbox "Als Standard-Route setzen" aktiviert, konnte trotzdem ein beliebiger Text als Hostname in der DB landen, was zu Inkonsistenzen mit `mc-router` führte.
- **Lösung**: Erzwingen des Hostnames `"__default__"` im Backend, sobald `default` aktiviert ist.

### 6. Login-Redirect für authentifizierte Benutzer (UX)
- **Problem**: Bereits angemeldete Benutzer, die `/login` aufriefen, sahen erneut das Login-Formular.
- **Lösung**: Automatischer Redirect auf das Dashboard `/` für aktive Sitzungen.

---

## Verification Plan

### Manual Verification
1. **Domain-Validierung**:
   - Versuchen, eine Route mit einer ungültigen Domain anzulegen (z. B. `test.google.com`, falls die Zone auf `meinedomain.de` läuft). Verifizieren, dass das UI den Fehler anzeigt und der Button blockiert ist.
   - Anlegen einer korrekten Subdomain (z. B. `mc.meinedomain.de`). Dies muss gelingen.
2. **Standard-Route**:
   - Erstellen einer Standard-Route (Fallback). Verifizieren, dass der Hostname in der Datenbank auf `__default__` gesetzt wird und das UI ein `*` (Standard-Badge) anzeigt.
3. **Port-Änderung & server.properties**:
   - Ändern des Ports eines Crafty-Servers in der UI. Verifizieren, dass der Port sowohl in Crafty als auch im Mount-Verzeichnis der `server.properties` überschrieben wird.
4. **Login-Redirect**:
   - Nach erfolgreichem Login `/login` aufrufen und prüfen, ob man direkt wieder auf `/` landet.
