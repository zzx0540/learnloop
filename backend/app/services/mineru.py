import asyncio
import logging
import tempfile
import time
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import httpx

from app.services.storage import images_dir, sanitize_filename

logger = logging.getLogger("learnloop.mineru")

MINERU_POLL_INTERVAL_SECONDS = 15.0
MINERU_TOTAL_TIMEOUT_SECONDS = 20 * 60.0
MINERU_HTTP_TIMEOUT_SECONDS = 180.0
MINERU_IMAGE_EXTENSIONS = {
    ".bmp",
    ".gif",
    ".jpeg",
    ".jpg",
    ".jp2",
    ".png",
    ".tif",
    ".tiff",
    ".webp",
}

_auth_warning_emitted = False


class MineruError(RuntimeError):
    pass


@dataclass
class MineruResult:
    markdown: str
    image_lookup: dict[str, Path]
    image_paths: list[Path]
    page_count: int | None
    elapsed_seconds: float


def _warn_auth_once(status_code: int) -> None:
    global _auth_warning_emitted
    if _auth_warning_emitted:
        return
    _auth_warning_emitted = True
    logger.warning("MinerU 鉴权失败（HTTP %d），将按文件格式执行回退", status_code)


async def _request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    stage: str,
    **kwargs,
) -> httpx.Response:
    try:
        response = await client.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        raise MineruError(f"{stage}网络请求失败（{type(exc).__name__}）") from exc
    if response.status_code in (401, 403):
        _warn_auth_once(response.status_code)
        raise MineruError(f"{stage}鉴权失败（HTTP {response.status_code}）")
    if not response.is_success:
        raise MineruError(f"{stage}失败（HTTP {response.status_code}）")
    return response


