from tests.conftest import clerk_user_payload, deleted_payload, post_event


async def test_get_user_returns_profile(client):
    await post_event(client, clerk_user_payload())

    response = await client.get("/users/user_test_1")

    assert response.status_code == 200
    body = response.json()
    assert body["clerk_id"] == "user_test_1"
    assert body["email"] == "user_test_1@example.com"
    assert body["first_name"] == "Ada"
    assert body["deleted_at"] is None


async def test_get_unknown_user_is_404(client):
    assert (await client.get("/users/user_missing")).status_code == 404


async def test_deleted_user_is_404_by_default(client):
    await post_event(client, clerk_user_payload())
    await post_event(client, deleted_payload())

    assert (await client.get("/users/user_test_1")).status_code == 404


async def test_deleted_user_visible_with_include_deleted(client):
    await post_event(client, clerk_user_payload())
    await post_event(client, deleted_payload())

    response = await client.get("/users/user_test_1", params={"include_deleted": "true"})

    assert response.status_code == 200
    assert response.json()["deleted_at"] is not None


async def test_health_reports_database(client):
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "up"}
