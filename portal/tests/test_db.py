import pytest

from portal import db


@pytest.mark.asyncio
async def test_create_and_lookup_user(client):
    user = await db.create_user("Alice@Example.com")
    assert user.email == "alice@example.com"
    assert user.is_active is True
    assert len(user.sign_in_token) >= 40
    assert user.sign_in_token != user.container_token

    by_id = await db.get_user_by_id(user.id)
    assert by_id is not None and by_id.email == "alice@example.com"

    by_sign_in = await db.get_active_user_by_sign_in_token(user.sign_in_token)
    assert by_sign_in is not None and by_sign_in.id == user.id

    by_container = await db.get_active_user_by_container_token(user.container_token)
    assert by_container is not None and by_container.id == user.id


@pytest.mark.asyncio
async def test_deactivated_user_not_returned_by_token_lookups(client):
    user = await db.create_user("bob@example.com")
    await db.deactivate_user(user.id)

    assert await db.get_active_user_by_sign_in_token(user.sign_in_token) is None
    assert await db.get_active_user_by_container_token(user.container_token) is None

    by_id = await db.get_user_by_id(user.id)
    assert by_id is not None and by_id.is_active is False


@pytest.mark.asyncio
async def test_regenerate_tokens_invalidates_old(client):
    user = await db.create_user("carol@example.com")
    old_sign_in = user.sign_in_token

    new_sign_in = await db.regenerate_sign_in_token(user.id)
    assert new_sign_in != old_sign_in
    assert await db.get_active_user_by_sign_in_token(old_sign_in) is None
    assert await db.get_active_user_by_sign_in_token(new_sign_in) is not None


@pytest.mark.asyncio
async def test_create_user_mints_client_token(client):
    user = await db.create_user("ct@example.com")
    assert user.client_token
    found = await db.get_active_user_by_client_token(user.client_token)
    assert found is not None
    assert found.id == user.id


@pytest.mark.asyncio
async def test_regenerate_client_token_rotates(client):
    user = await db.create_user("ct2@example.com")
    new_token = await db.regenerate_client_token(user.id)
    assert new_token != user.client_token
    assert await db.get_active_user_by_client_token(user.client_token) is None
    refetched = await db.get_active_user_by_client_token(new_token)
    assert refetched.id == user.id


@pytest.mark.asyncio
async def test_client_token_rejected_when_inactive(client):
    user = await db.create_user("ct3@example.com")
    await db.deactivate_user(user.id)
    assert await db.get_active_user_by_client_token(user.client_token) is None
