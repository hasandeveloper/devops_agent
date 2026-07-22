from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.controllers import webhooks as webhooks_controller
from routers.deps import get_db

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/cloudwatch")
async def cloudwatch_webhook(request: Request, db: Session = Depends(get_db)):
    return await webhooks_controller.handle_cloudwatch_webhook(request, db)


@router.post("/github")
async def github_webhook(request: Request, db: Session = Depends(get_db)):
    return await webhooks_controller.handle_github_webhook(request, db)
