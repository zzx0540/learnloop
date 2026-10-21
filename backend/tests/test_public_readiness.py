import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.agents import runtime as runtime_module
from app.agents.runtime import AgentRuntime, LlmOutputError, Tier
from app.api.admin import llm_costs
from app.config import Settings
from app.models import AppSetting, LlmCallLog
from tests.safety import require_test_database


@pytest.mark.parametrize(
    "url,confirmation",
    [
        ("postgresql+asyncpg://learnloop_test@127.0.0.1/learnloop", "1"),
        ("postgresql+asyncpg://learnloop_test@production/learnloop_test", "1"),
        ("postgresql+asyncpg://admin@127.0.0.1/learnloop_test", "1"),
        ("postgresql+asyncpg://learnloop_test@127.0.0.1/learnloop_test?host=production", "1"),
        ("postgresql+asyncpg://learnloop_test@127.0.0.1/learnloop_test", ""),
    ],
)
def test_database_reset_refuses_unsafe_target(url, confirmation):
    with pytest.raises(RuntimeError):
        require_test_database(url, confirmation)


def test_demo_and_unconfigured_model_never_create_client():
    for settings in (
        Settings(_env_file=None, demo_mode=True),
        Settings(_env_file=None, deepseek_api_key="", model_low=""),
    ):
        with pytest.raises(LlmOutputError):
            AgentRuntime(settings)._client_for(Tier.low)


def test_one_provider_can_supply_all_model_tiers():
    runtime = AgentRuntime(Settings(_env_file=None, model_low="user-selected-model", high_base_url=""))
    assert all(runtime._model_for(tier) == "user-selected-model" for tier in Tier)


async def test_unknown_cost_is_not_reported_as_free(db, monkeypatch):
    monkeypatch.setattr(runtime_module, "SessionLocal", async_sessionmaker(db.bind, expire_on_commit=False))
    runtime = AgentRuntime.__new__(AgentRuntime)
    assert await runtime._cost("unknown-model", 100, 0, 50) == -1
    db.add(AppSetting(key="llm_pricing", value={"v": {"priced-model": {"miss": 1, "hit": 0, "out": 2}}}))
    await db.commit()
    assert await runtime._cost("priced-model", 1000000, 0, 500000) == 2
    db.add_all(
        [
            LlmCallLog(agent="test", operation="test", model="unknown-model", tier="low", cost_estimate=-1),
            LlmCallLog(agent="test", operation="test", model="priced-model", tier="low", cost_estimate=2),
        ]
    )
    await db.commit()
    report = await llm_costs(db=db)
    assert report["total_cost_usd"] is None
    assert report["known_cost_usd"] == 2
    assert report["unpriced_calls"] == 1
    assert next(i for i in report["items"] if i["model"] == "unknown-model")["cost_usd"] is None


def test_pdf_parser_releases_source_file(tmp_path):
    import uuid

    import pymupdf

    from app.services.parsing import _parse_pdf

    path = tmp_path / "synthetic.pdf"
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text((72, 72), "LearnLoop original sample: SELECT chooses columns.")
        doc.save(path)
    parsed = _parse_pdf(path, uuid.uuid4(), uuid.uuid4())
    assert "SELECT" in parsed.markdown
    assert parsed.page_count == 1
    path.rename(tmp_path / "renamed.pdf")
