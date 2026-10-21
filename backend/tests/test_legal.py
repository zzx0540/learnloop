import hashlib
import json
import zipfile

import pytest

from app.config import get_settings
from app.source_package import package


def test_source_archive_excludes_secrets_runtime_and_installed_files(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    for name in ("LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md"):
        (root / name).write_text("public notice", encoding="utf-8")
    public = root / "backend/app/example.py"
    public.parent.mkdir(parents=True)
    public.write_text("print('original code')", encoding="utf-8")
    (root / ".env.example").write_text("DEEPSEEK_API_KEY=", encoding="utf-8")
    private = [
        ".env",
        "backend/.env.local",
        "backend/secrets/account.json",
        "frontend/node_modules/pkg/key.txt",
        "frontend/dist/private.js",
        "backend/backups/db.dump",
        "backend/app/temp.key",
        "unknown-directory/file.py",
    ]
    for name in private:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("PRIVATE_FIXTURE_VALUE", encoding="utf-8")
    output = root / ".legal"
    report = package(root, output)
    first = (output / "learnloop-source.zip").read_bytes()
    assert package(root, output) == report
    assert first == (output / "learnloop-source.zip").read_bytes()
    with zipfile.ZipFile(output / "learnloop-source.zip") as archive:
        assert "learnloop/backend/app/example.py" in archive.namelist()
        assert "learnloop/.env.example" in archive.namelist()
        assert not any(b"PRIVATE_FIXTURE_VALUE" in archive.read(name) for name in archive.namelist())
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["files"]["backend/app/example.py"] == hashlib.sha256(public.read_bytes()).hexdigest()
    with pytest.raises(ValueError):
        package(root, root / "backend/output")


async def test_legal_downloads_are_fixed_public_artifacts(client, tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "legal_dir", str(tmp_path))
    (tmp_path / "LICENSE").write_text("GNU AFFERO GENERAL PUBLIC LICENSE", encoding="utf-8")
    (tmp_path / ".env").write_text("PRIVATE_FIXTURE_VALUE", encoding="utf-8")
    with zipfile.ZipFile(tmp_path / "learnloop-source.zip", "w") as archive:
        archive.writestr("learnloop/LICENSE", "public")
    result = await client.get("/api/legal/license")
    assert result.status_code == 200 and result.headers["x-content-type-options"] == "nosniff"
    result = await client.get("/api/legal/source")
    assert result.status_code == 200 and "attachment" in result.headers["content-disposition"]
    assert (await client.get("/api/legal/.env")).status_code == 404
    assert (await client.get("/api/legal/missing")).status_code == 404
    assert (await client.get("/api/legal/notices")).status_code == 404
