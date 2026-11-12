from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON, BigInteger, Boolean, Date, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


ROLES = ("owner", "lawyer", "accountant", "technical", "viewer")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(300))
    full_name: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    telegram_chat_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    tg_link_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    tg_link_expires: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    digest_enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    memberships: Mapped[list[CompanyMember]] = relationship(back_populates="user", cascade="all, delete-orphan")


class CompanyMember(Base):
    __tablename__ = "company_members"
    __table_args__ = (UniqueConstraint("company_id", "user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20), default="viewer")

    company: Mapped[Company] = relationship(back_populates="members")
    user: Mapped[User] = relationship(back_populates="memberships")


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(primary_key=True)
    legal_name: Mapped[str] = mapped_column(String(300))
    stir: Mapped[str | None] = mapped_column(String(20))
    registration_date: Mapped[date | None] = mapped_column(Date)
    address: Mapped[str | None] = mapped_column(String(500))
    director: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(50))
    email: Mapped[str | None] = mapped_column(String(200))
    bank_details: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    members: Mapped[list[CompanyMember]] = relationship(back_populates="company", cascade="all, delete-orphan")
    documents: Mapped[list[CompanyDocument]] = relationship(back_populates="company", cascade="all, delete-orphan")
    attributes: Mapped[list[CompanyAttribute]] = relationship(back_populates="company", cascade="all, delete-orphan")
    contracts: Mapped[list[Contract]] = relationship(back_populates="company", cascade="all, delete-orphan")
    financials: Mapped[list[FinancialYear]] = relationship(back_populates="company", cascade="all, delete-orphan")
    tenders: Mapped[list[Tender]] = relationship(back_populates="company", cascade="all, delete-orphan")


class CompanyDocument(Base):
    __tablename__ = "company_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(50), index=True)
    title: Mapped[str] = mapped_column(String(300))
    issued_date: Mapped[date | None] = mapped_column(Date)
    expires_date: Mapped[date | None] = mapped_column(Date)
    # Facts printed on the document (stir, legal_name, ...) used for cross-document consistency checks.
    fields: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    filename: Mapped[str | None] = mapped_column(String(300))
    content_type: Mapped[str | None] = mapped_column(String(100))
    # Stored in the DB: serverless hosts have no persistent disk.
    content: Mapped[bytes | None] = mapped_column(LargeBinary, deferred=True)

    company: Mapped[Company] = relationship(back_populates="documents")


class CompanyAttribute(Base):
    """A measurable company fact, e.g. production capacity = 450 units/month."""

    __tablename__ = "company_attributes"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(300))
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(50))
    source: Mapped[str | None] = mapped_column(String(300))

    company: Mapped[Company] = relationship(back_populates="attributes")


class Contract(Base):
    __tablename__ = "contracts"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    customer: Mapped[str] = mapped_column(String(300))
    subject: Mapped[str] = mapped_column(String(500))
    amount_uzs: Mapped[float | None] = mapped_column(Float)
    completed_date: Mapped[date | None] = mapped_column(Date)
    similar: Mapped[bool] = mapped_column(Boolean, default=True)

    company: Mapped[Company] = relationship(back_populates="contracts")


class FinancialYear(Base):
    __tablename__ = "financial_years"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    year: Mapped[int] = mapped_column(Integer)
    revenue_uzs: Mapped[float] = mapped_column(Float)

    company: Mapped[Company] = relationship(back_populates="financials")


class Tender(Base):
    __tablename__ = "tenders"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(500))
    buyer: Mapped[str | None] = mapped_column(String(300))
    source_filename: Mapped[str | None] = mapped_column(String(300))
    deadline: Mapped[date | None] = mapped_column(Date)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    extraction_mode: Mapped[str] = mapped_column(String(20), default="rules")
    ocr_provider: Mapped[str | None] = mapped_column(String(20))
    analysis_seconds: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    company: Mapped[Company] = relationship(back_populates="tenders")
    requirements: Mapped[list[Requirement]] = relationship(
        back_populates="tender", cascade="all, delete-orphan", order_by="Requirement.id"
    )
    tasks: Mapped[list[Task]] = relationship(back_populates="tender", cascade="all, delete-orphan")
    timings: Mapped[list[PilotTiming]] = relationship(back_populates="tender", cascade="all, delete-orphan")


class Requirement(Base):
    __tablename__ = "requirements"

    id: Mapped[int] = mapped_column(primary_key=True)
    tender_id: Mapped[int] = mapped_column(ForeignKey("tenders.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(20))
    label: Mapped[str] = mapped_column(String(300))
    doc_kind: Mapped[str | None] = mapped_column(String(50))
    subject: Mapped[str | None] = mapped_column(String(500))
    op: Mapped[str | None] = mapped_column(String(4))
    value: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(50))
    currency: Mapped[str | None] = mapped_column(String(10))
    due: Mapped[date | None] = mapped_column(Date)
    critical: Mapped[bool] = mapped_column(Boolean, default=False)
    quote: Mapped[str] = mapped_column(Text)
    page: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(10), default="rules")

    tender: Mapped[Tender] = relationship(back_populates="requirements")


class Task(Base):
    """Team workspace: who does what by when."""

    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    tender_id: Mapped[int] = mapped_column(ForeignKey("tenders.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    role: Mapped[str] = mapped_column(String(30), default="other")  # lawyer | accountant | technical | other
    due: Mapped[date | None] = mapped_column(Date)
    done: Mapped[bool] = mapped_column(Boolean, default=False)

    tender: Mapped[Tender] = relationship(back_populates="tasks")


class PilotTiming(Base):
    """Self-reported pilot measurement: how long the same tender takes manually vs. with the tool."""

    __tablename__ = "pilot_timings"

    id: Mapped[int] = mapped_column(primary_key=True)
    tender_id: Mapped[int] = mapped_column(ForeignKey("tenders.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    manual_minutes: Mapped[float] = mapped_column(Float)
    assisted_minutes: Mapped[float] = mapped_column(Float)
    note: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    tender: Mapped[Tender] = relationship(back_populates="timings")
