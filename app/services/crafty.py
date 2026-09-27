import logging
import os
import re
from pathlib import Path

import httpx

from app.db.database import get_db

logger = logging.getLogger(__name__)

CRAFTY_VERIFY_TLS = os.getenv("CRAFTY_INSECURE_SKIP_VERIFY", "").lower() not in (
    "1",
    "true",
    "yes",
)

async def crafty_request(method: str, path: str, **kwargs):
    with get_db() as con:
        url_row = con.execute(
            "SELECT value FROM settings WHERE key='crafty_url'"
        ).fetchone()
        token_row = con.execute(
            "SELECT value FROM settings WHERE key='crafty_token'"
        ).fetchone()

    if not url_row or not token_row:
        return None, "Crafty integration is not configured."

    crafty_url = url_row[0].strip().rstrip("/")
    if "/api/v2" in crafty_url:
        crafty_url = re.sub(r"/api/v2/?$", "", crafty_url)

    headers = {
        "Authorization": f"Bearer {token_row[0].strip()}",
        "Content-Type": "application/json",
    }
    url = f"{crafty_url}/api/v2{path}"
    logger.info("Crafty request: %s %s", method.upper(), url)

    try:
        async with httpx.AsyncClient(verify=CRAFTY_VERIFY_TLS, timeout=10) as client:
            r = await getattr(client, method)(url, headers=headers, **kwargs)
            logger.info(
                "Crafty response: %s %s -> HTTP %d", method.upper(), url, r.status_code
            )

            try:
                r.raise_for_status()
            except httpx.HTTPStatusError:
                try:
                    err_json = r.json()
                    err_detail = (
                        err_json.get("error")
                        or err_json.get("detail")
                        or err_json.get("message")
                        or r.text
                    )
                except Exception:
                    err_detail = r.text[:500]

                logger.error(
                    "Crafty API error HTTP %d for %s %s: %s",
                    r.status_code,
                    method.upper(),
                    url,
                    err_detail,
                )
                status_map = {
                    401: "Crafty API error (401 Unauthorized): Invalid API token.",
                    403: "Crafty API error (403 Forbidden): Insufficient permissions.",
                    404: "Crafty API error (404 Not Found): Endpoint or server not found.",
                }
                return None, status_map.get(
                    r.status_code,
                    f"Crafty API error (HTTP {r.status_code}): {err_detail}",
                )

            try:
                data = r.json()
            except ValueError:
                return None, f"Crafty API error (invalid JSON): {r.text[:200]}"

            if isinstance(data, dict):
                if data.get("status") == "error":
                    return None, (
                        "Crafty API error: "
                        f"{data.get('error', data.get('detail', 'Unknown error'))}"
                    )
                if "data" in data:
                    return data["data"], None
                if "status" not in data:
                    return data, None
                return data.get("data") or data, None

            return data, None

    except (httpx.ConnectError, httpx.ConnectTimeout) as e:
        logger.error(
            "Crafty connection error %s for %s %s: %s",
            type(e).__name__,
            method.upper(),
            url,
            e,
        )

        if "certificate" in str(e).lower():
            return None, (
                "Crafty connection error: TLS certificate verification failed. "
                "Self-hosted Crafty commonly uses a self-signed certificate - "
                "set CRAFTY_INSECURE_SKIP_VERIFY=true if you trust this host, "
                "or install a valid certificate on Crafty."
            )
        return None, f"Crafty connection error: Host unreachable ({e})"
    except httpx.TimeoutException as e:
        return None, f"Crafty connection error: Timeout ({e})"
    except Exception as e:
        logger.exception("Crafty unexpected error %s %s", method.upper(), url)
        return None, f"Crafty connection error: {e}"

_prop_path_cache: dict[str, Path | None] = {}

_CANDIDATE_DIRS = (
    Path("/crafty/servers"),
    Path("/var/opt/crafty/servers"),
    Path("/app/crafty/servers"),
    Path("/data/crafty/servers"),
    Path("/data/servers"),
)

def _find_properties_in(directory: Path) -> Path | None:
    direct = directory / "server.properties"
    if direct.exists():
        return direct
    try:
        for found in directory.rglob("server.properties"):
            if found.is_file():
                return found
    except OSError as e:
        logger.warning("Error searching %s: %s", directory, e)
    return None

def get_server_properties_path(
    server_id: str, server_name: str | None = None
) -> Path | None:
    cached = _prop_path_cache.get(server_id)
    if cached is not None:
        return cached

    candidate_dirs = list(_CANDIDATE_DIRS)
    env_base = os.getenv("CRAFTY_SERVERS_DIR", "").strip()
    if env_base:
        candidate_dirs.insert(0, Path(env_base))

    for base_dir in candidate_dirs:
        if not base_dir.is_dir():
            continue

        for folder_name in (server_id, server_name):
            if not folder_name:
                continue
            target_dir = base_dir / folder_name
            if target_dir.is_dir():
                found = _find_properties_in(target_dir)
                if found:
                    _prop_path_cache[server_id] = found
                    return found

        try:
            for child in base_dir.iterdir():
                if not child.is_dir():
                    continue
                child_name = child.name.lower()
                if (server_id and server_id.lower() in child_name) or (
                    server_name and server_name.lower() in child_name
                ):
                    found = _find_properties_in(child)
                    if found:
                        _prop_path_cache[server_id] = found
                        return found
        except OSError as e:
            logger.warning("Error scanning %s: %s", base_dir, e)

    env_exact = os.getenv("SERVER_PROPERTIES_PATH", "").strip()
    if env_exact and Path(env_exact).exists():
        path = Path(env_exact)
        _prop_path_cache[server_id] = path
        return path

    logger.warning("No server.properties found for server '%s'", server_id)
    return None

def update_server_properties_port(file_path: Path, new_port: int) -> bool:
    try:
        if not file_path.exists():
            logger.error("server.properties not found at %s", file_path)
            return False

        content = file_path.read_text(encoding="utf-8", errors="ignore")
        lines = content.splitlines()
        updated_server = False
        updated_query = False
        old_server_port = None
        old_query_port = None

        new_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("server-port="):
                old_server_port = line.split("=", 1)[1] if "=" in line else ""
                new_lines.append(f"server-port={new_port}")
                updated_server = True
            elif stripped.startswith("query.port="):
                old_query_port = line.split("=", 1)[1] if "=" in line else ""
                new_lines.append(f"query.port={new_port}")
                updated_query = True
            else:
                new_lines.append(line)

        if not updated_server:
            new_lines.append(f"server-port={new_port}")
        if not updated_query:
            new_lines.append(f"query.port={new_port}")

        temp_path = file_path.with_name(f".{file_path.name}.tmp")
        temp_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        os.replace(temp_path, file_path)

        logger.info(
            "Updated %s: server-port %s -> %d, query.port %s -> %d",
            file_path,
            old_server_port or "(missing)",
            new_port,
            old_query_port or "(missing)",
            new_port,
        )
        return True
    except Exception:
        logger.exception("Failed to update %s", file_path)
        return False
