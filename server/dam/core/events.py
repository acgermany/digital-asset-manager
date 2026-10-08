from typing import Optional

from sqlalchemy.orm import Session

from ..models import Event


def log(session: Session, action: str, actor: str = "system", asset_id: Optional[int] = None,
        ingest_id: Optional[int] = None, **details) -> Event:
    event = Event(action=action, actor=actor, asset_id=asset_id, ingest_id=ingest_id, details=details)
    session.add(event)
    return event
