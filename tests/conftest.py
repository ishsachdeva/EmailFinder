from pathlib import Path

import pytest

from emailfinder.config.brief import load_company_brief
from emailfinder.persistence.database import create_database


@pytest.fixture
def brief():
    return load_company_brief(Path(__file__).parents[1] / "examples" / "company_brief.example.yaml")


@pytest.fixture
def engine(tmp_path):
    return create_database(tmp_path / "test.db")

