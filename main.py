from fastapi import FastAPI

from routers import health, webhooks

app = FastAPI(title="devops-agent")

app.include_router(health.router)
app.include_router(webhooks.router)
