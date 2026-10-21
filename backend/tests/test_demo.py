import pytest

from app.demo import validate_demo_target


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://learnloop@localhost/learnloop",
        "postgresql+asyncpg://admin@localhost/learnloop_demo",
        "postgresql+asyncpg://learnloop_demo@localhost/learnloop_demo?host=production",
    ],
)
def test_demo_rejects_other_databases(url):
    with pytest.raises(RuntimeError):
        validate_demo_target(url)


async def test_demo_blocks_writes_and_exposes_mode(client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "demo_mode", True)
    assert (await client.get("/api/info")).json() == {"demo_mode": True}
    assert (await client.post("/api/v1/courses", json={"name": "blocked"})).status_code == 403
    assert (await client.post("/api/v1/auth/logout")).status_code == 200
