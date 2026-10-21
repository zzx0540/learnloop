import re
import uuid
from pathlib import Path

from app.config import get_settings

ALLOWED_EXTENSIONS = {
    ".doc",
    ".docx",
    ".epub",
    ".markdown",
    ".md",
    ".pdf",
    ".ppt",
    ".pptx",
    ".txt",
}


def files_root() -> Path:
    return Path(get_settings().files_dir)


def course_dir(course_id: uuid.UUID) -> Path:
    return files_root() / str(course_id)


def market_package_dir(market_course_id: uuid.UUID, version: int) -> Path:
    return files_root() / "market" / str(market_course_id) / f"v{version}"


def doc_dir(course_id: uuid.UUID, document_id: uuid.UUID) -> Path:
    d = course_dir(course_id) / "raw" / str(document_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def vocab_dir(course_id: uuid.UUID) -> Path:
    d = course_dir(course_id) / "vocab"
    d.mkdir(parents=True, exist_ok=True)
    return d


def images_dir(course_id: uuid.UUID) -> Path:
    d = course_dir(course_id) / "images"
    d.mkdir(parents=True, exist_ok=True)
    return d


def rel_to_course(course_id: uuid.UUID, abs_path: Path) -> str:
    return abs_path.relative_to(course_dir(course_id)).as_posix()


def file_url(course_id: uuid.UUID, abs_path: Path) -> str:
    return f"/api/v1/files/{course_id}/{rel_to_course(course_id, abs_path)}"


def safe_resolve(course_id: uuid.UUID, rel: str) -> Path | None:
    """把课程内相对路径安全解析为绝对路径，越界返回 None。"""
    base = course_dir(course_id).resolve()
    target = (base / rel).resolve()
    if not target.is_relative_to(base):
        return None
    return target


def sanitize_filename(name: str) -> str:
    name = re.sub(r"[^\w一-龥.\-]+", "_", name)
    return name[:120] or "file"
