import json
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from dotenv import load_dotenv
from httpx import ASGITransport, AsyncClient
from svix.webhooks import Webhook

TEST_SECRET = "whsec_MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw"

# Must run before app.config is imported anywhere, since Settings is cached.
# load_dotenv only fills gaps, so a TEST_DATABASE_URL already exported wins over .env.
load_dotenv()
os.environ.setdefault(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://root:root@localhost:5432/account_service_test",
)
os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
os.environ["CLERK_WEBHOOK_SECRET"] = TEST_SECRET
os.environ["ENV"] = "test"

from app.config import get_settings  # noqa: E402
from app.db import dispose_engine, get_engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _clear_settings_cache() -> None:
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
async def _schema() -> AsyncIterator[None]:
    """Rebuild the schema per test so ordering guards start from a known state."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    await dispose_engine()


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as async_client:
        yield async_client


def clerk_user_payload(
    clerk_id: str = "user_test_1",
    event_type: str = "user.created",
    updated_at: datetime | None = None,
    email: str | None = None,
    first_name: str = "Ada",
) -> dict[str, Any]:
    moment = updated_at or datetime.now(UTC)
    millis = int(moment.timestamp() * 1000)
    return {
        "type": event_type,
        "object": "event",
        "data": {
            "id": clerk_id,
            "object": "user",
            "email_addresses": [
                {
                    "id": "idn_test_1",
                    "email_address": email or f"{clerk_id}@example.com",
                    "verification": {"status": "verified"},
                }
            ],
            "primary_email_address_id": "idn_test_1",
            "first_name": first_name,
            "last_name": "Lovelace",
            "image_url": "https://img.clerk.com/placeholder.png",
            "created_at": millis,
            "updated_at": millis,
            "last_sign_in_at": millis,
        },
    }


def deleted_payload(clerk_id: str = "user_test_1") -> dict[str, Any]:
    return {
        "type": "user.deleted",
        "object": "event",
        "data": {"id": clerk_id, "deleted": True, "object": "user"},
    }


def signed_headers(
    body: str,
    svix_id: str | None = None,
    secret: str = TEST_SECRET,
    signed_at: datetime | None = None,
) -> dict[str, str]:
    msg_id = svix_id or f"msg_{uuid.uuid4().hex[:16]}"
    moment = signed_at or datetime.now(UTC)
    return {
        "content-type": "application/json",
        "svix-id": msg_id,
        "svix-timestamp": str(int(moment.timestamp())),
        "svix-signature": Webhook(secret).sign(msg_id, moment, body),
    }


async def post_event(
    client: AsyncClient,
    payload: dict[str, Any],
    svix_id: str | None = None,
    secret: str = TEST_SECRET,
    signed_at: datetime | None = None,
    tamper: bool = False,
):
    body = json.dumps(payload)
    headers = signed_headers(body, svix_id=svix_id, secret=secret, signed_at=signed_at)
    if tamper:
        body = json.dumps({**payload, "data": {**payload["data"], "id": "user_tampered"}})
    return await client.post("/webhooks/clerk", content=body, headers=headers)


def minutes_ago(minutes: float) -> datetime:
    return datetime.now(UTC) - timedelta(minutes=minutes)
