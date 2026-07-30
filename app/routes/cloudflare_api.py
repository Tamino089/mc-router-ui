"""
Cloudflare API endpoints for the dashboard.
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.security import current_user
from app.db.schema import user_has_perm
from app.services import cloudflare
from app.services.cloudflare import get_cf_config

router = APIRouter()


@router.get("/api/cf/records")
async def get_cf_records(request: Request):
    token, _, _ = get_cf_config()
    if not token:
        return JSONResponse({"success": False, "error": "Cloudflare not configured"}, status_code=400)

    user = current_user(request)
    if not user or (user.get("role") != "admin" and not user_has_perm(user, "see_cloudflare")):
        return JSONResponse({"success": False, "error": "Permission denied"}, status_code=403)

    zone_id, err = await cloudflare.cf_get_zone_id()
    if err:
        return JSONResponse({"success": False, "error": err}, status_code=500)

    data, err = await cloudflare.cf_request("get", f"/zones/{zone_id}/dns_records", params={"type": "A"})
    if err:
        return JSONResponse({"success": False, "error": err}, status_code=500)

    return {"success": True, "records": data.get("result", [])}


@router.get("/api/cf/zones")
async def get_cf_zones(request: Request):
    """Return available Cloudflare zones for the domain dropdown."""
    user = current_user(request)
    if not user:
        return JSONResponse({"success": False, "error": "Not authenticated"}, status_code=401)

    token, _, _ = get_cf_config()
    if not token:
        return {"success": False, "zones": []}

    data, err = await cloudflare.cf_request("get", "/zones")
    if err or not data or not data.get("result"):
        _, zid, zname = get_cf_config()
        if zname:
            return {"success": True, "zones": [{"name": zname, "id": zid or ""}]}
        return {"success": False, "zones": []}

    zones = [{"name": z["name"], "id": z["id"]} for z in data["result"]]
    return {"success": True, "zones": zones}


@router.post("/api/cf/records")
async def create_cf_record(request: Request):
    token, _, _ = get_cf_config()
    if not token:
        return JSONResponse({"success": False, "error": "Cloudflare not configured"}, status_code=400)

    user = current_user(request)
    if not user or (user.get("role") != "admin" and not user_has_perm(user, "manage_cloudflare")):
        return JSONResponse({"success": False, "error": "Permission denied"}, status_code=403)

    data = await request.json()
    hostname = data.get("hostname", "").strip().lower()
    ip = data.get("ip", "").strip()

    if not hostname:
        return JSONResponse({"success": False, "error": "Hostname is required"}, status_code=400)

    if not ip:
        ip = await cloudflare.get_public_ip()
        if not ip:
            return JSONResponse({"success": False, "error": "Could not auto-detect public IP"}, status_code=500)

    # Prevent duplicating routes logic - check if domain is valid
    valid, v_err = await cloudflare.validate_domain(hostname, False)
    if not valid:
        return JSONResponse({"success": False, "error": v_err}, status_code=400)

    err = await cloudflare.cf_upsert_a_record(hostname, ip)
    if err:
        return JSONResponse({"success": False, "error": err}, status_code=500)

    return {"success": True}


@router.delete("/api/cf/records/{record_id}")
async def delete_cf_record(request: Request, record_id: str):
    token, _, _ = get_cf_config()
    if not token:
        return JSONResponse({"success": False, "error": "Cloudflare not configured"}, status_code=400)

    user = current_user(request)
    if not user or (user.get("role") != "admin" and not user_has_perm(user, "manage_cloudflare")):
        return JSONResponse({"success": False, "error": "Permission denied"}, status_code=403)

    err = await cloudflare.cf_delete_record_by_id(record_id)
    if err:
        return JSONResponse({"success": False, "error": err}, status_code=500)

    return {"success": True}


@router.get("/api/validate-route")
async def validate_route(request: Request):
    """Live validation for route creation/edit form."""
    user = current_user(request)
    if not user:
        return JSONResponse({"success": False, "error": "Not authenticated"}, status_code=401)

    hostname = request.query_params.get("hostname", "").strip().lower()
    domain = request.query_params.get("domain", "").strip().lower()
    backend = request.query_params.get("backend", "").strip()
    is_default = request.query_params.get("is_default", "false").lower() == "true"
    route_id = request.query_params.get("route_id", "")

    # Build effective hostname: subdomain + domain or raw hostname (FQDN)
    if not is_default and domain and hostname and "." not in hostname:
        full_hostname = f"{hostname}.{domain}"
    else:
        full_hostname = hostname

    if route_id:
        try:
            route_id_value = int(route_id)
        except ValueError:
            return JSONResponse({"success": False, "error": "Invalid route id"}, status_code=400)

        from app.db.database import get_db

        with get_db() as con:
            route = con.execute(
                "SELECT owner_id FROM routes WHERE id=?",
                (route_id_value,),
            ).fetchone()
        if not route:
            return JSONResponse({"success": False, "error": "Route not found"}, status_code=404)
        if (
            user.get("role") != "admin"
            and (
                route["owner_id"] != user["id"]
                or not user_has_perm(user, "edit_own_route")
            )
        ):
            return JSONResponse({"success": False, "error": "Permission denied"}, status_code=403)
    elif user.get("role") != "admin" and not user_has_perm(user, "create_route"):
        return JSONResponse({"success": False, "error": "Permission denied"}, status_code=403)

    # Fetch available zones for domain dropdown
    zones = []
    cf_token, _, _ = await cloudflare.get_cf_config()
    if cf_token:
        zdata, zerr = await cloudflare.cf_request("get", "/zones")
        if not zerr and zdata and zdata.get("result"):
            zones = [{"name": z["name"], "id": z["id"]} for z in zdata["result"]]
        if not zones:
            _, zid, zname = cloudflare.get_cf_config()
            if zname:
                zones = [{"name": zname, "id": zid or ""}]

    # Default response structure matching frontend expectations
    res = {
        "val-format": {"status": "neutral", "message": ""},
        "val-cf": {"status": "neutral", "message": ""},
        "val-dns": {"status": "neutral", "message": ""},
        "val-backend": {"status": "neutral", "message": ""},
        "val-resolved": "",
        "zones": zones,
    }

    if is_default:
        res["val-format"] = {"status": "success", "message": "Default route — no hostname needed"}
        res["val-cf"] = {"status": "neutral", "message": "Not applicable for default route"}
        res["val-dns"] = {"status": "neutral", "message": "Not applicable for default route"}
    else:
        # Hostname format validation
        if not hostname:
            res["val-format"] = {"status": "neutral", "message": "Enter a hostname"}
        elif domain and "." not in hostname:
            # Subdomain + domain selected
            from app.routes.routes import HOSTNAME_RE
            if HOSTNAME_RE.match(full_hostname):
                res["val-format"] = {"status": "success", "message": f"Resolves to {full_hostname}"}
                res["val-resolved"] = full_hostname
                valid, v_err = await cloudflare.validate_domain(full_hostname, is_default)
                if valid:
                    res["val-cf"] = {"status": "success", "message": "Domain matches configured zone"}
                else:
                    res["val-cf"] = {"status": "error", "message": v_err or "Domain not under configured zone"}
            else:
                res["val-format"] = {"status": "error", "message": "Invalid subdomain format"}
        elif "." not in hostname:
            # Subdomain-only - needs Cloudflare
            if not cf_token:
                res["val-format"] = {"status": "warning", "message": "Subdomain only — Cloudflare needed to resolve"}
                res["val-cf"] = {"status": "error", "message": "Cloudflare not configured"}
            else:
                resolved = await cloudflare.resolve_hostname(hostname)
                if resolved:
                    res["val-format"] = {"status": "success", "message": f"Resolves to {resolved}"}
                    res["val-cf"] = {"status": "success", "message": "Cloudflare configured"}
                    res["val-resolved"] = resolved
                else:
                    res["val-format"] = {"status": "error", "message": "No Cloudflare zone configured to resolve subdomain"}
                    res["val-cf"] = {"status": "success", "message": "Cloudflare configured"}
        else:
            # Full FQDN provided
            from app.routes.routes import HOSTNAME_RE
            if HOSTNAME_RE.match(hostname):
                res["val-format"] = {"status": "success", "message": "Valid FQDN format"}
                valid, v_err = await cloudflare.validate_domain(hostname, is_default)
                if valid:
                    res["val-cf"] = {"status": "success", "message": "Domain matches configured zone"}
                else:
                    res["val-cf"] = {"status": "error", "message": v_err or "Domain not under configured zone"}
            else:
                res["val-format"] = {"status": "error", "message": "Invalid FQDN format"}

        # Docker-managed hostname check
        check_hostname = full_hostname or hostname
        if check_hostname and not is_default:
            from app.services.docker_watcher import is_docker_managed
            if await is_docker_managed(check_hostname):
                res["val-format"] = {"status": "error", "message": f"'{check_hostname}' is managed by Docker labels"}

        # DNS record check (only for non-default, non-docker routes)
        if check_hostname and "." in check_hostname:
            # Check if hostname already exists in DB
            from app.db.database import get_db
            with get_db() as con:
                existing = con.execute(
                    "SELECT id FROM routes WHERE hostname=? AND id!=?",
                    (check_hostname, int(route_id) if route_id else -1),
                ).fetchone()
                if existing:
                    res["val-dns"] = {"status": "error", "message": "Hostname already exists in database"}
                else:
                    # Check Cloudflare for conflicts
                    if cf_token:
                        zone_id, err = await cloudflare.cf_get_zone_id()
                        if not err:
                            data, err = await cloudflare.cf_request("get", f"/zones/{zone_id}/dns_records", params={"type": "A", "name": check_hostname})
                            if not err and data.get("result"):
                                res["val-dns"] = {"status": "warning", "message": "DNS record already exists in Cloudflare"}
                            else:
                                res["val-dns"] = {"status": "success", "message": "No DNS conflict"}
                        else:
                            res["val-dns"] = {"status": "neutral", "message": "Could not check DNS"}
                    else:
                        res["val-dns"] = {"status": "neutral", "message": "Cloudflare not configured — cannot check DNS"}

    # Backend reachability check (TCP)
    if backend:
        try:
            parts = backend.rsplit(":", 1)
            if len(parts) == 2 and parts[1].isdigit():
                host, port = parts[0], int(parts[1])
                from app.services.health import tcp_check
                import asyncio
                healthy, latency, tcp_err = await asyncio.to_thread(tcp_check, host, port, 2.0)
                if healthy:
                    res["val-backend"] = {"status": "success", "message": f"Reachable ({latency}ms)"}
                else:
                    res["val-backend"] = {"status": "error", "message": tcp_err or "Backend unreachable (TCP connection failed)"}
            else:
                res["val-backend"] = {"status": "error", "message": "Invalid backend format (host:port)"}
        except Exception:
            res["val-backend"] = {"status": "error", "message": "Backend check failed"}
    else:
        res["val-backend"] = {"status": "neutral", "message": "Enter a backend server"}

    return {"success": True, **res}
