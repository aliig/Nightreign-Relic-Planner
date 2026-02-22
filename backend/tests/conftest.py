from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, delete

from app.core.config import settings
from app.core.db import engine, init_db
from app.main import app
from app.models import User
from tests.utils.user import authentication_token_from_email
from tests.utils.utils import get_superuser_token_headers


@pytest.fixture(scope="session", autouse=True)
def db() -> Generator[Session, None, None]:
    with Session(engine) as session:
        init_db(session)
        yield session
        statement = delete(User)
        session.execute(statement)
        session.commit()


@pytest.fixture(scope="module")
def client() -> Generator[TestClient, None, None]:
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def superuser_token_headers(client: TestClient) -> dict[str, str]:
    return get_superuser_token_headers(client)


@pytest.fixture(scope="module")
def normal_user_token_headers(client: TestClient, db: Session) -> dict[str, str]:
    return authentication_token_from_email(
        client=client, email=settings.EMAIL_TEST_USER, db=db
    )


@pytest.fixture(scope="session")
def override_game_data() -> Generator[None, None, None]:
    """Override GameDataDep with a real SourceDataHandler loaded once per session.

    This avoids re-initializing the heavy SourceDataHandler for each test module
    and ensures dependency_overrides is set before any route under test runs.
    """
    import json
    from pathlib import Path

    import nrplanner as _pkg
    from nrplanner import SourceDataHandler

    from app.core.game_data import get_game_data

    ds = SourceDataHandler(language="en_US")
    items_path = Path(_pkg.__file__).parent / "resources" / "json" / "items.json"
    _items_json = json.loads(items_path.read_text(encoding="utf-8"))  # noqa: F841

    app.dependency_overrides[get_game_data] = lambda: ds

    yield

    app.dependency_overrides.pop(get_game_data, None)
