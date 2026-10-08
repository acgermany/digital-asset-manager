from contextlib import asynccontextmanager

from fastapi import FastAPI

from ..db import init_db
from .routers import ai, assets, config, library, processing, system, workflows


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="Digital Asset Manager API", lifespan=lifespan)

for router in (system.router, config.router, library.router, workflows.router, ai.router, assets.router,
               processing.router):
    app.include_router(router, prefix="/api")
