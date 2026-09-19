import pytest
from sqlalchemy.orm import sessionmaker

from app.core.db_safety import UnsafeConfigurationError, assert_safe_for_tests


def pytest_configure(config):
    """Refuse to run the suite against anything but an explicit test database.

    Several tests commit real rows (demo seed fixtures), so this must abort
    before collection and before any fixture or test can touch a database:
    APP_ENV must be `test` AND the database name must carry a `test` token
    (app/core/db_safety.py). Never auto-sets APP_ENV -- the operator opts in
    explicitly, e.g.

        APP_ENV=test DATABASE_URL=postgresql://.../artesanfc_test pytest -q

    The guard is CI-ready: a future workflow only needs to export the same
    two variables. Failure output never contains credentials.
    """
    # Imported lazily so a missing/invalid APP_ENV is reported as a clean
    # message here instead of an ImportError while loading this conftest.
    from app.core.config import get_settings

    try:
        settings = get_settings()
        assert_safe_for_tests(settings.app_env, settings.database_url)
    except UnsafeConfigurationError as exc:
        pytest.exit(f"\n{exc}\n", returncode=2)


@pytest.fixture()
def db_connection():
    from app.db.base import engine

    connection = engine.connect()
    trans = connection.begin()
    try:
        yield connection
    finally:
        trans.rollback()
        connection.close()


@pytest.fixture()
def db_session(db_connection):
    session_factory = sessionmaker(bind=db_connection, join_transaction_mode="create_savepoint")
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
