import pytest

from iou import core, db


@pytest.fixture()
def conn(tmp_path):
    connection = db.connect(tmp_path / "test.db")
    yield connection
    connection.close()


@pytest.fixture()
def three_people(conn):
    return [core.add_person(conn, name) for name in ("Alice", "Bob", "Carol")]


def balances_by_name(conn):
    return {item["person"]["name"]: item["balance_cents"] for item in core.balances(conn)}
