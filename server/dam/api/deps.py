from sqlalchemy.orm import Session

from ..core import locations
from ..models import NetworkMount


def mounts(session: Session) -> list:
    return session.query(NetworkMount).all()


def host(session: Session, path):
    """Container path -> path the user recognises."""
    return locations.to_host(path, mounts(session))
