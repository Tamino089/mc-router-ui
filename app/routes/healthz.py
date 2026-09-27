import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.db.database import get_db
from app.services import mc_router

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])

@router.get("/healthz")
async def liveness() -> dict:
    return {"status": "ok"}

@router.get("/readyz")
async def readiness() -> JSONResponse:
    checks: dict[str, str] = {}

    try:
        with get_db() as con:
            con.execute("SELECT 1").fetchone()
        checks["database"] = "ok"
    except Exception:
        logger.exception("Readiness check failed for the database")
        checks["database"] = "error"

    _, err = await mc_router.router_request("get", "/routes")
    if err is None:
        checks["mc_router"] = "ok"
    else:
        logger.warning("Readiness check failed for mc-router: %s", err)
        checks["mc_router"] = "error"

    healthy = all(v == "ok" for v in checks.values())
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={"status": "ok" if healthy else "degraded", "checks": checks},
    )
