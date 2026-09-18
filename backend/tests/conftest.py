import pytest
from sqlalchemy.orm import sessionmaker

from app.db.base import engine


@pytest.fixture()
def db_connection():
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
