import hashlib
import json
import os
import re
import shutil
import uuid
from copy import deepcopy
from pathlib import Path, PurePosixPath

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.jobs.worker import job_handler
from app.models import (
    Course,
    Document,
    DocumentImage,
    Job,
    KnowledgePoint,
    KpEdge,
    KpWikiLink,
    MarketAcquisition,
    MarketCourse,
    MarketCourseVersion,
    PointsLedger,
    User,
    VocabEntry,
    WikiLogEntry,
    WikiPage,
    WikiPageRevision,
    utcnow,
)
from app.models.language import LanguageProfile
from app.schemas.market import (
    ManifestCourseV1,
    ManifestDocumentImageV1,
    ManifestDocumentV1,
    ManifestFileV1,
    ManifestKnowledgePointV1,
    ManifestKpEdgeV1,
    ManifestKpWikiLinkV1,
    ManifestVocabEntryV1,
    ManifestWikiPageV1,
    MarketCourseManifestV1,
)
from app.services.points import add_ledger_entry, points_balance
from app.services.storage import course_dir, market_package_dir

ACTIVE_CONTENT_JOBS = {"parse_document", "ingest_document", "extract_kps", "vocab_import"}
COURSE_FILE_MARKER = "__MARKET_COURSE_ID__"
UUID_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_sha256(manifest: MarketCourseManifestV1) -> str:
    raw = json.dumps(
        manifest.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _safe_relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("市场内容包包含不安全路径")
    return path


def _source_path(course_id: uuid.UUID, value: str) -> Path:
    base = course_dir(course_id).resolve()
    path = Path(value).resolve()
    if not path.is_relative_to(base) or not path.is_file():
        raise ValueError("源课程文件缺失或越界")
    return path


def _copy_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _link_or_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)


def _rewrite_text(text: str, replacements: dict[str, str]) -> str:
    for source, target in sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True):
        text = text.replace(source, target)
    return text


def _pack_source_refs(refs: list, document_keys: dict[str, str]) -> list[dict]:
    packed: list[dict] = []
    for raw in refs:
        if not isinstance(raw, dict):
            continue
        ref = deepcopy(raw)
        document_id = ref.pop("document_id", None)
        if document_id is not None:
            key = document_keys.get(str(document_id))
            if key is None:
                raise ValueError("Wiki 溯源引用了不存在的文档")
            ref["document_key"] = key
        packed.append(ref)
    return packed


def _unpack_source_refs(refs: list[dict], document_ids: dict[str, uuid.UUID]) -> list[dict]:
    unpacked: list[dict] = []
    for raw in refs:
        ref = deepcopy(raw)
        document_key = ref.pop("document_key", None)
        if document_key is not None:
            document_id = document_ids.get(str(document_key))
            if document_id is None:
                raise ValueError("市场内容包的文档溯源无法映射")
            ref["document_id"] = str(document_id)
        unpacked.append(ref)
    return unpacked


def _validate_manifest(manifest: MarketCourseManifestV1) -> None:
    files = {item.relative_path for item in manifest.files}
    if len(files) != len(manifest.files):
        raise ValueError("市场内容包存在重复文件路径")
    for path in files:
        _safe_relative(path)

    document_keys = {item.key for item in manifest.documents}
    if len(document_keys) != len(manifest.documents):
        raise ValueError("市场内容包存在重复文档键")
    for item in manifest.documents:
        if item.stored_rel not in files or (item.parsed_rel and item.parsed_rel not in files):
            raise ValueError("市场内容包的文档文件不完整")
    for item in manifest.document_images:
        if item.stored_rel not in files:
            raise ValueError("市场内容包的图片文件不完整")
        if item.document_key is not None and item.document_key not in document_keys:
            raise ValueError("市场内容包的图片文档引用无效")

    wiki_slugs = {item.slug for item in manifest.wiki_pages}
    kp_slugs = {item.slug for item in manifest.knowledge_points}
    if len(wiki_slugs) != len(manifest.wiki_pages) or len(kp_slugs) != len(
        manifest.knowledge_points
    ):
        raise ValueError("市场内容包存在重复 Wiki 或知识点 slug")
    for item in manifest.knowledge_points:
        if item.primary_wiki_slug is not None and item.primary_wiki_slug not in wiki_slugs:
            raise ValueError("市场内容包的知识点主 Wiki 引用无效")
    for item in manifest.kp_edges:
        if item.src_slug not in kp_slugs or item.dst_slug not in kp_slugs:
            raise ValueError("市场内容包的知识点边引用无效")
    for item in manifest.kp_wiki_links:
        if item.kp_slug not in kp_slugs or item.wiki_slug not in wiki_slugs:
            raise ValueError("市场内容包的知识点 Wiki 链接无效")
    words = {item.word for item in manifest.vocab_entries}
    if len(words) != len(manifest.vocab_entries):
        raise ValueError("市场内容包存在重复词条")