def _response_data(response: httpx.Response, stage: str) -> dict:
    try:
        payload = response.json()
    except ValueError as exc:
        raise MineruError(f"{stage}返回了无效 JSON") from exc
    if not isinstance(payload, dict):
        raise MineruError(f"{stage}响应格式不正确")
    if payload.get("code") != 0:
        message = str(payload.get("msg") or "未知错误").replace("\n", " ")[:300]
        raise MineruError(f"{stage}被拒绝：{message}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise MineruError(f"{stage}响应缺少 data")
    return data


def _read_archive(
    zip_path: Path,
    course_id: uuid.UUID,
    document_id: uuid.UUID,
) -> tuple[str, dict[str, Path], list[Path]]:
    try:
        archive = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as exc:
        raise MineruError("MinerU 返回的压缩包不合法") from exc

    with archive:
        files = [info for info in archive.infolist() if not info.is_dir()]
        markdown_entries = [
            info for info in files if PurePosixPath(info.filename).name == "full.md"
        ]
        if not markdown_entries:
            raise MineruError("MinerU 压缩包缺少 full.md")
        markdown_info = min(
            markdown_entries,
            key=lambda info: (len(PurePosixPath(info.filename).parts), info.filename),
        )
        try:
            markdown = archive.read(markdown_info).decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise MineruError("MinerU full.md 不是有效 UTF-8") from exc

        markdown_parent = PurePosixPath(markdown_info.filename).parent
        pending_images: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
        for info in files:
            entry = PurePosixPath(info.filename)
            try:
                relative = entry.relative_to(markdown_parent)
            except ValueError:
                continue
            if (
                len(relative.parts) < 2
                or relative.parts[0] != "images"
                or relative.suffix.lower() not in MINERU_IMAGE_EXTENSIONS
            ):
                continue
            pending_images.append((info, relative))

        target_dir = images_dir(course_id)
        image_lookup: dict[str, Path] = {}
        image_paths: list[Path] = []
        for index, (info, relative) in enumerate(pending_images, start=1):
            original_name = sanitize_filename(relative.name)
            target = target_dir / (
                f"{document_id.hex[:8]}_mineru_{index}_{original_name}"
            )
            target.write_bytes(archive.read(info))
            image_paths.append(target)
            image_lookup[relative.as_posix()] = target
            image_lookup[f"./{relative.as_posix()}"] = target
            image_lookup[PurePosixPath(info.filename).as_posix()] = target

    return markdown, image_lookup, image_paths


async def parse_with_mineru(
    path: Path,
    original_name: str,
    course_id: uuid.UUID,
    document_id: uuid.UUID,
    api_key: str,
    base_url: str,
) -> MineruResult:
    started = time.monotonic()
    headers = {"Authorization": f"Bearer {api_key}"}
    timeout = httpx.Timeout(
        MINERU_HTTP_TIMEOUT_SECONDS,
        connect=30.0,
        pool=30.0,
    )
    try:
        async with asyncio.timeout(MINERU_TOTAL_TIMEOUT_SECONDS):
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                apply_response = await _request(
                    client,
                    "POST",
                    f"{base_url.rstrip('/')}/api/v4/file-urls/batch",
                    "MinerU 申请上传链接",
                    headers={**headers, "Content-Type": "application/json"},
                    json={
                        "files": [
                            {
                                "name": original_name,
                                "is_ocr": True,
                                "data_id": str(document_id),
                            }
                        ],
                        "model_version": "vlm",
                    },
                )
                apply_data = _response_data(apply_response, "MinerU 申请上传链接")
                batch_id = apply_data.get("batch_id")
                file_urls = apply_data.get("file_urls")
                if not isinstance(batch_id, str) or not batch_id:
                    raise MineruError("MinerU 响应缺少 batch_id")
                if not isinstance(file_urls, list) or len(file_urls) != 1:
                    raise MineruError("MinerU 响应缺少单文件上传链接")
                upload_url = file_urls[0]
                if not isinstance(upload_url, str) or not upload_url:
                    raise MineruError("MinerU 上传链接不合法")

                file_bytes = await asyncio.to_thread(path.read_bytes)
                await _request(
                    client,
                    "PUT",
                    upload_url,
                    "MinerU 上传文件",
                    content=file_bytes,
                )

                page_count: int | None = None
                full_zip_url: str | None = None
                while full_zip_url is None:
                    poll_response = await _request(
                        client,
                        "GET",
                        f"{base_url.rstrip('/')}/api/v4/extract-results/batch/{batch_id}",
                        "MinerU 查询解析结果",
                        headers=headers,
                    )
                    poll_data = _response_data(poll_response, "MinerU 查询解析结果")
                    results = poll_data.get("extract_result")
                    if not isinstance(results, list):
                        raise MineruError("MinerU 查询响应缺少 extract_result")
                    current = next(
                        (
                            item
                            for item in results
                            if isinstance(item, dict)
                            and item.get("data_id") == str(document_id)
                        ),
                        results[0] if len(results) == 1 and isinstance(results[0], dict) else None,
                    )
                    if current is None:
                        await asyncio.sleep(MINERU_POLL_INTERVAL_SECONDS)
                        continue
                    progress = current.get("extract_progress")
                    if isinstance(progress, dict) and isinstance(progress.get("total_pages"), int):
                        page_count = progress["total_pages"]
                    state = current.get("state")
                    if state == "done":
                        candidate = current.get("full_zip_url")
                        if not isinstance(candidate, str) or not candidate:
                            raise MineruError("MinerU 完成结果缺少 full_zip_url")
                        full_zip_url = candidate
                        continue
                    if state == "failed":
                        reason = str(current.get("err_msg") or "未知错误").replace("\n", " ")[:300]
                        raise MineruError(f"MinerU 解析失败：{reason}")
                    if state not in (
                        "waiting-file",
                        "pending",
                        "running",
                        "converting",
                    ):
                        raise MineruError(f"MinerU 返回未知状态：{state}")
                    await asyncio.sleep(MINERU_POLL_INTERVAL_SECONDS)

                zip_response = await _request(
                    client,
                    "GET",
                    full_zip_url,
                    "MinerU 下载解析结果",
                )
                with tempfile.TemporaryDirectory(prefix="learnloop-mineru-") as temp_dir:
                    zip_path = Path(temp_dir) / "result.zip"
                    await asyncio.to_thread(zip_path.write_bytes, zip_response.content)
                    markdown, image_lookup, image_paths = await asyncio.to_thread(
                        _read_archive,
                        zip_path,
                        course_id,
                        document_id,
                    )
    except TimeoutError as exc:
        raise MineruError("MinerU 解析超过 20 分钟") from exc

    return MineruResult(
        markdown=markdown,
        image_lookup=image_lookup,
        image_paths=image_paths,
        page_count=page_count,
        elapsed_seconds=time.monotonic() - started,
    )
