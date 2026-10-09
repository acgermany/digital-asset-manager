from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..core import auth
from ..db import SessionLocal, init_db
from .routers import ai, assets, auth as auth_routes, config, library, processing, system, uploads, workflows

# Reachable without a session: login itself, and the container health check.
PUBLIC = ("/api/auth/", "/api/health")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    with SessionLocal() as session:
        auth.announce_setup_code(session)
    yield


app = FastAPI(title="Digital Asset Manager API", lifespan=lifespan)


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") and not path.startswith(PUBLIC):
        with SessionLocal() as session:
            if not auth.configured(session):
                return JSONResponse({"detail": "Set a password first", "auth": "setup"}, status_code=401)
            if not auth.valid_token(session, request.cookies.get(auth.COOKIE)):
                return JSONResponse({"detail": "Login required", "auth": "login"}, status_code=401)
    return await call_next(request)


for router in (auth_routes.router, system.router, config.router, library.router, workflows.router,
               ai.router, assets.router, processing.router, uploads.router):
    app.include_router(router, prefix="/api")
