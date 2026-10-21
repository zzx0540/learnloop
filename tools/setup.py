"""Create local credentials without displaying them or overwriting an existing .env."""
from pathlib import Path
import secrets

root = Path(__file__).resolve().parents[1]
values = (root / ".env.example").read_text(encoding="utf-8")
for key in ("POSTGRES_PASSWORD", "ADMIN_PASSWORD", "INITIAL_INVITE_CODE"):
    values = values.replace(key + "=", key + "=" + secrets.token_hex(20), 1)
try:
    with (root / ".env").open("x", encoding="utf-8", newline="\n") as f:
        f.write(values)
    (root / ".env").chmod(0o600)
except FileExistsError:
    raise SystemExit("Existing .env preserved. Edit it locally if needed.")
print("Created .env with random local credentials. Open it locally to configure your model and sign in; do not share it.")