async def _ensure_source_ready(db: AsyncSession, course_id: uuid.UUID) -> Course:
    course = await db.get(Course, course_id)
    if course is None:
        raise ValueError("市场源课程不存在")
    documents = list(
        (await db.execute(select(Document).where(Document.course_id == course.id))).scalars()
    )
    vocab_count = int(
        (
            await db.execute(
                select(func.count()).select_from(VocabEntry).where(VocabEntry.course_id == course.id)
            )
        ).scalar_one()
    )
    if not documents and vocab_count == 0:
        raise ValueError("源课程没有可发布的学习资料")
    if any(document.status != "ingested" for document in documents):
        raise ValueError("源课程仍有资料未完成入库")
    kp_total, kp_unlinked = (
        await db.execute(
            select(
                func.count(KnowledgePoint.id),
                func.count(KnowledgePoint.id).filter(KnowledgePoint.primary_wiki_page_id.is_(None)),
            ).where(KnowledgePoint.course_id == course.id)
        )
    ).one()
    if kp_total and kp_unlinked:
        raise ValueError("源课程仍有知识点未关联 Wiki 主页面")
    active = (
        await db.execute(
            select(Job.id).where(
                Job.course_id == course.id,
                Job.kind.in_(ACTIVE_CONTENT_JOBS),
                Job.status.in_(("queued", "running")),
            )
        )
    ).scalar_one_or_none()
    if active is not None:
        raise ValueError("源课程仍有内容任务进行中")
    return course


