import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.db import get_db
from app.models import Course, User
from app.services.storage import safe_resolve

router = APIRouter()


@router.get("/files/{course_id}/{rel_path:path}")
async def get_course_file(
    course_id: uuid.UUID,
    rel_path: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    course = await db.get(Course, course_id)
    if course is None or course.user_id != user.id:
        raise HTTPException(status_code=404, detail="文件不存在")
    target = safe_resolve(course_id, rel_path)
    if target is None or not target.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(target)
