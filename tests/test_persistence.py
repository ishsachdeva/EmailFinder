from sqlalchemy import inspect
from sqlalchemy.orm import Session

from emailfinder.persistence.database import Suppression, is_suppressed, normalize_domain, normalize_email, person_key


def test_schema_contains_required_entities(engine):
    assert set(inspect(engine).get_table_names()) == {"companies", "people", "emails", "evidence", "prospects", "jobs", "suppression"}


def test_dedupe_normalization():
    assert normalize_domain(" HTTPS://WWW.Example.COM/path ") == "example.com"
    assert normalize_email(" A@Example.COM ") == "a@example.com"
    assert person_key("  Ada   Lovelace ", "www.Example.com") == ("ada lovelace", "example.com")


def test_email_and_domain_suppression(engine):
    with Session(engine) as session:
        session.add_all([Suppression(email="blocked@example.com", reason="opt out"), Suppression(domain="blocked.test", reason="excluded")]); session.commit()
        assert is_suppressed(session, "BLOCKED@example.com", "example.com")
        assert is_suppressed(session, "a@blocked.test", "WWW.blocked.test")
        assert not is_suppressed(session, "ok@example.com", "example.com")

