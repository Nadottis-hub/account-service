from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import User
from app.schemas import UserRead

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/{clerk_id}", response_model=UserRead)
async def get_user(
    clerk_id: str,
    session: AsyncSession = Depends(get_session),
    include_deleted: bool = Query(
        default=False,
        description="Return soft-deleted users, for callers resolving historical references.",
    ),
) -> User:
    """Profile lookup for other internal services.

    No authentication here by design: the gateway authenticates callers before
    anything reaches this service.
    """
    stmt = select(User).where(User.clerk_id == clerk_id)
    if not include_deleted:
        stmt = stmt.where(User.deleted_at.is_(None))

    user = (await session.execute(stmt)).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found")

    return user
