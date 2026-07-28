"""
Crafty Controller API integration and server.properties management.
"""

import logging
import os
import re
from pathlib import Path
from typing import Optional

import httpx

from app.core.config import DB_PATH
from app.db.database import get_db

logger = logging.getLogger(__name__)


async def crafty_request(method: str, path: str, **kwargs):
    """Generic Crafty Controller API call."""
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

    crafty_token = token_row[0].strip()

    if len(crafty_token) > 8:
        masked_token = f"{crafty_token[:4]}...{crafty_token[-4:]}"
    elif crafty_token:
        masked_token = "***"
    else:
        masked_token = "None"

    headers = {
        "Authorization": f"Bearer {crafty_token}",
        "Content-Type": "application/json",
    }

    url = f"{crafty_url}/api/v2{path}"

    logger.info(
        "[Crafty Request] %s %s (Token: %s)", method.upper(), url, masked_token
    )

    try:
        async with httpx.AsyncClient(verify=False, timeout=10) as client:
            r = await getattr(client, method)(url, headers=headers, **kwargs)
            logger.info(
                "[Crafty Response] %s %s -> HTTP %d",
                method.upper(), url, r.status_code,
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
                    "[Crafty API Error] HTTP %d for %s %s: %s",
                    r.status_code, method.upper(), url, err_detail,
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

            if data and data.get("status") in ("error",):
                return None, f"Crafty API error: {data.get('error', data.get('detail', 'Unknown error'))}"
            if isinstance(data, dict) and "data" in data:
                return data["data"], None
            if isinstance(data, dict) and "status" not in data:
                return data, None
            return (data or {}).get("data") or data, None

    except (httpx.ConnectError, httpx.ConnectTimeout) as e:
        logger.error("[Crafty Connection Error] %s for %s %s: %s", type(e).__name__, method.upper(), url, e)
        return None, f"Crafty connection error: Host unreachable ({e})"
    except httpx.TimeoutException as e:
        return None, f"Crafty connection error: Timeout ({e})"
    except Exception as e:
        logger.exception("[Crafty Unexpected Error] %s %s", method.upper(), url)
        return None, f"Crafty connection error: {e}"


# ── server.properties management ──────────────────────────────────────────────

_prop_path_cache: dict[str, Optional[Path]] = {}


def get_server_properties_path(
    server_id: str, server_name: str = None
) -> Optional[Path]:
    """Locate server.properties for a given server ID or name.

    Result is cached in-memory keyed by server_id to avoid repeated
    filesystem globbing on every port-change request.
    """
    if server_id in _prop_path_cache:
        return _prop_path_cache[server_id]

    candidate_dirs = [
        Path("/crafty/servers"),
        Path("/var/opt/crafty/servers"),
        Path("/app/crafty/servers"),
        Path("/data/crafty/servers"),
        Path("/data/servers"),
    ]
    env_base = os.getenv("CRAFTY_SERVERS_DIR", "").strip()
    if env_base:
        candidate_dirs.insert(0, Path(env_base))

    for base_dir in candidate_dirs:
        if not base_dir.exists() or not base_dir.is_dir():
            continue

        # 1. Direct subfolder check by server_id or server_name
        for folder_name in (server_id, server_name):
            if not folder_name:
                continue
            target_dir = base_dir / folder_name
            if target_dir.exists() and target_dir.is_dir():
                direct_prop = target_dir / "server.properties"
                if direct_prop.exists():
                    _prop_path_cache[server_id] = direct_prop
                    return direct_prop
                try:
                    for found in target_dir.rglob("server.properties"):
                        if found.is_file():
                            _prop_path_cache[server_id] = found
                            return found
                except Exception as e:
                    logger.warning("Error searching in %s: %s", target_dir, e)

        # 2. Iterative search across all subdirectories of base_dir
        try:
            for child in base_dir.iterdir():
                if child.is_dir():
                    child_name = child.name.lower()
                    if (server_id and server_id.lower() in child_name) or (
                        server_name and server_name.lower() in child_name
                    ):
                        direct_prop = child / "server.properties"
                        if direct_prop.exists():
                            _prop_path_cache[server_id] = direct_prop
                            return direct_prop
                        for found in child.rglob("server.properties"):
                            if found.is_file():
                                _prop_path_cache[server_id] = found
                                return found
        except Exception as e:
            logger.warning("Error scanning base directory %s: %s", base_dir, e)

    # 3. Environmental fallback override
    env_exact = os.getenv("SERVER_PROPERTIES_PATH", "").strip()
    if env_exact:
        path = Path(env_exact)
        if path.exists():
            _prop_path_cache[server_id] = path
            return path
    _prop_path_cache[server_id] = None
    return None


def update_server_properties_port(file_path: Path, new_port: int) -> bool:
    """Update server-port and query.port in server.properties."""
    try:
        if not file_path.exists():
            logger.error("server.properties not found at %s", file_path)
            return False

        content = file_path.read_text(encoding="utf-8", errors="ignore")
        lines = content.splitlines()
        updated_server = False
        updated_query = False

        new_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("server-port="):
                new_lines.append(f"server-port={new_port}")
                updated_server = True
            elif stripped.startswith("query.port="):
                new_lines.append(f"query.port={new_port}")
                updated_query = True
            else:
                new_lines.append(line)

        if not updated_server:
            new_lines.append(f"server-port={new_port}")
        if not updated_query:
            new_lines.append(f"query.port={new_port}")

        file_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        logger.info("Updated port to %d in %s", new_port, file_path)
        return True
    except Exception:
        logger.exception("Failed to update server.properties at %s", file_path)
        return False
