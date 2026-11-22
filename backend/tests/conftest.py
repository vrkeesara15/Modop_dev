import os
import tempfile

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/rah_test")
os.environ["STORAGE_BACKEND"] = "local"
os.environ["STORAGE_LOCAL_PATH"] = tempfile.mkdtemp(prefix="rah_test_")
os.environ["RAH_DISABLE_SCHEDULER"] = "1"
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ.setdefault("DEMO_PASSWORD", "demo1234")

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="session", autouse=True)
def migrated():
    cfg = Config(os.path.join(HERE, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(HERE, "alembic"))
    command.upgrade(cfg, "head")
    yield


@pytest.fixture()
def seeded(migrated):
    """A freshly reset and demo-seeded database."""
    from app import seed

    seed.reset_db()
    seed.seed_demo(verbose=False)
    yield


@pytest.fixture()
def client(seeded):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


def login(client, who: str):
    r = client.post("/api/auth/login", json={"email": f"{who}@demo", "password": "demo1234"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture()
def as_user(client):
    cache = {}

    def get(who):
        if who not in cache:
            cache[who] = login(client, who)
        return cache[who]

    return get
