"""LLM 三档连通性冒烟测试（在服务器 app 容器内运行）。

用法: docker compose exec app python scripts/llm_smoke.py
"""

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openai import AsyncOpenAI

from app.config import get_settings

TINY_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def deepseek_client(settings) -> AsyncOpenAI:
    return AsyncOpenAI(base_url=settings.deepseek_base_url, api_key=settings.deepseek_api_key)


def high_client(settings) -> AsyncOpenAI:
    return AsyncOpenAI(base_url=settings.high_base_url, api_key=settings.high_api_key)


async def try_chat(client: AsyncOpenAI, model: str, label: str) -> None:
    start = time.monotonic()
    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "回复两个字：正常"}],
            max_tokens=16,
        )
        ms = int((time.monotonic() - start) * 1000)
        usage = resp.usage
        print(f"[OK ] {label} model={model} {ms}ms 回复={resp.choices[0].message.content!r} "
              f"tokens={usage.prompt_tokens}/{usage.completion_tokens}")
    except Exception as e:  # noqa: BLE001
        print(f"[FAIL] {label} model={model}: {type(e).__name__}: {e}")


async def try_vision(client: AsyncOpenAI, model: str) -> None:
    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "这张图是什么颜色？一个词回答。"},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{TINY_PNG}"},
                        },
                    ],
                }
            ],
            max_tokens=16,
        )
        print(f"[OK ] vision model={model} 回复={resp.choices[0].message.content!r}")
    except Exception as e:  # noqa: BLE001
        print(f"[FAIL] vision model={model}: {type(e).__name__}: {e}")


async def main() -> None:
    settings = get_settings()
    ds = deepseek_client(settings)
    hi = high_client(settings)

    print("== 高档代理可用模型（前 30 个） ==")
    try:
        models = [m.id async for m in hi.models.list()]
        for mid in models[:30]:
            print("  -", mid)
    except Exception as e:  # noqa: BLE001
        print(f"[FAIL] models.list: {type(e).__name__}: {e}")

    print("== 三档连通性 ==")
    await try_chat(ds, settings.model_low, "低档")
    await try_chat(ds, settings.model_medium, "中档")
    await try_chat(hi, settings.model_high, "高档")

    print("== 视觉能力 ==")
    await try_vision(hi, settings.model_vision or settings.model_high)


if __name__ == "__main__":
    asyncio.run(main())
