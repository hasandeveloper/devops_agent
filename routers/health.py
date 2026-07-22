from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.controllers.health import check_health
from routers.deps import get_db

# tags=["health"] is just a heading  for below endpoints in the docs, it is not a path prefix. The path prefix is defined in main.py when including the router.
router = APIRouter(tags=["health"])


@router.get("/health")
def health(db: Session = Depends(get_db)):
    return check_health(db)
