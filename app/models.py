from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, String, false, func, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    """Local mirror of a Clerk user.

    Clerk is the system of record; every column below is either copied from a webhook
    payload or bookkeeping owned by this service. Nothing here is authoritative.
    """

    __tablename__ = "users"

    clerk_id: Mapped[str] = mapped_column(String(255), primary_key=True)

    # Nullable: Clerk allows phone-only users, and the user.deleted payload carries no email.
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    email_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    primary_email_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    first_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    last_sign_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    clerk_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Clerk's own updated_at. Drives the out-of-order guard in services/clerk_sync.py,
    # and is deliberately distinct from updated_at below (our row write time).
    clerk_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # Partial, so a deleted user's address can be reused by a new signup.
        Index(
            "uq_users_email_active",
            "email",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


class WebhookEvent(Base):
    """One row per Svix message id we have already committed.

    Written in the same transaction as the user mutation, so a crash mid-processing
    rolls both back and Clerk's retry is reprocessed cleanly.
    """

    __tablename__ = "webhook_events"

    svix_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    clerk_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
