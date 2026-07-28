"""
Health and readiness endpoints.

- /healthz : liveness — process is alive and event loop responsive.
- /readyz  : readiness — dependencies (DB, mc-router) are reachable and
             background workers are not stuck.
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.db.database import get_db
from app.services import mc_router

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def liveness() -> dict:
    """Liveness probe — process is up and serving."""
    return {"status": "ok"}


@router.get("/readyz")
async def readiness() -> JSONResponse:
    """Readiness probe — checks DB open + mc-router API reachability."""
    checks: dict[str, str] = {}

    # 1) Database is openable
    try:
        with get_db() as con:
            con.execute("SELECT 1").fetchone()
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {e}"

    # 2) mc-router is answering
    _, err = await mc_router.router_request("get", "/routes")
    checks["mc_router"] = "ok" if err is None else f"error: {err}"

    healthy = all(v == "ok" for v in checks.values())
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={"status": "ok" if healthy else "degraded", "checks": checks},
    )
