"""Check only the local synthetic demo. Never accepts a remote target."""
import argparse
import http.cookiejar
import io
import json
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--wait", action="store_true")
args = parser.parse_args()
base = "http://127.0.0.1:8088"
jar = http.cookiejar.CookieJar()
client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

def request(path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base+path, data=data, headers={"Content-Type": "application/json"})
    with client.open(req, timeout=5) as response:
        return json.load(response)

for attempt in range(60 if args.wait else 1):
    try:
        assert request("/api/health")["status"] == "ok"
        break
    except (OSError, AssertionError):
        if not args.wait or attempt == 59:
            raise
        time.sleep(2)
assert request("/api/info")["demo_mode"] is True
with client.open(base + "/api/legal/license", timeout=10) as response:
    assert b"GNU AFFERO GENERAL PUBLIC LICENSE" in response.read()
with client.open(base + "/api/legal/notices", timeout=10) as response:
    notices = response.read()
    assert b"PyMuPDF" in notices and b"SIL OPEN FONT LICENSE" in notices
with client.open(base + "/api/legal/source", timeout=10) as response:
    with zipfile.ZipFile(io.BytesIO(response.read())) as archive:
        assert "learnloop/LICENSE" in archive.namelist()
        assert "learnloop/Dockerfile" in archive.namelist()
        assert "learnloop/LICENSES/OFL-1.1.txt" in archive.namelist()
        assert not any(name.endswith("/.env") or "/node_modules/" in name for name in archive.namelist())
        local_main = Path(__file__).resolve().parents[1] / "backend/app/main.py"
        assert archive.read("learnloop/backend/app/main.py") == local_main.read_bytes()

assert request("/api/v1/auth/login", {"username": "demo", "password": "learnloop-demo-only"})["username"] == "demo"
courses = request("/api/v1/courses")
assert len(courses) == 1
course_id = courses[0]["id"]
assert request(f"/api/v1/courses/{course_id}/overview")["course"]["id"] == course_id
try:
    request("/api/v1/courses", {"name": "must-not-be-created"})
except urllib.error.HTTPError as error:
    assert error.code == 403
else:
    raise AssertionError("Demo write guard failed")
assert len(request("/api/v1/courses")) == 1
print("PASS: local demo, license/notices, matching source archive, login and write rejection")
