"""Fixed, build-time public license artifacts; never reads user-controlled paths."""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.config import get_settings

router = APIRouter()
ARTIFACTS = {
    "license": ("LICENSE", "text/plain", None),
    "notice": ("NOTICE", "text/plain", None),
    "notices": ("third-party-notices.txt", "text/plain", None),
    "source": ("learnloop-source.zip", "application/zip", "learnloop-source.zip"),
}


@router.get("/api/legal/{artifact}", include_in_schema=False)
async def legal_artifact(artifact: str):
    if artifact not in ARTIFACTS:
        raise HTTPException(status_code=404)
    name, media_type, download = ARTIFACTS[artifact]
    root = Path(get_settings().legal_dir or Path(__file__).resolve().parents[3] / ".legal")
    path = root / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="构建许可文件尚未生成，请按部署文档生成源码快照。")
    return FileResponse(
        path, media_type=media_type, filename=download, headers={"X-Content-Type-Options": "nosniff"}
    )
