import asyncio
import logging
import os

import httpx

from app.core import config
from app.db.database import get_db

logger = logging.getLogger(__name__)

_cf_zone_id_cache: str | None = config.CF_ZONE_ID or None
_cf_zone_name_cache: str | None = config.CF_ZONE_NAME or None

def invalidate_zone_cache() -> None:
    global _cf_zone_id_cache, _cf_zone_name_cache
    _cf_zone_id_cache = config.CF_ZONE_ID or None
    _cf_zone_name_cache = config.CF_ZONE_NAME or None

def get_cf_config():
    with get_db() as con:
        t_row = con.execute("SELECT value FROM settings WHERE key='cf_api_token'").fetchone()
        z_row = con.execute("SELECT value FROM settings WHERE key='cf_zone_id'").fetchone()
        zn_row = con.execute("SELECT value FROM settings WHERE key='cf_zone_name'").fetchone()

    token = (t_row[0] if t_row else "") or config.CF_API_TOKEN
    zone_id = (z_row[0] if z_row else "") or config.CF_ZONE_ID
    zone_name = (zn_row[0] if zn_row else "") or config.CF_ZONE_NAME
    return token, zone_id, zone_name

async def cf_request(method: str, path: str, **kwargs):
    token, _, _ = get_cf_config()
    if not token:
        return None, "Cloudflare API token not configured."

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    url = config.CF_API_BASE + path
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await getattr(client, method)(url, headers=headers, **kwargs)
            data = r.json()
            if not data.get("success", False):
                return None, f"Cloudflare API error: {data.get('errors')}"
            return data, None
    except Exception as e:
        return None, f"Cloudflare connection error: {e}"

async def cf_get_zone_id():
    token, zid, zname = get_cf_config()
    if not token:
        return None, "Cloudflare API token not configured."
    if zid:
        return zid, None
    if not zname:
        return None, "No CF Zone ID or Name configured."

    data, err = await cf_request("get", "/zones", params={"name": zname})
    if err:
        return None, err
    results = data.get("result", [])
    if not results:
        return None, f"Cloudflare zone '{zname}' not found"
    return results[0]["id"], None

async def get_public_ip() -> str | None:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get("https://api.ipify.org?format=json")
            r.raise_for_status()
            return r.json().get("ip")
    except Exception as e:
        logger.warning("Could not determine public IP: %s", e)
        return None

async def cf_find_record(zone_id: str, hostname: str):
    data, err = await cf_request(
        "get", f"/zones/{zone_id}/dns_records", params={"type": "A", "name": hostname}
    )
    if err:
        return None, err
    results = data.get("result", [])
    return (results[0] if results else None), None

async def cf_upsert_a_record(hostname: str, ip: str) -> str | None:
    token, _, _ = get_cf_config()
    if not token:
        return None
    zone_id, err = await cf_get_zone_id()
    if err:
        return err
    record, err = await cf_find_record(zone_id, hostname)
    if err:
        return err
    payload = {"type": "A", "name": hostname, "content": ip, "ttl": 1, "proxied": False}
    if record:
        if record.get("content") == ip:
            return None
        _, err = await cf_request(
            "put", f"/zones/{zone_id}/dns_records/{record['id']}", json=payload
        )
    else:
        _, err = await cf_request(
            "post", f"/zones/{zone_id}/dns_records", json=payload
        )
    return err

async def cf_delete_record_by_hostname(hostname: str) -> str | None:
    token, _, _ = get_cf_config()
    if not token:
        return None
    zone_id, err = await cf_get_zone_id()
    if err:
        return err
    record, err = await cf_find_record(zone_id, hostname)
    if err:
        return err
    if not record:
        return None
    _, err = await cf_request(
        "delete", f"/zones/{zone_id}/dns_records/{record['id']}"
    )
    return err

