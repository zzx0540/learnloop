import csv
import uuid
from collections.abc import Iterable
from io import StringIO
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.runtime import Tier, runtime
from app.jobs.worker import job_failure_handler, job_handler
from app.models import Course, Job, VocabEntry
from app.models.language import LanguageProfile, LanguageSource, VocabSourceEntry
from app.schemas.vocab import NormalizeVocabOut, VocabItemOut
from app.services.parsing import _decode_text

VOCAB_NORMALIZE_SYSTEM_V1 = """你是词表规范化器。版本：VOCAB_NORMALIZE_SYSTEM_V1。
从用户提供的原始词表行中提取词条，不虚构原文不存在的词。
每个可解析行最多输出一个词条；无法可靠解析的行直接丢弃。
保留原文中的释义、音标和例句，可做必要的 Markdown 清理，但不要扩写。"""


def daily_new_words(course: Course) -> int:
    value = (course.settings or {}).get("daily_new_words", 20)
    if not isinstance(value, int) or isinstance(value, bool) or not 5 <= value <= 100:
        return 20
    return value


def _clean_item(item: VocabItemOut) -> VocabItemOut | None:
    values = {
        "word": item.word.strip(),
        "phonetic": item.phonetic.strip(),
        "meaning_md": item.meaning_md.strip(),
        "example_md": item.example_md.strip(),
    }
    try:
        return VocabItemOut.model_validate(values)
    except ValidationError:
        return None


def _parse_strict_vocab_with_metadata(
    data: bytes, *, delimiter: str
) -> tuple[list[VocabItemOut], int, dict[str, tuple[int | None, str]]]:
    text = _decode_text(data)
    reader = csv.DictReader(StringIO(text), delimiter=delimiter)
    fieldnames = [name.lstrip("\ufeff").strip().lower() for name in (reader.fieldnames or [])]
    required = ["word", "phonetic", "meaning", "example"]
    if fieldnames[:4] != required or any(name not in {"rank", "level"} for name in fieldnames[4:]):
        raise ValueError("词表表头必须以 word,phonetic,meaning,example 开头，可选 rank,level")
    keys = dict(zip(fieldnames, reader.fieldnames or [], strict=True))
    items: list[VocabItemOut] = []
    metadata: dict[str, tuple[int | None, str]] = {}
    dropped = 0
    for raw in reader:
        try:
            item = VocabItemOut(
                word=(raw.get(keys["word"]) or "").strip(),
                phonetic=(raw.get(keys["phonetic"]) or "").strip(),
                meaning_md=(raw.get(keys["meaning"]) or "").strip(),
                example_md=(raw.get(keys["example"]) or "").strip(),
            )
        except ValidationError:
            dropped += 1
            continue
        cleaned = _clean_item(item)
        if cleaned is None:
            dropped += 1
        else:
            items.append(cleaned)
            raw_rank = (raw.get(keys.get("rank", "")) or "").strip()
            try:
                rank = int(raw_rank) if raw_rank else None
                if rank is not None and rank < 1:
                    raise ValueError
            except ValueError:
                rank = None
            metadata[cleaned.word] = (
                rank,
                (raw.get(keys.get("level", "")) or "").strip()[:32],
            )
    return items, dropped, metadata


def parse_strict_vocab(data: bytes, *, delimiter: str) -> tuple[list[VocabItemOut], int]:
    items, dropped, _ = _parse_strict_vocab_with_metadata(data, delimiter=delimiter)
    return items, dropped


async def normalize_freeform_vocab(
    lines: list[str], *, course_id: uuid.UUID, job_id: uuid.UUID
) -> tuple[list[VocabItemOut], int]:
    items: list[VocabItemOut] = []
    dropped = 0
    for start in range(0, len(lines), 50):
        batch = lines[start : start + 50]
        output: NormalizeVocabOut = await runtime.run_structured(
            agent="vocab",
            operation="normalize_vocab",
            tier=Tier.low,
            response_model=NormalizeVocabOut,
            system_prompt=VOCAB_NORMALIZE_SYSTEM_V1,
            volatile="原始词表行：\n" + "\n".join(batch),
            course_id=course_id,
            job_id=job_id,
            max_tokens=4096,
            temperature=0,
            thinking=False,
        )
        cleaned = [item for raw in output.items if (item := _clean_item(raw)) is not None]
        items.extend(cleaned)
        dropped += max(0, len(batch) - len(cleaned))
    return items, dropped


