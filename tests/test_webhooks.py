from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.db import get_sessionmaker
from app.models import User, WebhookEvent
from tests.conftest import (
    clerk_user_payload,
    deleted_payload,
    minutes_ago,
    post_event,
)


async def _count(model, *where):
    async with get_sessionmaker()() as session:
        stmt = select(func.count()).select_from(model)
        for clause in where:
            stmt = stmt.where(clause)
        return (await session.execute(stmt)).scalar_one()


async def _get_user(clerk_id: str) -> User | None:
    async with get_sessionmaker()() as session:
        return (
            await session.execute(select(User).where(User.clerk_id == clerk_id))
        ).scalar_one_or_none()


async def test_valid_signature_creates_user(client):
    response = await post_event(client, clerk_user_payload())
    assert response.status_code == 204

    user = await _get_user("user_test_1")
    assert user is not None
    assert user.email == "user_test_1@example.com"
    assert user.email_verified is True
    assert user.first_name == "Ada"
    assert user.deleted_at is None


async def test_tampered_body_is_rejected(client):
    response = await post_event(client, clerk_user_payload(), tamper=True)

    assert response.status_code == 400
    assert await _count(User) == 0
    assert await _count(WebhookEvent) == 0


async def test_wrong_secret_is_rejected(client):
    response = await post_event(
        client, clerk_user_payload(), secret="whsec_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    )

    assert response.status_code == 400
    assert await _count(User) == 0


async def test_missing_signature_headers_are_rejected(client):
    response = await client.post(
        "/webhooks/clerk",
        content='{"type":"user.created","data":{"id":"user_x"}}',
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 400
    assert await _count(User) == 0


async def test_timestamp_outside_tolerance_is_rejected(client):
    """Svix enforces a ±5 minute window, which bounds the replay surface."""
    stale = datetime.now(UTC) - timedelta(minutes=30)
    response = await post_event(client, clerk_user_payload(), signed_at=stale)

    assert response.status_code == 400
    assert await _count(User) == 0


async def test_replayed_svix_id_is_a_noop(client):
    first = await post_event(client, clerk_user_payload(first_name="Ada"), svix_id="msg_replay")
    assert first.status_code == 204

    # Same message id, different content: the claim must reject it before any write.
    second = await post_event(
        client, clerk_user_payload(first_name="Changed"), svix_id="msg_replay"
    )
    assert second.status_code == 204

    assert await _count(WebhookEvent) == 1
    user = await _get_user("user_test_1")
    assert user.first_name == "Ada"


async def test_out_of_order_update_is_skipped(client):
    await post_event(client, clerk_user_payload(first_name="Current"))

    stale = clerk_user_payload(
        event_type="user.updated", first_name="Stale", updated_at=minutes_ago(60)
    )
    response = await post_event(client, stale)

    assert response.status_code == 204
    user = await _get_user("user_test_1")
    assert user.first_name == "Current"


async def test_newer_update_is_applied(client):
    await post_event(client, clerk_user_payload(first_name="Old", updated_at=minutes_ago(60)))

    fresh = clerk_user_payload(event_type="user.updated", first_name="New")
    assert (await post_event(client, fresh)).status_code == 204

    user = await _get_user("user_test_1")
    assert user.first_name == "New"


async def test_update_for_unknown_user_inserts(client):
    """user.updated can arrive before user.created on retry; both share one upsert."""
    response = await post_event(client, clerk_user_payload(event_type="user.updated"))

    assert response.status_code == 204
    assert await _get_user("user_test_1") is not None


async def test_delete_soft_deletes(client):
    await post_event(client, clerk_user_payload())

    assert (await post_event(client, deleted_payload())).status_code == 204

    user = await _get_user("user_test_1")
    assert user is not None
    assert user.deleted_at is not None


async def test_delete_for_unknown_user_is_acknowledged(client):
    response = await post_event(client, deleted_payload("user_never_seen"))

    assert response.status_code == 204
    assert await _count(User) == 0


async def test_late_update_does_not_resurrect_deleted_user(client):
    await post_event(client, clerk_user_payload())
    await post_event(client, deleted_payload())

    late = clerk_user_payload(event_type="user.updated", first_name="Resurrected")
    assert (await post_event(client, late)).status_code == 204

    user = await _get_user("user_test_1")
    assert user.deleted_at is not None
    assert user.first_name == "Ada"


async def test_email_can_be_reused_after_soft_delete(client):
    await post_event(client, clerk_user_payload("user_first"))
    await post_event(client, deleted_payload("user_first"))

    reuse = clerk_user_payload("user_second", email="user_first@example.com")
    assert (await post_event(client, reuse)).status_code == 204

    assert (await _get_user("user_second")).email == "user_first@example.com"


async def test_unhandled_event_type_is_acknowledged(client):
    payload = {"type": "session.created", "object": "event", "data": {"id": "sess_1"}}
    response = await post_event(client, payload)

    assert response.status_code == 204
    assert await _count(User) == 0


async def test_phone_only_user_without_email(client):
    payload = clerk_user_payload()
    payload["data"]["email_addresses"] = []
    payload["data"]["primary_email_address_id"] = None

    assert (await post_event(client, payload)).status_code == 204

    user = await _get_user("user_test_1")
    assert user.email is None
    assert user.email_verified is False


async def test_primary_email_selected_by_id_not_order(client):
    payload = clerk_user_payload()
    payload["data"]["email_addresses"] = [
        {"id": "idn_other", "email_address": "secondary@example.com"},
        {
            "id": "idn_primary",
            "email_address": "primary@example.com",
            "verification": {"status": "verified"},
        },
    ]
    payload["data"]["primary_email_address_id"] = "idn_primary"

    await post_event(client, payload)

    user = await _get_user("user_test_1")
    assert user.email == "primary@example.com"
    assert user.email_verified is True


async def test_unverified_email_recorded_as_unverified(client):
    payload = clerk_user_payload()
    payload["data"]["email_addresses"][0]["verification"] = {"status": "unverified"}

    await post_event(client, payload)

    assert (await _get_user("user_test_1")).email_verified is False


async def test_clerk_millisecond_timestamps_are_converted(client):
    moment = datetime(2024, 5, 31, 12, 0, 0, tzinfo=UTC)
    await post_event(client, clerk_user_payload(updated_at=moment))

    user = await _get_user("user_test_1")
    assert user.clerk_created_at == moment
    assert user.clerk_updated_at == moment
