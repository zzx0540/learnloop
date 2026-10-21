"""AgentRuntime：全部 LLM 调用的唯一入口。

- 分档路由 low/medium/high/vision（模型与端点全部来自配置）
- 提示词缓存友好：稳定块进 system（字节级稳定命中 DeepSeek 前缀缓存），易变内容进 user
- 结构化输出：Pydantic 校验，失败做一次低档修复调用
- 每课程每日 token 预算护栏；全量调用写 llm_call_logs
"""

import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from enum import Enum
from zoneinfo import ZoneInfo

from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select

from app.config import Settings, get_settings
from app.db import SessionLocal
from app.models import Course, LlmCallLog, User
from app.services.settings_store import get_setting

logger = logging.getLogger("learnloop.agents")


class Tier(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    vision = "vision"


class BudgetExceeded(Exception):
    def __init__(self, course_id: uuid.UUID):
        super().__init__(f"课程 {course_id} 今日 AI 额度已用完")
        self.course_id = course_id


class LlmOutputError(Exception):
    pass


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        first_nl = text.find("\n")
        if first_nl != -1:
            text = text[first_nl + 1 :]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    return text.strip()


class AgentRuntime:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._clients: dict[str, AsyncOpenAI] = {}

    def _model_for(self, tier: Tier) -> str:
        s = self.settings
        return {
            Tier.low: s.model_low,
            Tier.medium: s.model_medium,
            Tier.high: s.model_high,
            Tier.vision: s.model_vision or s.model_high,
        }[tier]

    def _client_for(self, tier: Tier) -> AsyncOpenAI:
        s = self.settings
        if tier in (Tier.low, Tier.medium):
            base, key = s.deepseek_base_url, s.deepseek_api_key
        else:
            base, key = s.high_base_url, s.high_api_key
        if base not in self._clients:
            self._clients[base] = AsyncOpenAI(base_url=base, api_key=key, timeout=180, max_retries=2)
        return self._clients[base]

    async def _check_budget(self, course_id: uuid.UUID | None) -> None:
        if course_id is None:
            return
        async with SessionLocal() as db:
            owner_role = (
                await db.execute(
                    select(User.role)
                    .join(Course, Course.user_id == User.id)
                    .where(Course.id == course_id)
                )
            ).scalar_one_or_none()
            if owner_role == "admin":
                return
            budget = await get_setting(db, "llm_daily_token_budget_per_course")
            day_start = (
                datetime.now(ZoneInfo("Asia/Shanghai"))
                .replace(hour=0, minute=0, second=0, microsecond=0)
                .astimezone(timezone.utc)
            )
            used = (
                await db.execute(
                    select(
                        func.coalesce(func.sum(LlmCallLog.prompt_tokens + LlmCallLog.completion_tokens), 0)
                    ).where(LlmCallLog.course_id == course_id, LlmCallLog.created_at >= day_start)
                )
            ).scalar_one()
            if used >= int(budget):
                raise BudgetExceeded(course_id)

    @staticmethod
    def _extract_usage(usage) -> tuple[int, int, int]:
        """返回 (prompt_tokens, cached_tokens, completion_tokens)，兼容 DeepSeek 与 OpenAI 风格。"""
        if usage is None:
            return 0, 0, 0
        prompt = getattr(usage, "prompt_tokens", 0) or 0
        completion = getattr(usage, "completion_tokens", 0) or 0
        cached = getattr(usage, "prompt_cache_hit_tokens", None)
        if cached is None:
            details = getattr(usage, "prompt_tokens_details", None)
            cached = getattr(details, "cached_tokens", 0) if details is not None else 0
        return int(prompt), int(cached or 0), int(completion)

    async def _cost(self, model: str, prompt: int, cached: int, completion: int) -> float:
        async with SessionLocal() as db:
            pricing = await get_setting(db, "llm_pricing") or {}
        p = pricing.get(model)
        if not p:
            return 0.0
        miss = max(prompt - cached, 0)
        return round(
            (miss * p["miss"] + cached * p["hit"] + completion * p["out"]) / 1_000_000, 6
        )

    async def _log(self, **kw) -> None:
        try:
            async with SessionLocal() as db:
                db.add(LlmCallLog(**kw))
                await db.commit()
        except Exception:  # noqa: BLE001
            logger.exception("llm_call_logs 写入失败")

    MAX_TOKENS_CEILING = 32768

    async def _chat(
        self,
        *,
        tier: Tier,
        messages: list[dict],
        max_tokens: int,
        temperature: float,
        json_mode: bool,
        agent: str,
        operation: str,
        course_id: uuid.UUID | None,
        job_id: uuid.UUID | None,
        chat_message_id: uuid.UUID | None,
        thinking: bool = False,
        web_search: bool = False,
    ) -> str:
        await self._check_budget(course_id)
        client = self._client_for(tier)
        model = self._model_for(tier)
        kwargs: dict = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tier in (Tier.low, Tier.medium):
            # DeepSeek 思考模式默认开启；转写型任务显式关闭，避免耗尽输出额度
            kwargs["extra_body"] = {"thinking": {"type": "enabled" if thinking else "disabled"}}
        if web_search:
            kwargs.setdefault("extra_body", {})["web_search_options"] = {}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        async def _call_once() -> object:
            start = time.monotonic()
            try:
                return await client.chat.completions.create(**kwargs), start
            except Exception as e:
                if json_mode and "response_format" in kwargs:
                    # 个别代理不支持 response_format，去掉后重试一次
                    kwargs.pop("response_format", None)
                    return await client.chat.completions.create(**kwargs), start
                raise e

        try:
            resp, start = await _call_once()
            # 输出被 max_tokens 截断 → 升额一次重试（截断的 JSON 无法修复，重试更可靠）
            finish = resp.choices[0].finish_reason
            if finish == "length" and max_tokens < self.MAX_TOKENS_CEILING:
                kwargs["max_tokens"] = min(max_tokens * 2, self.MAX_TOKENS_CEILING)
                logger.warning(
                    "%s/%s 输出截断（max_tokens=%d），升额至 %d 重试",
                    agent, operation, max_tokens, kwargs["max_tokens"],
                )
                resp, start = await _call_once()
        except Exception as e:
            await self._log(
                agent=agent, operation=operation, model=model, tier=tier.value,
                course_id=course_id, job_id=job_id, chat_message_id=chat_message_id,
                status="error", error=str(e)[:2000], latency_ms=0,
            )
            raise
        latency_ms = int((time.monotonic() - start) * 1000)
        prompt_t, cached_t, completion_t = self._extract_usage(resp.usage)
        cost = await self._cost(model, prompt_t, cached_t, completion_t)
        await self._log(
            agent=agent, operation=operation, model=model, tier=tier.value,
            course_id=course_id, job_id=job_id, chat_message_id=chat_message_id,
            prompt_tokens=prompt_t, cached_tokens=cached_t, completion_tokens=completion_t,
            cost_estimate=cost, latency_ms=latency_ms, status="ok",
        )
        content = resp.choices[0].message.content or ""
        if not content.strip():
            raise LlmOutputError(
                f"{model} 返回空内容（finish_reason={resp.choices[0].finish_reason}, "
                f"max_tokens={kwargs['max_tokens']}）"
            )
        return content

    async def run_structured[T: BaseModel](
        self,
        *,
        agent: str,
        operation: str,
        tier: Tier,
        response_model: type[T],
        system_prompt: str,
        stable_blocks: list[str] | None = None,
        volatile: str,
        course_id: uuid.UUID | None = None,
        job_id: uuid.UUID | None = None,
        chat_message_id: uuid.UUID | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.3,
        thinking: bool = False,
        web_search: bool = False,
    ) -> T:
        """结构化调用：稳定块进 system（吃前缀缓存），易变内容进 user，输出经 Pydantic 校验。"""
        schema_block = (
            "# 输出 JSON Schema（字段名必须完全一致）\n"
            + json.dumps(response_model.model_json_schema(), ensure_ascii=False)
        )
        system = system_prompt + "\n\n" + schema_block
        if stable_blocks:
            system = system + "\n\n" + "\n\n".join(stable_blocks)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": volatile},
        ]
        raw = await self._chat(
            tier=tier, messages=messages, max_tokens=max_tokens, temperature=temperature,
            json_mode=True, agent=agent, operation=operation,
            course_id=course_id, job_id=job_id, chat_message_id=chat_message_id,
            thinking=thinking,
            web_search=web_search,
        )
        try:
            return response_model.model_validate_json(_strip_fences(raw))
        except ValidationError as first_error:
            logger.warning("%s/%s JSON 校验失败，尝试修复: %s", agent, operation, first_error)
            repaired = await self._chat(
                tier=Tier.low,
                messages=[
                    {
                        "role": "system",
                        "content": "你是 JSON 修复器。把用户给出的内容修复为符合 JSON Schema 的合法 JSON，"
                        "只输出 JSON 本身，不要任何解释。",
                    },
                    {
                        "role": "user",
                        "content": (
                            "JSON Schema:\n"
                            f"{json.dumps(response_model.model_json_schema(), ensure_ascii=False)}\n\n"
                            f"校验错误:\n{first_error}\n\n待修复内容:\n{raw[:12000]}"
                        ),
                    },
                ],
                max_tokens=max_tokens,
                temperature=0,
                json_mode=True,
                agent=agent,
                operation=f"{operation}.repair",
                course_id=course_id,
                job_id=job_id,
                chat_message_id=None,
            )
            try:
                return response_model.model_validate_json(_strip_fences(repaired))
            except ValidationError as e:
                raise LlmOutputError(f"JSON 修复后仍不合法: {e}") from first_error

    async def run_text(
        self,
        *,
        agent: str,
        operation: str,
        tier: Tier,
        system_prompt: str,
        stable_blocks: list[str] | None = None,
        volatile: str,
        course_id: uuid.UUID | None = None,
        job_id: uuid.UUID | None = None,
        chat_message_id: uuid.UUID | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.5,
        thinking: bool = False,
    ) -> str:
        system = system_prompt
        if stable_blocks:
            system = system + "\n\n" + "\n\n".join(stable_blocks)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": volatile},
        ]
        return await self._chat(
            tier=tier, messages=messages, max_tokens=max_tokens, temperature=temperature,
            json_mode=False, agent=agent, operation=operation,
            course_id=course_id, job_id=job_id, chat_message_id=chat_message_id,
            thinking=thinking,
        )

    async def run_stream(
        self,
        *,
        agent: str,
        operation: str,
        tier: Tier,
        system_prompt: str,
        stable_blocks: list[str] | None,
        messages: list[dict],
        course_id: uuid.UUID | None,
        chat_message_id: uuid.UUID | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.6,
        thinking: bool = False,
    ) -> AsyncIterator[str]:
        await self._check_budget(course_id)
        client = self._client_for(tier)
        model = self._model_for(tier)
        system = system_prompt
        if stable_blocks:
            system = system + "\n\n" + "\n\n".join(stable_blocks)
        request_messages = [{"role": "system", "content": system}, *messages]
        kwargs: dict = {
            "model": model,
            "messages": request_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tier in (Tier.low, Tier.medium):
            kwargs["extra_body"] = {"thinking": {"type": "enabled" if thinking else "disabled"}}

        start = time.monotonic()
        usage = None
        try:
            stream = await client.chat.completions.create(**kwargs)
            async for chunk in stream:
                if chunk.usage is not None:
                    usage = chunk.usage
                if not chunk.choices:
                    continue
                content = chunk.choices[0].delta.content or ""
                if content:
                    yield content
        except Exception as e:
            await self._log(
                agent=agent,
                operation=operation,
                model=model,
                tier=tier.value,
                course_id=course_id,
                job_id=None,
                chat_message_id=chat_message_id,
                status="error",
                error=str(e)[:2000],
                latency_ms=int((time.monotonic() - start) * 1000),
            )
            raise

        latency_ms = int((time.monotonic() - start) * 1000)
        prompt_t, cached_t, completion_t = self._extract_usage(usage)
        cost = await self._cost(model, prompt_t, cached_t, completion_t)
        await self._log(
            agent=agent,
            operation=operation,
            model=model,
            tier=tier.value,
            course_id=course_id,
            job_id=None,
            chat_message_id=chat_message_id,
            prompt_tokens=prompt_t,
            cached_tokens=cached_t,
            completion_tokens=completion_t,
            cost_estimate=cost,
            latency_ms=latency_ms,
            status="ok",
        )


runtime = AgentRuntime()