async def upsert_vocab_items(
    db: AsyncSession,
    *,
    course_id: uuid.UUID,
    items: Iterable[VocabItemOut],
    source_note: str,
) -> dict[str, int]:
    items = list(items)
    words = {item.word for item in items}
    existing = {
        entry.word: entry
        for entry in (
            await db.execute(
                select(VocabEntry).where(
                    VocabEntry.course_id == course_id,
                    VocabEntry.word.in_(words),
                )
            )
        ).scalars()
    } if words else {}
    counts = {"imported": 0, "updated": 0, "duplicates": 0}
    for item in items:
        entry = existing.get(item.word)
        if entry is None:
            entry = VocabEntry(
                course_id=course_id,
                word=item.word,
                phonetic=item.phonetic,
                meaning_md=item.meaning_md,
                example_md=item.example_md,
                source_note=source_note,
            )
            db.add(entry)
            existing[item.word] = entry
            counts["imported"] += 1
            continue
        changed = False
        for field in ("phonetic", "meaning_md", "example_md"):
            current = getattr(entry, field)
            incoming = getattr(item, field)
            if not current.strip() and incoming:
                setattr(entry, field, incoming)
                changed = True
        if not entry.source_note.strip() and source_note:
            entry.source_note = source_note
            changed = True
        counts["updated" if changed else "duplicates"] += 1
    return counts


@job_handler("vocab_import", heavy=True)
async def handle_vocab_import(db: AsyncSession, job: Job) -> dict:
    course_id = uuid.UUID(job.payload["course_id"])
    course = await db.get(Course, course_id)
    if course is None:
        return {"skipped": "课程不存在"}
    if course.kind != "language":
        return {"skipped": "仅语言课程支持词表导入"}
    path = Path(job.payload["stored_path"])
    if not path.is_file():
        raise FileNotFoundError("词表导入文件不存在")
    file_format = str(job.payload["format"])
    data = path.read_bytes()
    metadata: dict[str, tuple[int | None, str]] = {}
    if file_format in ("csv", "tsv"):
        items, dropped, metadata = _parse_strict_vocab_with_metadata(
            data, delimiter="," if file_format == "csv" else "\t"
        )
    else:
        lines = [line.strip() for line in _decode_text(data).splitlines() if line.strip()]
        items, dropped = await normalize_freeform_vocab(
            lines,
            course_id=course_id,
            job_id=job.id,
        )
    counts = await upsert_vocab_items(
        db,
        course_id=course_id,
        items=items,
        source_note=str(job.payload.get("source_note", ""))[:120],
    )
    source_id = uuid.UUID(job.payload["source_id"])
    source = await db.get(LanguageSource, source_id)
    if source is None or source.course_id != course_id or source.kind != "vocab":
        raise ValueError("词表来源不存在或不属于当前课程")
    words = {item.word for item in items}
    entries = list(
        (
            await db.execute(
                select(VocabEntry).where(
                    VocabEntry.course_id == course_id,
                    VocabEntry.word.in_(words),
                )
            )
        ).scalars()
    ) if words else []
    existing_links = set(
        (
            await db.execute(
                select(VocabSourceEntry.vocab_id).where(VocabSourceEntry.source_id == source.id)
            )
        ).scalars()
    )
    for entry in entries:
        if entry.id not in existing_links:
            source_rank, level_hint = metadata.get(entry.word, (None, ""))
            db.add(
                VocabSourceEntry(
                    source_id=source.id,
                    vocab_id=entry.id,
                    source_rank=source_rank,
                    level_hint=level_hint,
                )
            )
    source.status = "ready"
    source.word_count = len(entries)
    source.analysis = {
        "version": "VOCAB_SOURCE_ANALYSIS_V1",
        "imported": counts["imported"],
        "updated": counts["updated"],
        "duplicates": counts["duplicates"],
        "dropped": dropped,
    }
    profile = await db.get(LanguageProfile, course_id)
    if profile is not None:
        profile.assessment_status = "stale" if profile.latest_assessment_at else "required"
    await db.commit()
    return {**counts, "dropped": dropped, "format": file_format}


@job_failure_handler("vocab_import")
async def fail_vocab_import(db: AsyncSession, job: Job, error: str) -> None:
    source_id = job.payload.get("source_id")
    if not source_id:
        return
    source = await db.get(LanguageSource, uuid.UUID(source_id))
    if source is not None and source.status != "ready":
        source.status = "failed"
        source.error = f"词表导入失败: {error}"[:2000]
