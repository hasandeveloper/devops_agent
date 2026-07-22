from sqlalchemy import text
from sqlalchemy.orm import Session


def check_health(db: Session) -> dict:
    db.execute(text("SELECT 1"))
    return {"status": "ok"}
