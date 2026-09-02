import logging
from typing import Literal

from sqlalchemy import and_, func, or_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, WebhookEvent
from app.schemas import ClerkDeletedData, ClerkEvent, ClerkUserData

logger = logging.getLogger(__name__)

HANDLED_EVENTS = frozenset({"user.created", "user.updated", "user.deleted"})

Outcome = Literal[
    "applied",
    "skipped_stale",
    "deleted",
    "already_deleted",
    "not_found",
    "ignored",
]

# Columns refreshed from a user.created / user.updated payload. deleted_at is
# absent on purpose: deletion is terminal and must not be undone by a late update.
_MIRRORED_COLUMNS = (
    "email",
    "email_verified",
    "primary_email_id",
    "first_name",
    "last_name",
    "username",
    "image_url",
    "last_sign_in_at",
    "clerk_created_at",
    "clerk_updated_at",
)


async def claim_event(
    session: AsyncSession,
    svix_id: str,
    event_type: str,
    clerk_user_id: str | None,
) -> bool:
    """Reserve this Svix message id. Returns False when it was already processed.

    Runs in the caller's transaction, so the claim commits together with the user
    mutation it guards.
    """
    stmt = (
        pg_insert(WebhookEvent)
        .values(svix_id=svix_id, event_type=event_type, clerk_user_id=clerk_user_id)
        .on_conflict_do_nothing(index_elements=[WebhookEvent.svix_id])
        .returning(WebhookEvent.svix_id)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None


async def process_event(session: AsyncSession, event: ClerkEvent) -> Outcome:
    match event.type:
        case "user.created" | "user.updated":
            return await _upsert_user(session, ClerkUserData.model_validate(event.data))
        case "user.deleted":
            return await _soft_delete_user(session, ClerkDeletedData.model_validate(event.data))
        case _:
            logger.info("ignoring unhandled clerk event type=%s", event.type)
            return "ignored"


async def _upsert_user(session: AsyncSession, data: ClerkUserData) -> Outcome:
    primary = data.primary_email

    values = {
        "clerk_id": data.id,
        "email": primary.email_address if primary else None,
        "email_verified": bool(
            primary and primary.verification and primary.verification.status == "verified"
        ),
        "primary_email_id": primary.id if primary else None,
        "first_name": data.first_name,
        "last_name": data.last_name,
        "username": data.username,
        "image_url": data.image_url,
        "last_sign_in_at": data.last_sign_in_at,
        "clerk_created_at": data.created_at,
        "clerk_updated_at": data.updated_at,
    }

    stmt = pg_insert(User).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[User.clerk_id],
        set_={col: stmt.excluded[col] for col in _MIRRORED_COLUMNS}
        # onupdate= does not fire for a core INSERT ... ON CONFLICT, so bump it here.
        | {"updated_at": func.now()},
        where=and_(
            # Deletion is terminal: a late create/update must not resurrect the row.
            User.deleted_at.is_(None),
            # Out-of-order guard. A payload without updated_at cannot be ordered, so
            # it is allowed through rather than silently dropped.
            or_(
                User.clerk_updated_at.is_(None),
                stmt.excluded.clerk_updated_at.is_(None),
                stmt.excluded.clerk_updated_at > User.clerk_updated_at,
            ),
        ),
    ).returning(User.clerk_id)

    result = await session.execute(stmt)
    if result.scalar_one_or_none() is None:
        # Conflict fired but the WHERE rejected it: stale payload, or already deleted.
        logger.info("skipped stale/terminal clerk payload for user=%s", data.id)
        return "skipped_stale"

    return "applied"


async def _soft_delete_user(session: AsyncSession, data: ClerkDeletedData) -> Outcome:
    stmt = (
        update(User)
        .where(User.clerk_id == data.id, User.deleted_at.is_(None))
        .values(deleted_at=func.now(), updated_at=func.now())
        .returning(User.clerk_id)
    )
    result = await session.execute(stmt)
    if result.scalar_one_or_none() is not None:
        return "deleted"

    # Either never mirrored (webhook missed / user predates this service) or already
    # soft-deleted. Both are terminal states, so neither is an error.
    exists = await session.get(User, data.id)
    if exists is None:
        logger.warning("user.deleted for unknown clerk_id=%s", data.id)
        return "not_found"
    return "already_deleted"
