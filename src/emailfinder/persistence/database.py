from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, create_engine, or_, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship


def now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Company(Base):
    __tablename__ = "companies"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(250), index=True)
    domain: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    website: Mapped[str] = mapped_column(String(500))
    industry: Mapped[str | None] = mapped_column(String(150))
    employee_range: Mapped[str | None] = mapped_column(String(50))
    country: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class Person(Base):
    __tablename__ = "people"
    __table_args__ = (UniqueConstraint("company_id", "full_name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    full_name: Mapped[str] = mapped_column(String(250))
    title: Mapped[str] = mapped_column(String(250))
    source_url: Mapped[str | None] = mapped_column(String(500))
    buyer_role_score: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(40))
    company: Mapped[Company] = relationship()


class Email(Base):
    __tablename__ = "emails"
    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id"), index=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    discovery_method: Mapped[str] = mapped_column(String(100))
    discovery_source: Mapped[str] = mapped_column(String(500))
    verification_status: Mapped[str] = mapped_column(String(40), index=True)
    verification_provider: Mapped[str] = mapped_column(String(100))
    catch_all: Mapped[bool] = mapped_column(Boolean, default=False)
    bounce_risk: Mapped[str] = mapped_column(String(40))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    person: Mapped[Person] = relationship()


class Evidence(Base):
    __tablename__ = "evidence"
    __table_args__ = (Index("ix_evidence_entity", "entity_type", "entity_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[int] = mapped_column(Integer)
    evidence_type: Mapped[str] = mapped_column(String(100))
    source_url: Mapped[str] = mapped_column(String(500))
    source_title: Mapped[str] = mapped_column(String(250))
    excerpt: Mapped[str] = mapped_column(Text)
    source_quality: Mapped[str] = mapped_column(String(40), default="WEAK_SECONDARY")
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Prospect(Base):
    __tablename__ = "prospects"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    person_id: Mapped[int | None] = mapped_column(ForeignKey("people.id"), index=True)
    email_id: Mapped[int | None] = mapped_column(ForeignKey("emails.id"), index=True)
    icp_score: Mapped[int] = mapped_column(Integer)
    deterministic_score: Mapped[int] = mapped_column(Integer, default=0)
    model_score: Mapped[int] = mapped_column(Integer, default=0)
    final_icp_score: Mapped[int] = mapped_column(Integer, default=0)
    buyer_score: Mapped[int] = mapped_column(Integer)
    confidence_score: Mapped[float] = mapped_column(Float)
    confidence_band: Mapped[str] = mapped_column(String(40))
    need_hypothesis: Mapped[str | None] = mapped_column(Text)
    personalization_angle: Mapped[str | None] = mapped_column(Text)
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), index=True)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    contacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    company: Mapped[Company] = relationship()
    person: Mapped[Person | None] = relationship()
    email: Mapped[Email | None] = relationship()


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_type: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(40), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_count: Mapped[int] = mapped_column(Integer, default=0)
    accepted_count: Mapped[int] = mapped_column(Integer, default=0)
    rejected_count: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    discovered_count: Mapped[int] = mapped_column(Integer, default=0)
    evidence_count: Mapped[int] = mapped_column(Integer, default=0)
    evaluated_count: Mapped[int] = mapped_column(Integer, default=0)
    insufficient_count: Mapped[int] = mapped_column(Integer, default=0)


class Suppression(Base):
    __tablename__ = "suppression"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str | None] = mapped_column(String(320), unique=True)
    domain: Mapped[str | None] = mapped_column(String(255), unique=True)
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


def create_database(path: str | Path = "emailfinder.db"):
    engine = create_engine(f"sqlite:///{Path(path)}")
    Base.metadata.create_all(engine)
    # Tiny forward-only compatibility step for Phase 1 local databases.
    from sqlalchemy import inspect, text
    columns = {c["name"] for c in inspect(engine).get_columns("evidence")}
    job_columns = {c["name"] for c in inspect(engine).get_columns("jobs")}
    with engine.begin() as connection:
        if "source_quality" not in columns: connection.execute(text("ALTER TABLE evidence ADD COLUMN source_quality VARCHAR(40) DEFAULT 'WEAK_SECONDARY'"))
        for name in ("discovered_count", "evidence_count", "evaluated_count", "insufficient_count"):
            if name not in job_columns: connection.execute(text(f"ALTER TABLE jobs ADD COLUMN {name} INTEGER DEFAULT 0"))
        prospect_columns = {c["name"] for c in inspect(engine).get_columns("prospects")}
        for name in ("deterministic_score", "model_score", "final_icp_score"):
            if name not in prospect_columns: connection.execute(text(f"ALTER TABLE prospects ADD COLUMN {name} INTEGER DEFAULT 0"))
    return engine


def normalize_domain(value: str) -> str:
    value = value.strip().lower()
    value = value.removeprefix("https://").removeprefix("http://").split("/", 1)[0]
    return value.removeprefix("www.")


def normalize_email(value: str) -> str:
    return value.strip().lower()


def person_key(full_name: str, domain: str) -> tuple[str, str]:
    return (" ".join(full_name.lower().split()), normalize_domain(domain))


def is_suppressed(session: Session, email: str, domain: str) -> bool:
    return session.scalar(select(Suppression.id).where(or_(Suppression.email == normalize_email(email), Suppression.domain == normalize_domain(domain)))) is not None
