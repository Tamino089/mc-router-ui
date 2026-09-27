import asyncio
import ipaddress
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.ratelimit import RateLimiter
from app.core.security import current_user
from app.core.validation import HOSTNAME_RE
from app.db.database import get_db
from app.db.schema import user_has_perm
from app.routes import get_form_or_json
from app.services import cloudflare
from app.services.cloudflare import get_cf_config

logger = logging.getLogger(__name__)

router = APIRouter()

validate_limiter = RateLimiter(max_attempts=30, window_seconds=60)

def _error(message: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse({"success": False, "error": message}, status_code=status_code)

def _authorize(request: Request, permission: str):
    user = current_user(request)
    if not user:
        return None, _error("Not authenticated", 401)
    if user.get("role") != "admin" and not user_has_perm(user, permission):
        return None, _error("Permission denied", 403)
    return user, None

@router.get("/api/cf/records")
async def get_cf_records(request: Request):
    _, error = _authorize(request, "see_cloudflare")
    if error:
        return error

    token, _, _ = get_cf_config()
    if not token:
        return _error("Cloudflare not configured")

    zone_id, err = await cloudflare.cf_get_zone_id()
    if err:
        return _error(err, 502)

    data, err = await cloudflare.cf_request(
        "get", f"/zones/{zone_id}/dns_records", params={"type": "A"}
    )
    if err:
        return _error(err, 502)

    return {"success": True, "records": data.get("result", [])}

@router.get("/api/cf/zones")
async def get_cf_zones(request: Request):
    _, error = _authorize(request, "see_cloudflare")
    if error:
        return error

    token, _, _ = get_cf_config()
    if not token:
        return {"success": False, "zones": []}

    data, err = await cloudflare.cf_request("get", "/zones")
    if err or not data or not data.get("result"):
        _, zone_id, zone_name = get_cf_config()
        if zone_name:
            return {"success": True, "zones": [{"name": zone_name, "id": zone_id or ""}]}
        return {"success": False, "zones": []}

    return {
        "success": True,
        "zones": [{"name": z["name"], "id": z["id"]} for z in data["result"]],
    }

@router.post("/api/cf/records")
async def create_cf_record(request: Request):
    _, error = _authorize(request, "manage_cloudflare")
    if error:
        return error

    token, _, _ = get_cf_config()
    if not token:
        return _error("Cloudflare not configured")

    data = await get_form_or_json(request)
    hostname = str(data.get("hostname", "")).strip().lower()
    ip = str(data.get("ip", "")).strip()

    if not hostname:
        return _error("Hostname is required")

    if ip:
        try:
            ipaddress.ip_address(ip)
        except ValueError:
            return _error("IP must be a valid IPv4 or IPv6 address")
    else:
        ip = await cloudflare.get_public_ip()
        if not ip:
            return _error("Could not auto-detect public IP", 502)

    valid, v_err = await cloudflare.validate_domain(hostname, False)
    if not valid:
        return _error(v_err)

    err = await cloudflare.cf_upsert_a_record(hostname, ip)
    if err:
        return _error(err, 502)

    return {"success": True}

@router.delete("/api/cf/records/{record_id}")
async def delete_cf_record(request: Request, record_id: str):
    _, error = _authorize(request, "manage_cloudflare")
    if error:
        return error

    token, _, _ = get_cf_config()
    if not token:
        return _error("Cloudflare not configured")

    if not record_id.isalnum():
        return _error("Invalid record id")

    err = await cloudflare.cf_delete_record_by_id(record_id)
    if err:
        return _error(err, 502)

    return {"success": True}

async def _available_zones() -> list[dict]:
    token, _, _ = cloudflare.get_cf_config()
    if not token:
        return []
    data, err = await cloudflare.cf_request("get", "/zones")
    if not err and data and data.get("result"):
        return [{"name": z["name"], "id": z["id"]} for z in data["result"]]
    _, zone_id, zone_name = cloudflare.get_cf_config()
    return [{"name": zone_name, "id": zone_id or ""}] if zone_name else []

def _authorize_route_change(user: dict, route_id: str | None):
    if route_id:
        try:
            route_id_value = int(route_id)
        except ValueError:
            return None, _error("Invalid route id")
        with get_db() as con:
            route = con.execute(
                "SELECT owner_id FROM routes WHERE id=?", (route_id_value,)
            ).fetchone()
        if not route:
            return None, _error("Route not found", 404)
        if user.get("role") != "admin" and (
            route["owner_id"] != user["id"]
            or not user_has_perm(user, "edit_own_route")
        ):
            return None, _error("Permission denied", 403)
        return route_id_value, None

    if user.get("role") != "admin" and not user_has_perm(user, "create_route"):
        return None, _error("Permission denied", 403)
    return None, None

@router.get("/api/validate-route")
async def validate_route(request: Request):
    user = current_user(request)
    if not user:
        return _error("Not authenticated", 401)

    if await validate_limiter.is_limited(f"user:{user['id']}"):
        return _error("Too many validation requests. Please wait a moment.", 429)
    await validate_limiter.record(f"user:{user['id']}")

    hostname = request.query_params.get("hostname", "").strip().lower()
    domain = request.query_params.get("domain", "").strip().lower()
    backend = request.query_params.get("backend", "").strip()
    is_default = request.query_params.get("is_default", "false").lower() == "true"
    route_id = request.query_params.get("route_id", "")

    route_id_value, error = _authorize_route_change(user, route_id or None)
    if error:
        return error

    if not is_default and domain and hostname and "." not in hostname:
        full_hostname = f"{hostname}.{domain}"
    else:
        full_hostname = hostname

    cf_token, _, _ = cloudflare.get_cf_config()

    res = {
        "val-format": {"status": "neutral", "message": ""},
        "val-cf": {"status": "neutral", "message": ""},
        "val-dns": {"status": "neutral", "message": ""},
        "val-backend": {"status": "neutral", "message": ""},
        "val-resolved": "",
        "zones": await _available_zones(),
    }

    if is_default:
        res["val-format"] = {"status": "success", "message": "Default route - no hostname needed"}
        res["val-cf"] = {"status": "neutral", "message": "Not applicable for default route"}
        res["val-dns"] = {"status": "neutral", "message": "Not applicable for default route"}
    else:
        if not hostname:
            res["val-format"] = {"status": "neutral", "message": "Enter a hostname"}
        elif domain and "." not in hostname:
            if HOSTNAME_RE.match(full_hostname):
                res["val-format"] = {"status": "success", "message": f"Resolves to {full_hostname}"}
                res["val-resolved"] = full_hostname
                valid, v_err = await cloudflare.validate_domain(full_hostname, is_default)
                if valid:
                    res["val-cf"] = {
                        "status": "success",
                        "message": "Domain matches configured zone",
                    }
                else:
                    res["val-cf"] = {
                        "status": "error",
                        "message": v_err or "Domain not under configured zone",
                    }
            else:
                res["val-format"] = {"status": "error", "message": "Invalid subdomain format"}
        elif "." not in hostname:
            if not cf_token:
                res["val-format"] = {
                    "status": "warning",
                    "message": "Subdomain only - Cloudflare needed to resolve",
                }
                res["val-cf"] = {"status": "error", "message": "Cloudflare not configured"}
            else:
                resolved = await cloudflare.resolve_hostname(hostname)
                res["val-cf"] = {"status": "success", "message": "Cloudflare configured"}
                if resolved:
                    res["val-format"] = {"status": "success", "message": f"Resolves to {resolved}"}
                    res["val-resolved"] = resolved
                else:
                    res["val-format"] = {
                        "status": "error",
                        "message": "No Cloudflare zone configured to resolve subdomain",
                    }
        elif HOSTNAME_RE.match(hostname):
            res["val-format"] = {"status": "success", "message": "Valid FQDN format"}
            valid, v_err = await cloudflare.validate_domain(hostname, is_default)
            if valid:
                res["val-cf"] = {"status": "success", "message": "Domain matches configured zone"}
            else:
                res["val-cf"] = {
                    "status": "error",
                    "message": v_err or "Domain not under configured zone",
                }
        else:
            res["val-format"] = {"status": "error", "message": "Invalid FQDN format"}

        check_hostname = full_hostname or hostname
        if check_hostname:
            from app.services.docker_watcher import is_docker_managed

            if await is_docker_managed(check_hostname):
                res["val-format"] = {
                    "status": "error",
                    "message": f"'{check_hostname}' is managed by Docker labels",
                }

        if check_hostname and "." in check_hostname:
            with get_db() as con:
                existing = con.execute(
                    "SELECT id FROM routes WHERE hostname=? AND id!=?",
                    (check_hostname, route_id_value if route_id_value is not None else -1),
                ).fetchone()

            if existing:
                res["val-dns"] = {
                    "status": "error",
                    "message": "Hostname already exists in database",
                }
            elif not cf_token:
                res["val-dns"] = {
                    "status": "neutral",
                    "message": "Cloudflare not configured - cannot check DNS",
                }
            else:
                zone_id, err = await cloudflare.cf_get_zone_id()
                if err:
                    res["val-dns"] = {"status": "neutral", "message": "Could not check DNS"}
                else:
                    data, err = await cloudflare.cf_request(
                        "get",
                        f"/zones/{zone_id}/dns_records",
                        params={"type": "A", "name": check_hostname},
                    )
                    if not err and data.get("result"):
                        res["val-dns"] = {
                            "status": "warning",
                            "message": "DNS record already exists in Cloudflare",
                        }
                    else:
                        res["val-dns"] = {"status": "success", "message": "No DNS conflict"}

    if backend:
        res["val-backend"] = await _check_backend(backend)
    else:
        res["val-backend"] = {"status": "neutral", "message": "Enter a backend server"}

    return {"success": True, **res}

async def _check_backend(backend: str) -> dict:
    parts = backend.rsplit(":", 1)
    if len(parts) != 2 or not parts[1].isdigit():
        return {"status": "error", "message": "Invalid backend format (host:port)"}

    from app.services.health import tcp_check

    host, port = parts[0], int(parts[1])
    try:
        healthy, latency, tcp_err = await asyncio.to_thread(tcp_check, host, port, 2.0)
    except Exception:
        logger.exception("Backend validation failed for %s", backend)
        return {"status": "error", "message": "Backend check failed"}

    if healthy:
        return {"status": "success", "message": f"Reachable ({latency}ms)"}
    return {
        "status": "error",
        "message": tcp_err or "Backend unreachable (TCP connection failed)",
    }
