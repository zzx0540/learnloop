from sqlalchemy.engine import make_url


def require_test_database(url: str, confirmation: str) -> str:
    if confirmation != "1":
        raise RuntimeError("Set LEARNLOOP_ALLOW_TEST_RESET=1 only for the disposable test database.")
    try:
        target = make_url(url)
    except Exception:
        raise RuntimeError("Invalid TEST_DATABASE_URL (value withheld).") from None
    if (
        target.drivername != "postgresql+asyncpg"
        or target.database != "learnloop_test"
        or target.username != "learnloop_test"
        or target.host not in {"127.0.0.1", "localhost", "::1", "pg-test"}
        or target.query
    ):
        raise RuntimeError(
            "Tests require a local learnloop_test database and dedicated learnloop_test role, "
            "without URL options."
        )
    return url