async def build_market_manifest(
    db: AsyncSession,
    source_course: Course,
    staging_dir: Path,
) -> MarketCourseManifestV1:
    documents = list(
        (
            await db.execute(
                select(Document)
                .where(Document.course_id == source_course.id)
                .order_by(Document.created_at, Document.id)
            )
        ).scalars()
    )
    document_keys = {str(row.id): f"doc-{index:04d}" for index, row in enumerate(documents, 1)}
    images = list(
        (
            await db.execute(
                select(DocumentImage)
                .where(DocumentImage.course_id == source_course.id)
                .order_by(DocumentImage.created_at, DocumentImage.id)
            )
        ).scalars()
    )
    image_keys = {str(row.id): f"image-{index:04d}" for index, row in enumerate(images, 1)}

    source_to_package: dict[str, str] = {}
    manifest_documents: list[ManifestDocumentV1] = []
    declared_sources: list[tuple[Path, str, bool]] = []
    for document in documents:
        key = document_keys[str(document.id)]
        original = _source_path(source_course.id, document.stored_path)
        if original.stat().st_size != document.size_bytes or _sha256(original) != document.sha256:
            raise ValueError("源课程文档完整性校验失败")
        original_rel = f"raw/{key}/original{original.suffix.lower()}"
        source_to_package[
            str(original.relative_to(course_dir(source_course.id).resolve())).replace(os.sep, "/")
        ] = original_rel
        declared_sources.append((original, original_rel, False))
        parsed_rel = None
        if document.parsed_md_path:
            parsed = _source_path(source_course.id, document.parsed_md_path)
            parsed_rel = f"raw/{key}/parsed.md"
            source_to_package[
                str(parsed.relative_to(course_dir(source_course.id).resolve())).replace(os.sep, "/")
            ] = parsed_rel
            declared_sources.append((parsed, parsed_rel, True))
        manifest_documents.append(
            ManifestDocumentV1(
                key=key,
                original_name=document.original_name,
                mime=document.mime,
                size_bytes=document.size_bytes,
                sha256=document.sha256,
                stored_rel=original_rel,
                parsed_rel=parsed_rel,
                page_count=document.page_count,
            )
        )

    manifest_images: list[ManifestDocumentImageV1] = []
    for image in images:
        key = image_keys[str(image.id)]
        source = _source_path(source_course.id, image.stored_path)
        image_rel = f"images/{key}{source.suffix.lower()}"
        source_to_package[
            str(source.relative_to(course_dir(source_course.id).resolve())).replace(os.sep, "/")
        ] = image_rel
        declared_sources.append((source, image_rel, False))
        manifest_images.append(
            ManifestDocumentImageV1(
                key=key,
                document_key=document_keys.get(str(image.document_id)) if image.document_id else None,
                stored_rel=image_rel,
                caption=image.caption,
            )
        )

    replacements = {
        f"/api/v1/files/{source_course.id}/{source_rel}": (
            f"/api/v1/files/{COURSE_FILE_MARKER}/{package_rel}"
        )
        for source_rel, package_rel in source_to_package.items()
    }
    manifest_files: list[ManifestFileV1] = []
    for source, relative, rewrite in declared_sources:
        target = staging_dir / _safe_relative(relative)
        if rewrite:
            target.parent.mkdir(parents=True, exist_ok=True)
            content = source.read_text(encoding="utf-8")
            target.write_text(_rewrite_text(content, replacements), encoding="utf-8")
        else:
            _copy_file(source, target)
        manifest_files.append(
            ManifestFileV1(
                relative_path=relative,
                size_bytes=target.stat().st_size,
                sha256=_sha256(target),
            )
        )

    wiki_pages = list(
        (
            await db.execute(
                select(WikiPage)
                .where(WikiPage.course_id == source_course.id)
                .order_by(WikiPage.slug)
            )
        ).scalars()
    )
    wiki_by_id = {row.id: row.slug for row in wiki_pages}
    manifest_wiki = [
        ManifestWikiPageV1(
            slug=row.slug,
            title=row.title,
            kind=row.kind,
            content_md=_rewrite_text(row.content_md, replacements),
            out_links=list(row.out_links or []),
            source_refs=_pack_source_refs(list(row.source_refs or []), document_keys),
        )
        for row in wiki_pages
    ]

    knowledge_points = list(
        (
            await db.execute(
                select(KnowledgePoint)
                .where(KnowledgePoint.course_id == source_course.id)
                .order_by(KnowledgePoint.slug)
            )
        ).scalars()
    )
    kp_by_id = {row.id: row.slug for row in knowledge_points}
    manifest_kps = [
        ManifestKnowledgePointV1(
            slug=row.slug,
            title=row.title,
            summary=row.summary,
            recall_prompt=row.recall_prompt,
            primary_wiki_slug=wiki_by_id.get(row.primary_wiki_page_id),
            importance=row.importance,
            difficulty=row.difficulty,
            created_by=row.created_by,
        )
        for row in knowledge_points
    ]
    edges = list(
        (
            await db.execute(
                select(KpEdge).where(KpEdge.course_id == source_course.id).order_by(KpEdge.id)
            )
        ).scalars()
    )
    manifest_edges = [
        ManifestKpEdgeV1(
            src_slug=kp_by_id[row.src_kp_id],
            dst_slug=kp_by_id[row.dst_kp_id],
            relation=row.relation,
            created_by=row.created_by,
        )
        for row in edges
    ]
    links = (
        await db.execute(
            select(KpWikiLink.kp_id, KpWikiLink.wiki_page_id, KpWikiLink.role)
            .join(KnowledgePoint, KnowledgePoint.id == KpWikiLink.kp_id)
            .where(KnowledgePoint.course_id == source_course.id)
        )
    ).all()
    manifest_links = [
        ManifestKpWikiLinkV1(
            kp_slug=kp_by_id[kp_id], wiki_slug=wiki_by_id[wiki_id], role=role
        )
        for kp_id, wiki_id, role in links
    ]

    vocab = list(
        (
            await db.execute(
                select(VocabEntry)
                .where(VocabEntry.course_id == source_course.id)
                .order_by(VocabEntry.word)
            )
        ).scalars()
    )
    daily_new_words = (source_course.settings or {}).get("daily_new_words")
    if not isinstance(daily_new_words, int) or not 5 <= daily_new_words <= 100:
        daily_new_words = None
    manifest = MarketCourseManifestV1(
        course=ManifestCourseV1(
            name=source_course.name,
            description=source_course.description,
            goal_text=source_course.goal_text,
            kind=source_course.kind,
            daily_minutes=source_course.daily_minutes,
            daily_new_words=daily_new_words,
        ),
        files=manifest_files,
        documents=manifest_documents,
        document_images=manifest_images,
        wiki_pages=manifest_wiki,
        knowledge_points=manifest_kps,
        kp_edges=manifest_edges,
        kp_wiki_links=manifest_links,
        vocab_entries=[
            ManifestVocabEntryV1(
                word=row.word,
                phonetic=row.phonetic,
                meaning_md=row.meaning_md,
                example_md=row.example_md,
                source_note=row.source_note,
            )
            for row in vocab
        ],
    )
    _validate_manifest(manifest)
    source_ids = {
        str(source_course.id),
        *(str(row.id) for row in documents),
        *(str(row.id) for row in images),
        *(str(row.id) for row in wiki_pages),
        *(str(row.id) for row in knowledge_points),
        *(str(row.id) for row in edges),
        *(str(row.id) for row in vocab),
    }
    serialized = json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False)
    if source_ids.intersection(UUID_PATTERN.findall(serialized)):
        raise ValueError("市场内容包仍包含源课程对象标识")
    return manifest


