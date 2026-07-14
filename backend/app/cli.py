from __future__ import annotations

import argparse

from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import build_engine, build_session_factory
from app.models import User
from app.services.object_cleanup import (
    recover_stale_import_jobs,
    retryable_cleanup_jobs,
    run_object_cleanup_jobs,
)
from app.services.object_storage import build_object_storage


def set_admin(username: str, *, enabled: bool) -> int:
    normalized = username.strip().casefold()
    engine = build_engine(get_settings())
    session_factory = build_session_factory(engine)
    try:
        with session_factory() as session:
            user = session.scalar(select(User).where(User.username == normalized))
            if user is None:
                print(f"User '{normalized}' was not found")
                return 1
            user.is_admin = enabled
            session.commit()
            action = "promoted to administrator" if enabled else "removed from administrators"
            print(f"User '{normalized}' {action}")
            return 0
    finally:
        engine.dispose()


def retry_cleanup(*, limit: int) -> int:
    settings = get_settings()
    engine = build_engine(settings)
    session_factory = build_session_factory(engine)
    try:
        storage = build_object_storage(settings)
        with session_factory() as session:
            stale_imports = recover_stale_import_jobs(session, limit=limit)
            jobs = retryable_cleanup_jobs(session, limit=limit)
            failed = run_object_cleanup_jobs(session, storage, jobs)
            print(
                f"Recovered {len(stale_imports)} stale import job(s); "
                f"processed {len(jobs)} cleanup job(s); {len(failed)} remain failed"
            )
            return 1 if failed else 0
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description="Harbor Market operator commands")
    subparsers = parser.add_subparsers(dest="command", required=True)
    promote = subparsers.add_parser("promote-admin", help="Grant administrator permission")
    promote.add_argument("username")
    demote = subparsers.add_parser("demote-admin", help="Remove administrator permission")
    demote.add_argument("username")
    cleanup = subparsers.add_parser(
        "retry-object-cleanup",
        help="Retry pending or failed object-storage cleanup jobs",
    )
    cleanup.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    if args.command == "retry-object-cleanup":
        if args.limit < 1 or args.limit > 1_000:
            parser.error("--limit must be between 1 and 1000")
        return retry_cleanup(limit=args.limit)
    return set_admin(args.username, enabled=args.command == "promote-admin")


if __name__ == "__main__":
    raise SystemExit(main())
