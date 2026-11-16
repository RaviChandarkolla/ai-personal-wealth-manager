from datetime import date

import pytest

from wealth_advisor.bootstrap import bootstrap_demo
from wealth_advisor.db import open_db

AS_OF = date(2026, 10, 9)


@pytest.fixture(scope="session")
def demo_conn():
    conn = open_db(":memory:")
    bootstrap_demo(conn, AS_OF)
    return conn