def verify_market_package(version: MarketCourseVersion) -> MarketCourseManifestV1:
    manifest = MarketCourseManifestV1.model_validate(version.manifest)
    _validate_manifest(manifest)
    if _manifest_sha256(manifest) != version.package_sha256:
        raise ValueError("市场内容包清单校验失败")
    expected = market_package_dir(version.market_course_id, version.version).resolve()
    package = Path(version.package_path).resolve()
    if package != expected or not package.is_dir():
        raise ValueError("市场内容包目录无效")
    for item in manifest.files:
        path = (package / _safe_relative(item.relative_path)).resolve()
        if not path.is_relative_to(package) or not path.is_file() or path.is_symlink():
            raise ValueError("市场内容包文件缺失或越界")
        if path.stat().st_size != item.size_bytes or _sha256(path) != item.sha256:
            raise ValueError("市场内容包文件校验失败")
    return manifest


async def _publish_market_course(db: AsyncSession, job: Job) -> dict:
    version_id = uuid.UUID(job.payload["market_version_id"])
    version = (
        await db.execute(
            select(MarketCourseVersion)
            .where(MarketCourseVersion.id == version_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if version is None:
        raise ValueError("市场发布版本不存在")
    if version.status == "ready":
        return {"market_course_id": str(version.market_course_id), "version": version.version}
    market = (
        await db.execute(
            select(MarketCourse).where(MarketCourse.id == version.market_course_id).with_for_update()
        )
    ).scalar_one()
    if market.source_course_id is None:
        raise ValueError("市场源课程已删除")
    source = await _ensure_source_ready(db, market.source_course_id)

    final_dir = market_package_dir(market.id, version.version)
    staging_dir = final_dir.with_name(f"{final_dir.name}.tmp-{version.id.hex}")
    shutil.rmtree(staging_dir, ignore_errors=True)
    staging_dir.mkdir(parents=True, exist_ok=False)
    try:
        manifest = await build_market_manifest(db, source, staging_dir)
        package_sha = _manifest_sha256(manifest)
        shutil.rmtree(final_dir, ignore_errors=True)
        final_dir.parent.mkdir(parents=True, exist_ok=True)
        staging_dir.rename(final_dir)
        version.manifest = manifest.model_dump(mode="json")
        version.package_path = str(final_dir)
        version.package_sha256 = package_sha
        version.status = "ready"
        version.error = None
        market.current_version = version.version
        market.status = "published"
        market.published_at = utcnow()
        await db.commit()
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        shutil.rmtree(final_dir, ignore_errors=True)
        raise
    return {
        "market_course_id": str(market.id),
        "version": version.version,
        "documents": len(manifest.documents),
        "knowledge_points": len(manifest.knowledge_points),
        "vocab_entries": len(manifest.vocab_entries),
    }


@job_handler("market_publish", heavy=True)
async def handle_market_publish(db: AsyncSession, job: Job) -> dict:
    is_final_attempt = job.attempts >= job.max_attempts
    version_id = uuid.UUID(job.payload["market_version_id"])
    try:
        return await _publish_market_course(db, job)
    except Exception as exc:
        await db.rollback()
        if is_final_attempt:
            version = await db.get(MarketCourseVersion, version_id)
            if version is not None and version.status != "ready":
                version.status = "failed"
                version.error = str(exc)[:2000]
                await db.commit()
        raise


def _target_course_id(acquisition_id: uuid.UUID) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"learnloop:market-acquisition:{acquisition_id}")


async def _materialize_acquisition(
    db: AsyncSession,
    acquisition: MarketAcquisition,
    version: MarketCourseVersion,
    manifest: MarketCourseManifestV1,
) -> uuid.UUID:
    target_id = _target_course_id(acquisition.id)
    if await db.get(Course, target_id) is not None:
        raise ValueError("市场获取存在未完成的目标课程")
    package = Path(version.package_path).resolve()
    target_root = course_dir(target_id)
    shutil.rmtree(target_root, ignore_errors=True)
    target_root.mkdir(parents=True, exist_ok=False)
    try:
        for file in manifest.files:
            source = (package / _safe_relative(file.relative_path)).resolve()
            target = target_root / _safe_relative(file.relative_path)
            if file.relative_path.endswith("/parsed.md"):
                target.parent.mkdir(parents=True, exist_ok=True)
                content = source.read_text(encoding="utf-8")
                target.write_text(content.replace(COURSE_FILE_MARKER, str(target_id)), encoding="utf-8")
            else:
                _link_or_copy(source, target)

        settings = {}
        if manifest.course.daily_new_words is not None:
            settings["daily_new_words"] = manifest.course.daily_new_words
        course = Course(
            id=target_id,
            user_id=acquisition.user_id,
            name=acquisition.title_snapshot,
            description=manifest.course.description,
            goal_text=manifest.course.goal_text,
            kind=manifest.course.kind,
            language_track="custom" if manifest.course.kind == "language" else None,
            daily_minutes=manifest.course.daily_minutes,
            target_date=None,
            status="onboarding",
            settings=settings,
        )
        db.add(course)
        await db.flush()
        if course.kind == "language":
            db.add(LanguageProfile(course_id=course.id))

        document_ids: dict[str, uuid.UUID] = {}
        for item in manifest.documents:
            document_id = uuid.uuid4()
            document_ids[item.key] = document_id
            db.add(
                Document(
                    id=document_id,
                    course_id=target_id,
                    original_name=item.original_name,
                    stored_path=str(target_root / _safe_relative(item.stored_rel)),
                    mime=item.mime,
                    size_bytes=item.size_bytes,
                    sha256=item.sha256,
                    status="ingested",
                    parsed_md_path=(
                        str(target_root / _safe_relative(item.parsed_rel))
                        if item.parsed_rel
                        else None
                    ),
                    page_count=item.page_count,
                    is_market_copy=True,
                )
            )
        await db.flush()

        for item in manifest.document_images:
            db.add(
                DocumentImage(
                    course_id=target_id,
                    document_id=document_ids.get(item.document_key) if item.document_key else None,
                    stored_path=str(target_root / _safe_relative(item.stored_rel)),
                    caption=item.caption,
                )
            )

        wiki_ids: dict[str, uuid.UUID] = {}
        for item in manifest.wiki_pages:
            page_id = uuid.uuid4()
            wiki_ids[item.slug] = page_id
            content = item.content_md.replace(COURSE_FILE_MARKER, str(target_id))
            db.add(
                WikiPage(
                    id=page_id,
                    course_id=target_id,
                    slug=item.slug,
                    title=item.title,
                    kind=item.kind,
                    content_md=content,
                    out_links=item.out_links,
                    source_refs=_unpack_source_refs(item.source_refs, document_ids),
                    version=1,
                    updated_by="market",
                )
            )
            db.add(
                WikiPageRevision(
                    page_id=page_id,
                    version=1,
                    content_md=content,
                    change_summary=f"从课程市场版本 {acquisition.version_snapshot} 导入",
                )
            )
        await db.flush()

        kp_ids: dict[str, uuid.UUID] = {}
        for item in manifest.knowledge_points:
            kp_id = uuid.uuid4()
            kp_ids[item.slug] = kp_id
            db.add(
                KnowledgePoint(
                    id=kp_id,
                    course_id=target_id,
                    slug=item.slug,
                    title=item.title,
                    summary=item.summary,
                    recall_prompt=item.recall_prompt,
                    primary_wiki_page_id=(
                        wiki_ids.get(item.primary_wiki_slug) if item.primary_wiki_slug else None
                    ),
                    importance=item.importance,
                    difficulty=item.difficulty,
                    status="pending",
                    mastery_score=0,
                    created_by=item.created_by,
                )
            )
        await db.flush()
        db.add_all(
            [
                KpEdge(
                    course_id=target_id,
                    src_kp_id=kp_ids[item.src_slug],
                    dst_kp_id=kp_ids[item.dst_slug],
                    relation=item.relation,
                    created_by=item.created_by,
                )
                for item in manifest.kp_edges
            ]
        )
        db.add_all(
            [
                KpWikiLink(
                    kp_id=kp_ids[item.kp_slug],
                    wiki_page_id=wiki_ids[item.wiki_slug],
                    role=item.role,
                )
                for item in manifest.kp_wiki_links
            ]
        )
        db.add_all(
            [
                VocabEntry(
                    course_id=target_id,
                    word=item.word,
                    phonetic=item.phonetic,
                    meaning_md=item.meaning_md,
                    example_md=item.example_md,
                    status="pending",
                    activated_at=None,
                    source_note=item.source_note,
                )
                for item in manifest.vocab_entries
            ]
        )
        db.add(
            WikiLogEntry(
                course_id=target_id,
                op="market_import",
                entry_md=f"从课程市场导入《{acquisition.title_snapshot}》版本 {acquisition.version_snapshot}",
            )
        )

        debit_note = f"market_course:{acquisition.id}"
        existing_debit = (
            await db.execute(
                select(PointsLedger.id).where(
                    PointsLedger.user_id == acquisition.user_id,
                    PointsLedger.kind == "market_course",
                    PointsLedger.note == debit_note,
                )
            )
        ).scalar_one_or_none()
        if acquisition.price_snapshot > 0 and existing_debit is None:
            ledger = await add_ledger_entry(
                db,
                user_id=acquisition.user_id,
                course_id=None,
                kind="market_course",
                amount=-acquisition.price_snapshot,
                applied_multiplier=1.0,
                note=debit_note,
                ref_id=acquisition.id,
            )
            if ledger is None:
                raise ValueError("市场课程积分扣款重复")
        acquisition.target_course_id = target_id
        acquisition.status = "completed"
        acquisition.error = None
        await db.commit()
    except Exception:
        await db.rollback()
        shutil.rmtree(target_root, ignore_errors=True)
        raise
    return target_id


async def _acquire_market_course(db: AsyncSession, job: Job) -> dict:
    acquisition_id = uuid.UUID(job.payload["acquisition_id"])
    acquisition = (
        await db.execute(
            select(MarketAcquisition)
            .where(MarketAcquisition.id == acquisition_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if acquisition is None:
        raise ValueError("市场获取记录不存在")
    if acquisition.status == "completed":
        return {"course_id": str(acquisition.target_course_id), "status": "completed"}
    await db.execute(select(User.id).where(User.id == acquisition.user_id).with_for_update())
    existing_debit = (
        await db.execute(
            select(PointsLedger.id).where(
                PointsLedger.user_id == acquisition.user_id,
                PointsLedger.kind == "market_course",
                PointsLedger.note == f"market_course:{acquisition.id}",
            )
        )
    ).scalar_one_or_none()
    if (
        acquisition.price_snapshot > 0
        and existing_debit is None
        and (await points_balance(db, acquisition.user_id)) < acquisition.price_snapshot
    ):
        acquisition.status = "failed"
        acquisition.error = "积分不足"
        await db.commit()
        return {"status": "failed", "error": "积分不足"}
    version = await db.get(MarketCourseVersion, acquisition.market_version_id)
    if version is None or version.status != "ready":
        raise ValueError("市场课程版本不可用")
    manifest = verify_market_package(version)
    target_id = await _materialize_acquisition(db, acquisition, version, manifest)
    return {"course_id": str(target_id), "status": "completed"}


@job_handler("market_acquire", heavy=True)
async def handle_market_acquire(db: AsyncSession, job: Job) -> dict:
    is_final_attempt = job.attempts >= job.max_attempts
    acquisition_id = uuid.UUID(job.payload["acquisition_id"])
    try:
        return await _acquire_market_course(db, job)
    except Exception as exc:
        await db.rollback()
        if is_final_attempt:
            acquisition = await db.get(MarketAcquisition, acquisition_id)
            if acquisition is not None and acquisition.status != "completed":
                acquisition.status = "failed"
                acquisition.error = str(exc)[:2000]
                await db.commit()
        raise
