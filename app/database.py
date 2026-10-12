"""SQLAlchemy engine / session wiring."""

from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker


def create_session_factory(database_url: str) -> tuple[Engine, sessionmaker[Session]]:
    url = make_url(database_url)
    connect_args: dict = {}

    if url.get_backend_name() == "sqlite":
        connect_args["check_same_thread"] = False  # FastAPI runs sync routes in a threadpool
        if url.database and url.database != ":memory:":
            Path(url.database).parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(database_url, connect_args=connect_args, future=True)

    if url.get_backend_name() == "sqlite":

        @event.listens_for(engine, "connect")
        def _enable_sqlite_fk(dbapi_connection, _record):  # pragma: no cover - trivial
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine, sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
