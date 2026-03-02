from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlmodel import Session

from app.api.deps import get_db
from app.core.config import settings
from app.core.db import engine, init_db
from app.main import app
from tests.utils.user import authentication_token_from_email
from tests.utils.utils import get_superuser_token_headers


@pytest.fixture(scope="function")
def db() -> Generator[Session, None, None]:
    """Provides a transactional database session for each test.

    Everything is rolled back after the test, ensuring the development
    database remains unchanged. We use nested transactions (SAVEPOINT)
    so that code calling session.commit() only commits the savepoint.
    """
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection)

    # Start a nested transaction (savepoint)
    session.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def restart_savepoint(session2, transaction2):
        # When a nested transaction ends, start a new one to keep the savepoint active
        if transaction2.nested and not transaction2._parent.nested:
            session2.begin_nested()

    # Ensure the database is initialized (superuser exists) within this transaction
    init_db(session)

    yield session

    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture(scope="function", autouse=True)
def db_override(db: Session) -> Generator[None, None, None]:
    """Override the get_db dependency in the FastAPI app to use the test session."""
    app.dependency_overrides[get_db] = lambda: db
    yield
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture(scope="module")
def client() -> Generator[TestClient, None, None]:
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def superuser_token_headers(client: TestClient) -> dict[str, str]:
    return get_superuser_token_headers(client)


@pytest.fixture(scope="function")
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