async def cf_delete_record_by_id(record_id: str) -> str | None:
    token, _, _ = get_cf_config()
    if not token:
        return None
    zone_id, err = await cf_get_zone_id()
    if err:
        return err
    _, err = await cf_request(
        "delete", f"/zones/{zone_id}/dns_records/{record_id}"
    )
    return err

async def sync_dns_for_route(hostname: str, is_default: bool) -> str | None:
    token, _, _ = get_cf_config()
    if not token:
        return "Cloudflare not configured — DNS record not created"
    if is_default or not hostname or hostname == "__default__":
        return None
    ip = await get_public_ip()
    if not ip:
        return "Could not determine public IP — DNS record not updated"
    return await cf_upsert_a_record(hostname, ip)

async def get_zone_name_domain() -> str | None:
    global _cf_zone_name_cache
    if _cf_zone_name_cache:
        return _cf_zone_name_cache.strip().lower()

    env_zone = os.getenv("CLOUDFLARE_ZONE_NAME", "").strip().lower()
    if env_zone:
        _cf_zone_name_cache = env_zone
        return env_zone

    try:
        with get_db() as con:
            row = con.execute(
                "SELECT value FROM settings WHERE key='cf_zone_name'"
            ).fetchone()
            if row and row[0]:
                _cf_zone_name_cache = row[0].strip().lower()
                return _cf_zone_name_cache
    except Exception:
        logger.exception("Could not read cf_zone_name from settings")

    if config.CF_API_TOKEN and (config.CF_ZONE_ID or _cf_zone_id_cache):
        zone_id = config.CF_ZONE_ID or _cf_zone_id_cache
        data, err = await cf_request("get", f"/zones/{zone_id}")
        if not err and data:
            zname = data.get("result", {}).get("name", "").strip().lower()
            if zname:
                _cf_zone_name_cache = zname
                return zname
    return None

async def resolve_hostname(hostname: str) -> str | None:
    hostname = hostname.strip().lower()
    if not hostname or "." in hostname:
        return hostname
    zone = await get_zone_name_domain()
    if zone:
        return f"{hostname}.{zone}"
    return None

async def validate_domain(
    hostname: str, is_default: bool
) -> tuple[bool, str | None]:
    if is_default or hostname == "__default__":
        return True, None
    zone = await get_zone_name_domain()
    hostname_clean = hostname.strip().lower()
    if zone:
        if hostname_clean == zone or hostname_clean.endswith("." + zone):
            return True, None

        if "." not in hostname_clean:
            return True, None
        return (
            False,
            f"Invalid domain: '{hostname}' must be under the main domain '{zone}'.",
        )
    return True, None

async def ddns_loop() -> None:
    consecutive_errors = 0
    while True:
        try:
            token, zone_id, zone_name = get_cf_config()
            if token and (zone_id or zone_name):
                ip = await get_public_ip()
                if ip:
                    with get_db() as con:
                        last_row = con.execute(
                            "SELECT value FROM settings WHERE key='last_public_ip'"
                        ).fetchone()
                        last_ip = last_row["value"] if last_row else None
                        rows = con.execute(
                            "SELECT hostname, is_default FROM routes"
                        ).fetchall()

                    if ip != last_ip:
                        logger.info("Public IP changed: %s -> %s", last_ip, ip)
                        hostnames = [
                            row["hostname"]
                            for row in rows
                            if row["hostname"]
                            and not row["is_default"]
                            and row["hostname"] != "__default__"
                        ]
                        for hostname in hostnames:
                            err = await cf_upsert_a_record(hostname, ip)
                            if err:
                                logger.warning("DNS update failed for %s: %s", hostname, err)

                        with get_db() as con:
                            con.execute(
                                "INSERT OR REPLACE INTO settings (key,value) "
                                "VALUES ('last_public_ip',?)",
                                (ip,),
                            )
                            con.commit()
            consecutive_errors = 0
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Error in DDNS loop")
            consecutive_errors += 1
        backoff = min(consecutive_errors * config.DDNS_INTERVAL, 3600)
        await asyncio.sleep(config.DDNS_INTERVAL + backoff)
