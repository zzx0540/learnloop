"""文档解析：输出 Markdown + 抽取图片（图片引用重写为课程文件 URL）。"""

import asyncio
import logging
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urlsplit

from app.config import get_settings
from app.services.storage import file_url, images_dir

logger = logging.getLogger("learnloop.parsing")


@dataclass
class ParseResult:
    markdown: str
    page_count: int | None = None
    image_paths: list[Path] = field(default_factory=list)
    details: dict = field(default_factory=dict)


MINERU_EXTENSIONS = {".doc", ".docx", ".pdf", ".ppt", ".pptx"}
LOCAL_FALLBACK_EXTENSIONS = {".docx", ".pdf"}
MARKDOWN_IMAGE_PATTERN = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


def _decode_text(data: bytes) -> str:
    for enc in ("utf-8", "gb18030"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def rewrite_markdown_images(
    markdown: str,
    course_id: uuid.UUID,
    resolve_image: Callable[[str], Path | None],
) -> tuple[str, list[Path]]:
    images: list[Path] = []
    seen: set[Path] = set()

    def _rewrite(match: re.Match[str]) -> str:
        raw_destination = match.group(2).strip()
        if raw_destination.startswith("<") and ">" in raw_destination:
            destination = raw_destination[1 : raw_destination.index(">")]
        else:
            destination = raw_destination.split(maxsplit=1)[0]
        target = resolve_image(destination)
        if target is None or not target.exists():
            return match.group(0)
        if target not in seen:
            seen.add(target)
            images.append(target)
        return f"![{match.group(1)}]({file_url(course_id, target)})"

    return MARKDOWN_IMAGE_PATTERN.sub(_rewrite, markdown), images


def _parse_pdf(path: Path, course_id: uuid.UUID, doc_id: uuid.UUID) -> ParseResult:
    import pymupdf
    import pymupdf4llm

    img_dir = images_dir(course_id)
    with pymupdf.open(str(path)) as pdf:
        md = pymupdf4llm.to_markdown(
            pdf,
            write_images=True,
            image_path=str(img_dir),
            image_format="png",
            dpi=110,
        )
        page_count = pdf.page_count

    prefix = str(img_dir)

    def resolve_image(destination: str) -> Path | None:
        decoded = unquote(urlsplit(destination).path)
        if not decoded.startswith(prefix):
            return None
        return Path(decoded)

    md, images = rewrite_markdown_images(md, course_id, resolve_image)
    return ParseResult(markdown=md, page_count=page_count, image_paths=images)


def _parse_epub(path: Path, course_id: uuid.UUID, doc_id: uuid.UUID) -> ParseResult:
    from bs4 import BeautifulSoup
    from ebooklib import ITEM_DOCUMENT, ITEM_IMAGE, epub
    from markdownify import markdownify

    book = epub.read_epub(str(path), options={"ignore_ncx": True})
    img_dir = images_dir(course_id)
    saved: dict[str, Path] = {}
    images: list[Path] = []

    for item in book.get_items():
        if item.get_type() == ITEM_IMAGE:
            name = f"{doc_id.hex[:8]}_{Path(item.get_name()).name}"
            target = img_dir / name
            target.write_bytes(item.get_content())
            saved[Path(item.get_name()).name] = target
            images.append(target)

    parts: list[str] = []
    for item in book.get_items():
        if item.get_type() != ITEM_DOCUMENT:
            continue
        soup = BeautifulSoup(item.get_content(), "html.parser")
        for img in soup.find_all("img"):
            src = img.get("src") or ""
            target = saved.get(Path(src).name)
            if target is not None:
                img["src"] = file_url(course_id, target)
        parts.append(markdownify(str(soup), heading_style="ATX"))

    title = book.get_metadata("DC", "title")
    header = f"# {title[0][0]}\n\n" if title else ""
    return ParseResult(markdown=header + "\n\n---\n\n".join(parts), image_paths=images)


def _parse_docx(path: Path, course_id: uuid.UUID, doc_id: uuid.UUID) -> ParseResult:
    import mammoth
    from markdownify import markdownify

    img_dir = images_dir(course_id)
    images: list[Path] = []
    counter = 0

    def convert_image(image):
        nonlocal counter
        counter += 1
        ext = (image.content_type or "image/png").split("/")[-1].split("+")[0]
        target = img_dir / f"{doc_id.hex[:8]}_docx_{counter}.{ext}"
        with image.open() as f:
            target.write_bytes(f.read())
        images.append(target)
        return {"src": file_url(course_id, target)}

    with open(path, "rb") as f:
        result = mammoth.convert_to_html(f, convert_image=mammoth.images.img_element(convert_image))
    return ParseResult(markdown=markdownify(result.value, heading_style="ATX"), image_paths=images)


def _parse_sync(path: Path, ext: str, course_id: uuid.UUID, doc_id: uuid.UUID) -> ParseResult:
    if ext == ".pdf":
        return _parse_pdf(path, course_id, doc_id)
    if ext == ".epub":
        return _parse_epub(path, course_id, doc_id)
    if ext == ".docx":
        return _parse_docx(path, course_id, doc_id)
    if ext in (".txt", ".md", ".markdown"):
        return ParseResult(markdown=_decode_text(path.read_bytes()))
    raise ValueError(f"不支持的文件类型: {ext}")


async def parse_file(
    path: Path,
    ext: str,
    course_id: uuid.UUID,
    doc_id: uuid.UUID,
    original_name: str | None = None,
) -> ParseResult:
    ext = ext.lower()
    settings = get_settings()
    if ext in MINERU_EXTENSIONS and settings.mineru_api_key.strip():
        from app.services.mineru import MineruError, parse_with_mineru

        try:
            mineru_result = await parse_with_mineru(
                path,
                original_name or path.name,
                course_id,
                doc_id,
                settings.mineru_api_key,
                settings.mineru_base_url,
            )

            def resolve_image(destination: str) -> Path | None:
                decoded = unquote(urlsplit(destination).path).replace("\\", "/")
                candidates = (decoded, decoded.removeprefix("./"), decoded.lstrip("/"))
                return next(
                    (
                        mineru_result.image_lookup[candidate]
                        for candidate in candidates
                        if candidate in mineru_result.image_lookup
                    ),
                    None,
                )

            markdown, _ = rewrite_markdown_images(
                mineru_result.markdown,
                course_id,
                resolve_image,
            )
            details = {
                "mineru": "success",
                "elapsed_seconds": round(mineru_result.elapsed_seconds, 3),
            }
            if mineru_result.page_count is not None:
                details["pages"] = mineru_result.page_count
            return ParseResult(
                markdown=markdown,
                page_count=mineru_result.page_count,
                image_paths=mineru_result.image_paths,
                details=details,
            )
        except MineruError as exc:
            if ext not in LOCAL_FALLBACK_EXTENSIONS:
                raise ValueError("该格式需要在线转换服务，当前不可用") from exc
            result = await asyncio.to_thread(_parse_sync, path, ext, course_id, doc_id)
            result.details = {
                "mineru": "failed→fallback",
                "reason": str(exc)[:500],
            }
            return result

    if ext in MINERU_EXTENSIONS and ext not in LOCAL_FALLBACK_EXTENSIONS:
        raise ValueError("该格式需要在线转换服务，当前不可用")
    result = await asyncio.to_thread(_parse_sync, path, ext, course_id, doc_id)
    if ext in LOCAL_FALLBACK_EXTENSIONS:
        result.details = {"mineru": "disabled→local"}
    return result
