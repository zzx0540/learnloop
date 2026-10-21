import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import app.services.companion  # noqa: F401  注册任务处理器
import app.services.examiner  # noqa: F401  注册任务处理器
import app.services.grading  # noqa: F401  注册任务处理器
import app.services.ingest  # noqa: F401  注册任务处理器
import app.services.language_learning  # noqa: F401  注册任务处理器
import app.services.market  # noqa: F401  注册任务处理器
import app.services.planner  # noqa: F401  注册任务处理器
import app.services.vocab  # noqa: F401  注册任务处理器
import app.services.wiki_lint  # noqa: F401  注册任务处理器
from app.api import api_router
from app.api.legal import router as legal_router
from app.bootstrap import bootstrap
from app.config import get_settings
from app.db import SessionLocal
from app.jobs.worker import cron_loop, recover_on_startup, start_workers, sweeper_loop

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if get_settings().demo_mode:
        from app.demo import seed_demo

        async with SessionLocal() as db:
            await seed_demo(db)
        yield
        return
    async with SessionLocal() as db:
        await bootstrap(db)
    await recover_on_startup()
    tasks = start_workers()
    tasks.append(asyncio.create_task(sweeper_loop(), name="job-sweeper"))
    tasks.append(asyncio.create_task(cron_loop(), name="job-cron"))
    yield
    for t in tasks:
        t.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.gather(*tasks)


app = FastAPI(
    title="学习环 LearnLoop",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

app.include_router(api_router, prefix="/api/v1")
app.include_router(legal_router)


@app.middleware("http")
async def demo_write_guard(request: Request, call_next):
    if (
        get_settings().demo_mode
        and request.method not in {"GET", "HEAD", "OPTIONS"}
        and request.url.path not in {"/api/v1/auth/login", "/api/v1/auth/logout"}
    ):
        return JSONResponse(
            status_code=403,
            content={"detail": "当前为预置只读演示，生成、上传和保存已关闭。请按 README 创建自己的实例。"},
        )
    return await call_next(request)


@app.get("/api/info")
async def public_info():
    return {"demo_mode": get_settings().demo_mode}


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logging.getLogger("learnloop.api").error(
        "未捕获异常 %s %s",
        request.method,
        request.url.path,
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    return JSONResponse(status_code=500, content={"detail": "服务器开小差了，请稍后再试"})


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}


_static_dir = Path(get_settings().static_dir)
if _static_dir.is_dir():
    assets_dir = _static_dir / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str) -> FileResponse:
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404)
        candidate = _static_dir / full_path
        if full_path and ".." not in full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_static_dir / "index.html")
