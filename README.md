# MC Router UI

Web-UI für [mc-router](https://github.com/itzg/mc-router) — alles in **einem einzigen Docker-Container**.

mc-router und die Web-UI laufen zusammen über Supervisor. Kein zweiter Container nötig.

---

## Schnellstart

```bash
git clone <dieses-repo>
cd mc-router-ui
docker compose up -d --build
```

UI öffnen: `http://<server-ip>:8090`  
Login: `admin` / `changeme` → danach **sofort Passwort in der UI ändern**

---

## Unraid-Einrichtung

In der Unraid Docker-UI diese Umgebungsvariablen setzen:

| Variable         | Standard       | Beschreibung                                      |
|------------------|----------------|---------------------------------------------------|
| `MC_PORT`        | `25565`        | Minecraft-Eingangsport (nach außen freigeben)     |
| `API_PORT`       | `8080`         | mc-router REST-API (intern, nicht nach außen)     |
| `UI_PORT`        | `8090`         | Web-UI Port (Host-seitig)                         |
| `ADMIN_USERNAME` | `admin`        | Login-Name                                        |
| `ADMIN_PASSWORD` | `changeme`     | **Unbedingt ändern!** (nur beim ersten Start)     |
| `SECRET_KEY`     | *(unsicher)*   | Zufälliger langer String für Session-Cookies      |
| `DB_PATH`        | `/data/…`      | Pfad zur SQLite-DB (Volume bleibt erhalten)       |

Port-Mappings in Unraid:
- `25565` → Host-Port nach Wahl (Minecraft)
- `8000`  → Host-Port nach Wahl (Web-UI, z.B. `8090`)

---

## Zu Crafty routen

Crafty Controller läuft als eigener Container und verwaltet Minecraft-Server auf eigenen Ports (z.B. `25566`, `25567`, …).

In der Web-UI → **Route hinzufügen**:
- **Hostname**: `survival.meinedomain.de`
- **Backend**: `crafty:25566` *(Container-Name im selben Docker-Netzwerk)*  
  oder `192.168.1.50:25566` *(direkte IP)*

mc-router liest beim Minecraft-Handshake den Hostnamen und leitet an das passende Backend weiter — alles über Port 25565.

### Netzwerk-Tipp für Crafty

Damit `crafty` als Hostname erreichbar ist, beide Container ins selbe Docker-Netzwerk:

```yaml
# in docker-compose.yml:
networks:
  - crafty-net   # externes Netzwerk wo Crafty drinhängt

networks:
  crafty-net:
    external: true
```

Oder: IP-Adresse des Crafty-Hosts verwenden.

---

## Features

- **Routen verwalten** — Hinzufügen, Bearbeiten, Löschen per UI
- **Standard-Route** — Fallback für unbekannte Hostnamen
- **Live Health-Check** — TCP-Connect-Test pro Backend (grüner/roter Punkt)
- **Aktive Verbindungen** — Live-Zähler aus mc-router `/connections`
- **Router-Status** — zeigt ob mc-router intern läuft
- **SQLite-Persistenz** — Routen überleben Container-Neustarts, werden beim Start automatisch in mc-router eingespielt
- **CORS korrekt gesetzt** — kein Browser-Fehler bei lokalem Zugriff
- **Passwort änderbar** — in der UI unter Einstellungen
- **Fehlermeldungen** — alle Fehler (mc-router offline, Verbindungsprobleme) werden in der UI angezeigt

---

## Architektur (Ein-Container)

```
Minecraft-Client
      │ (Hostname X, Port 25565)
      ▼
  [Container]
  ┌─────────────────────────────────────┐
  │  mc-router :25565                   │
  │     │ intern :8080 REST-API         │
  │     ▼                               │
  │  mc-router-ui :8000 (FastAPI)       │
  │     │ SQLite /data/                 │
  └─────────────────────────────────────┘
      │
      ▼
  Browser :8090 (oder UI_PORT)

      │ Route: survival.domain.de
      ▼
  crafty:25566  (anderer Container / Host)
```

---

## Logs

```bash
docker exec mc-router-ui tail -f /var/log/supervisor/mc-router.log
docker exec mc-router-ui tail -f /var/log/supervisor/web-ui.log
```
