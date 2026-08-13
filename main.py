from contextlib import asynccontextmanager

from fastapi import FastAPI

from config.logging import attach_uvicorn_file_logging
from routers import health, webhooks


@asynccontextmanager
async def lifespan(app: FastAPI):
    attach_uvicorn_file_logging()
    yield


app = FastAPI(title="devops-agent", lifespan=lifespan)

app.include_router(health.router)
app.include_router(webhooks.router)
