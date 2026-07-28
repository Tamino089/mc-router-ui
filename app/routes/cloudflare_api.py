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
async def validate_route_live(
    request: Request,
    hostname: str,
    backend: str,
    is_default: str = "false",
    route_id: int = None,
):
    user = current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    is_def = is_default.lower() == "true"
    hostname = hostname.strip().lower()
    backend = backend.strip()

    # Auto-resolve subdomain-only hostname for validation
    if not is_def and hostname and "." not in hostname:
        resolved = await cloudflare.resolve_hostname(hostname)
        resolved_hostname = resolved
    else:
        resolved_hostname = hostname

    res = {
        "hostname_format": {"status": "neutral", "message": ""},
        "cf_zone": {"status": "neutral", "message": ""},
        "dns_record": {"status": "neutral", "message": ""},
        "backend_reachable": {"status": "neutral", "message": ""},
    }

    # 1. Hostname Format
    if is_def:
        res["hostname_format"] = {"status": "success", "message": "Default route acts as a fallback for all unmatched hostnames."}
        res["cf_zone"] = {"status": "neutral", "message": "Not applicable for default route."}
        res["dns_record"] = {"status": "neutral", "message": "Not applicable for default route."}
    else:
        if not hostname:
            res["hostname_format"] = {"status": "error", "message": "Hostname cannot be empty."}
        elif " " in hostname or not all(c.isalnum() or c in ".-" for c in hostname):
            res["hostname_format"] = {"status": "error", "message": "Hostname contains invalid characters."}
        else:
            res["hostname_format"] = {"status": "success", "message": f"Hostname format valid → will resolve to: {resolved_hostname}"}

            token_cf, _, _ = get_cf_config()
            if token_cf:
                valid, msg = await cloudflare.validate_domain(resolved_hostname, False)
                if not valid:
                    res["cf_zone"] = {"status": "error", "message": msg}
                else:
                    res["cf_zone"] = {"status": "success", "message": f"Hostname '{resolved_hostname}' matches your Cloudflare zone."}

                # DNS record check
                zone_id, zerr = await cloudflare.cf_get_zone_id()
                if not zerr and zone_id:
                    existing_rec, _ = await cloudflare.cf_find_record(zone_id, resolved_hostname)
                    if existing_rec:
                        res["dns_record"] = {"status": "success", "message": f"DNS A-record already exists ({existing_rec.get('content', '?')})"}
                    else:
                        res["dns_record"] = {"status": "warning", "message": "No DNS A-record yet — will be created on save."}
                else:
                    res["dns_record"] = {"status": "neutral", "message": "Could not check DNS records."}
            else:
                res["cf_zone"] = {"status": "warning", "message": "Cloudflare integration not configured."}
                res["dns_record"] = {"status": "neutral", "message": "Cloudflare not configured."}

    # 2. Backend Format & Reachability
    if not backend:
        res["backend_reachable"] = {"status": "error", "message": "Backend address is required."}
    else:
        parts = backend.rsplit(":", 1)
        if len(parts) != 2 or not parts[1].isdigit():
            res["backend_reachable"] = {"status": "error", "message": "Backend must be in format host:port."}
        else:
            import asyncio
            from app.services.health import tcp_check
            host, port = parts[0], int(parts[1])
            # Run blocking TCP check in a thread so we don't stall the event loop
            healthy, _ = await asyncio.to_thread(tcp_check, host, port, 3.0)
            if healthy:
                res["backend_reachable"] = {"status": "success", "message": "Backend is reachable via TCP."}
            else:
                res["backend_reachable"] = {"status": "warning", "message": "Backend is unreachable right now (TCP connect failed). You can still save — the route will retry automatically."}

    return res
