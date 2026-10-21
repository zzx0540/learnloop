import inspect
import io
import uuid
import zipfile
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Document, DocumentImage, Job, User
from app.services import ingest as ingest_svc
from app.services import mineru as mineru_svc
from app.services import parsing as parsing_svc
from app.services.parsing import ParseResult


async def _create_course(client: AsyncClient) -> dict:
    response = await client.post(
        "/api/v1/courses",
        json={"name": "MinerU 解析测试", "goal_text": "验证多格式文档入库"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _zip_result(*, include_markdown: bool = True) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        if include_markdown:
            archive.writestr(
                "result/full.md",
                "# 公式文档\n\n观察下图：\n\n![曲线](images/chart.png)\n\n$x^2+y^2=1$",
            )
        archive.writestr("result/images/chart.png", b"fake-png")
    return buffer.getvalue()


def _http_response(
    method: str,
    url: str,
    status_code: int = 200,
    *,
    json: dict | None = None,
    content: bytes = b"",
) -> httpx.Response:
    request = httpx.Request(method, url)
    if json is not None:
        return httpx.Response(status_code, json=json, request=request)
    return httpx.Response(status_code, content=content, request=request)


def _install_http(monkeypatch, handler):
    calls: list[tuple[str, str, dict]] = []

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def request(self, method: str, url: str, **kwargs):
            calls.append((method, url, kwargs))
            response = handler(method, url, kwargs)
            if inspect.isawaitable(response):
                response = await response
            return response

    monkeypatch.setattr(mineru_svc.httpx, "AsyncClient", FakeAsyncClient)
    return calls


def _enable_mineru(monkeypatch, api_key: str = "test-mineru-key") -> None:
    monkeypatch.setattr(
        parsing_svc,
        "get_settings",
        lambda: SimpleNamespace(
            mineru_api_key=api_key,
            mineru_base_url="https://mineru.test",
        ),
    )


async def test_mineru_success_rewrites_images_and_records_metadata(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: User,
    monkeypatch,
) -> None:
    course = await _create_course(client)
    course_id = uuid.UUID(course["id"])
    upload = await client.post(
        f"/api/v1/courses/{course['id']}/documents",
        files={"file": ("formula.pdf", b"%PDF-fake", "application/pdf")},
    )
    assert upload.status_code == 200, upload.text
    document_id = uuid.UUID(upload.json()["id"])
    polls = 0

    def handler(method: str, url: str, kwargs: dict) -> httpx.Response:
        nonlocal polls
        if method == "POST":
            body = kwargs["json"]
            assert body["model_version"] == "vlm"
            assert body["files"] == [
                {
                    "name": "formula.pdf",
                    "is_ocr": True,
                    "data_id": str(document_id),
                }
            ]
            return _http_response(
                method,
                url,
                json={
                    "code": 0,
                    "msg": "ok",
                    "data": {
                        "batch_id": "batch-1",
                        "file_urls": ["https://upload.test/signed"],
                    },
                },
            )
        if method == "PUT":
            assert url == "https://upload.test/signed"
            assert "headers" not in kwargs
            assert kwargs["content"] == b"%PDF-fake"
            return _http_response(method, url)
        if url.endswith("/api/v4/extract-results/batch/batch-1"):
            polls += 1
            if polls == 1:
                result = {
                    "data_id": str(document_id),
                    "state": "running",
                    "extract_progress": {"extracted_pages": 5, "total_pages": 12},
                }
            else:
                result = {
                    "data_id": str(document_id),
                    "state": "done",
                    "full_zip_url": "https://download.test/result.zip",
                }
            return _http_response(
                method,
                url,
                json={
                    "code": 0,
                    "msg": "ok",
                    "data": {"batch_id": "batch-1", "extract_result": [result]},
                },
            )
        assert url == "https://download.test/result.zip"
        return _http_response(method, url, content=_zip_result())

    _enable_mineru(monkeypatch)
    monkeypatch.setattr(mineru_svc, "MINERU_POLL_INTERVAL_SECONDS", 0)
    calls = _install_http(monkeypatch, handler)
    parse_job = (
        await db.execute(select(Job).where(Job.kind == "parse_document"))
    ).scalar_one()

    result = await ingest_svc.handle_parse_document(db, parse_job)

    assert result["mineru"] == "success"
    assert result["pages"] == 12
    assert result["images"] == 1
    assert result["elapsed_seconds"] >= 0
    document = await db.get(Document, document_id)
    await db.refresh(document)
    parsed = Path(document.parsed_md_path).read_text(encoding="utf-8")
    assert f"/api/v1/files/{course_id}/images/" in parsed
    assert "](images/chart.png)" not in parsed
    image = (
        await db.execute(
            select(DocumentImage).where(DocumentImage.document_id == document_id)
        )
    ).scalar_one()
    assert Path(image.stored_path).read_bytes() == b"fake-png"
    assert [method for method, _, _ in calls] == ["POST", "PUT", "GET", "GET", "GET"]


@pytest.mark.parametrize(
    ("mode", "reason_fragment"),
    [
        ("failed", "MinerU 解析失败"),
        ("timeout", "超过 20 分钟"),
        ("auth", "鉴权失败"),
        ("missing_markdown", "缺少 full.md"),
    ],
)
async def test_mineru_failures_fallback_to_local_pdf(
    tmp_path: Path,
    monkeypatch,
    mode: str,
    reason_fragment: str,
) -> None:
    document_id = uuid.uuid4()
    path = tmp_path / "fallback.pdf"
    path.write_bytes(b"%PDF-fake")
    polls = 0

    def handler(method: str, url: str, kwargs: dict) -> httpx.Response:
        nonlocal polls
        if mode == "auth" and method == "POST":
            return _http_response(method, url, 401)
        if method == "POST":
            return _http_response(
                method,
                url,
                json={
                    "code": 0,
                    "msg": "ok",
                    "data": {
                        "batch_id": "batch-fallback",
                        "file_urls": ["https://upload.test/signed"],
                    },
                },
            )
        if method == "PUT":
            return _http_response(method, url)
        if url.endswith("/api/v4/extract-results/batch/batch-fallback"):
            polls += 1
            state = "failed" if mode == "failed" else "running"
            result = {
                "data_id": str(document_id),
                "state": state,
                "err_msg": "测试失败" if state == "failed" else "",
            }
            if mode == "missing_markdown":
                result = {
                    "data_id": str(document_id),
                    "state": "done",
                    "full_zip_url": "https://download.test/result.zip",
                }
            return _http_response(
                method,
                url,
                json={
                    "code": 0,
                    "msg": "ok",
                    "data": {
                        "batch_id": "batch-fallback",
                        "extract_result": [result],
                    },
                },
            )
        return _http_response(
            method,
            url,
            content=_zip_result(include_markdown=False),
        )

    _enable_mineru(monkeypatch)
    _install_http(monkeypatch, handler)
    monkeypatch.setattr(
        parsing_svc,
        "_parse_pdf",
        lambda *args: ParseResult(markdown="# 本地 PDF 回退", page_count=3),
    )
    monkeypatch.setattr(mineru_svc, "MINERU_POLL_INTERVAL_SECONDS", 0)
    if mode == "timeout":
        monkeypatch.setattr(mineru_svc, "MINERU_TOTAL_TIMEOUT_SECONDS", 0.01)

    result = await parsing_svc.parse_file(
        path,
        ".pdf",
        uuid.uuid4(),
        document_id,
        "fallback.pdf",
    )

    assert result.markdown == "# 本地 PDF 回退"
    assert result.page_count == 3
    assert result.details["mineru"] == "failed→fallback"
    assert reason_fragment in result.details["reason"]


async def test_empty_key_uses_local_pdf_without_http(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "local.pdf"
    path.write_bytes(b"%PDF-fake")
    monkeypatch.setattr(
        parsing_svc,
        "get_settings",
        lambda: SimpleNamespace(mineru_api_key="", mineru_base_url="https://mineru.test"),
    )
    monkeypatch.setattr(
        parsing_svc,
        "_parse_pdf",
        lambda *args: ParseResult(markdown="# 直接本地解析", page_count=2),
    )

    class UnexpectedClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("key 为空时不应发起 HTTP 请求")

    monkeypatch.setattr(mineru_svc.httpx, "AsyncClient", UnexpectedClient)
    result = await parsing_svc.parse_file(
        path,
        ".pdf",
        uuid.uuid4(),
        uuid.uuid4(),
        "local.pdf",
    )

    assert result.markdown == "# 直接本地解析"
    assert result.details == {"mineru": "disabled→local"}


async def test_ppt_upload_allowed_but_auth_failure_marks_document_failed(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: User,
    monkeypatch,
) -> None:
    course = await _create_course(client)
    upload = await client.post(
        f"/api/v1/courses/{course['id']}/documents",
        files={
            "file": (
                "slides.ppt",
                b"fake-ppt-content",
                "application/vnd.ms-powerpoint",
            )
        },
    )
    assert upload.status_code == 200, upload.text
    rejected = await client.post(
        f"/api/v1/courses/{course['id']}/documents",
        files={"file": ("archive.xyz", b"unknown", "application/octet-stream")},
    )
    assert rejected.status_code == 400
    document_id = uuid.UUID(upload.json()["id"])

    _enable_mineru(monkeypatch)
    _install_http(
        monkeypatch,
        lambda method, url, kwargs: _http_response(method, url, 403),
    )
    parse_job = (
        await db.execute(select(Job).where(Job.kind == "parse_document"))
    ).scalar_one()

    with pytest.raises(ValueError, match="该格式需要在线转换服务，当前不可用"):
        await ingest_svc.handle_parse_document(db, parse_job)

    document = await db.get(Document, document_id)
    await db.refresh(document)
    assert document.status == "failed"
    assert "该格式需要在线转换服务，当前不可用" in document.error
