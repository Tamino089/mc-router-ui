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
    backend = request.query_params.get("backend", "").strip()
    is_default = request.query_params.get("is_default", "false").lower() == "true"
    route_id = request.query_params.get("route_id", "")

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

    # Default response structure matching frontend expectations
    res = {
        "val-format": {"status": "neutral", "message": ""},
        "val-cf": {"status": "neutral", "message": ""},
        "val-dns": {"status": "neutral", "message": ""},
        "val-backend": {"status": "neutral", "message": ""},
    }

    if is_default:
        res["val-format"] = {"status": "success", "message": "Default route — no hostname needed"}
        res["val-cf"] = {"status": "neutral", "message": "Not applicable for default route"}
        res["val-dns"] = {"status": "neutral", "message": "Not applicable for default route"}
    else:
        # Hostname format validation
        if not hostname:
            res["val-format"] = {"status": "error", "message": "Hostname is required"}
        elif "." not in hostname:
            # Subdomain-only - needs Cloudflare
            cf_token, _, _ = await cloudflare.get_cf_config()
            if not cf_token:
                res["val-format"] = {"status": "warning", "message": "Subdomain only — Cloudflare needed to resolve"}
                res["val-cf"] = {"status": "error", "message": "Cloudflare not configured"}
            else:
                res["val-format"] = {"status": "checking", "message": "Will resolve via Cloudflare…"}
                res["val-cf"] = {"status": "success", "message": "Cloudflare configured"}
        else:
            # Full FQDN provided
            from app.routes.routes import HOSTNAME_RE
            if HOSTNAME_RE.match(hostname):
                res["val-format"] = {"status": "success", "message": "Valid FQDN format"}
                res["val-cf"] = {"status": "neutral", "message": "Not needed (full FQDN provided)"}
            else:
                res["val-format"] = {"status": "error", "message": "Invalid FQDN format"}

        # DNS record check (only for non-default, non-docker routes)
        if hostname and "." in hostname:
            # Check if hostname already exists in DB
            from app.db.database import get_db
            with get_db() as con:
                existing = con.execute(
                    "SELECT id FROM routes WHERE hostname=? AND id!=?",
                    (hostname, int(route_id) if route_id else -1),
                ).fetchone()
                if existing:
                    res["val-dns"] = {"status": "error", "message": "Hostname already exists in database"}
                else:
                    # Check Cloudflare for conflicts
                    cf_token, _, _ = await cloudflare.get_cf_config()
                    if cf_token:
                        zone_id, err = await cloudflare.cf_get_zone_id()
                        if not err:
                            data, err = await cloudflare.cf_request("get", f"/zones/{zone_id}/dns_records", params={"type": "A", "name": hostname})
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
                healthy, latency = await asyncio.to_thread(tcp_check, host, port, 2.0)
                if healthy:
                    res["val-backend"] = {"status": "success", "message": f"Reachable ({latency}ms)"}
                else:
                    res["val-backend"] = {"status": "error", "message": "Backend unreachable (TCP connection failed)"}
            else:
                res["val-backend"] = {"status": "error", "message": "Invalid backend format (host:port)"}
        except Exception:
            res["val-backend"] = {"status": "error", "message": "Backend check failed"}
    else:
        res["val-backend"] = {"status": "error", "message": "Backend is required"}

    return {"success": True, **res}
