from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _from_clerk_millis(value: int | None) -> datetime | None:
    """Clerk sends timestamps as milliseconds since the epoch, not seconds."""
    if value is None:
        return None
    return datetime.fromtimestamp(value / 1000, tz=UTC)


class ClerkEvent(BaseModel):
    """Webhook envelope. `data` stays raw so each handler parses its own shape."""

    model_config = ConfigDict(extra="ignore")

    type: str
    data: dict[str, Any]


class ClerkVerification(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: str | None = None


class ClerkEmailAddress(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    email_address: str
    verification: ClerkVerification | None = None


class ClerkUserData(BaseModel):
    """Payload of user.created / user.updated.

    Every field but `id` is optional: Clerk omits what is not set, and treating a
    missing field as a hard error would make the endpoint retry forever.
    """

    model_config = ConfigDict(extra="ignore")

    id: str
    email_addresses: list[ClerkEmailAddress] = Field(default_factory=list)
    primary_email_address_id: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    username: str | None = None
    image_url: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    last_sign_in_at: datetime | None = None

    @field_validator("created_at", "updated_at", "last_sign_in_at", mode="before")
    @classmethod
    def _millis_to_datetime(cls, value: Any) -> Any:
        if isinstance(value, int):
            return _from_clerk_millis(value)
        return value

    @property
    def primary_email(self) -> ClerkEmailAddress | None:
        """The address Clerk flagged as primary, falling back to the first on file."""
        if not self.email_addresses:
            return None
        if self.primary_email_address_id:
            for address in self.email_addresses:
                if address.id == self.primary_email_address_id:
                    return address
        return self.email_addresses[0]


class ClerkDeletedData(BaseModel):
    """Payload of user.deleted — minimal, with no email and no updated_at."""

    model_config = ConfigDict(extra="ignore")

    id: str
    deleted: bool = True


class UserRead(BaseModel):
    """What internal services get back from GET /users/{clerk_id}."""

    model_config = ConfigDict(from_attributes=True)

    clerk_id: str
    email: str | None
    email_verified: bool
    first_name: str | None
    last_name: str | None
    username: str | None
    image_url: str | None
    last_sign_in_at: datetime | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
