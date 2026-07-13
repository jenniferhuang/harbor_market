from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings


def build_engine(settings: Settings) -> Engine:
    database_url = settings.database_url.get_secret_value()
    backend = make_url(database_url).get_backend_name()
    options: dict[str, object] = {"pool_pre_ping": True}

    if backend == "sqlite":
        options["connect_args"] = {"check_same_thread": False}
    else:
        options.update(
            {
                "pool_size": settings.database_pool_size,
                "max_overflow": settings.database_max_overflow,
                "connect_args": {
                    "connect_timeout": settings.database_connect_timeout_seconds,
                },
            }
        )

    return create_engine(database_url, **options)


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
