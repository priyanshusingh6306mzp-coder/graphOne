"""Pydantic models matching the prompt's schemas exactly (plus source/collectedAt on all records).

Internal code uses clean attribute access (record.source.url). For Sheets, call
`record.model_dump(mode="json")` and pass the dict to export.flatten.to_row().
"""
from datetime import datetime, timezone
from enum import Enum
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

SCHEMA_VERSION = "1.0"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(v: Optional[datetime]) -> Optional[datetime]:
    if v is not None and v.tzinfo is None:
        return v.replace(tzinfo=timezone.utc)
    return v


class PricingModel(str, Enum):
    FREE = "FREE"
    FREEMIUM = "FREEMIUM"
    PAID = "PAID"
    ENTERPRISE = "ENTERPRISE"


class Source(BaseModel):
    name: str
    url: str

    @field_validator("url")
    @classmethod
    def _http_url(cls, v: str) -> str:
        if not v.lower().startswith(("http://", "https://")):
            raise ValueError("source.url must be an http(s) URL")
        return v


class _Record(BaseModel):
    schemaVersion: str = SCHEMA_VERSION
    collectedAt: datetime = Field(default_factory=utcnow)

    @field_validator("collectedAt")
    @classmethod
    def _c(cls, v):
        return _aware(v)


# ---- STARTUP ----
class StartupData(BaseModel):
    employeeCount: Optional[int] = None


class StartupContent(BaseModel):
    entityName: str
    data: StartupData = StartupData()


class Startup(_Record):
    recordType: Literal["STARTUP"] = "STARTUP"
    source: Source
    content: StartupContent


# ---- PRODUCT ----
class ProductContent(BaseModel):
    startupName: str
    pricingModel: PricingModel


class Product(_Record):
    recordType: Literal["PRODUCT"] = "PRODUCT"
    source: Source
    content: ProductContent


# ---- RESEARCH_PAPER ----
class PaperContent(BaseModel):
    title: str
    authors: List[str]
    paper_url: str
    github_url: Optional[str] = None
    github_stars: Optional[int] = None
    published_date: datetime

    @field_validator("published_date")
    @classmethod
    def _d(cls, v):
        return _aware(v)


class ResearchPaper(_Record):
    recordType: Literal["RESEARCH_PAPER"] = "RESEARCH_PAPER"
    source: Source
    content: PaperContent


# ---- JOB ----
class JobContent(BaseModel):
    company: str
    title: Optional[str] = None
    date: datetime
    is_remote: bool
    role_family: str

    @field_validator("date")
    @classmethod
    def _d(cls, v):
        return _aware(v)


class Job(_Record):
    recordType: Literal["JOB"] = "JOB"
    source: Source
    content: JobContent


# ---- NEWS (schema not given in the prompt; assumption documented in docs/DECISIONS.md) ----
class NewsContent(BaseModel):
    title: str
    date: datetime
    summary: Optional[str] = None
    entities: List[str] = []

    @field_validator("date")
    @classmethod
    def _d(cls, v):
        return _aware(v)


class News(_Record):
    recordType: Literal["NEWS"] = "NEWS"
    source: Source
    content: NewsContent
