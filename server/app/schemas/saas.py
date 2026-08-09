"""Public Swyingify SaaS scan response models (no money-path fields)."""

from __future__ import annotations

import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class SaasQualificationComponent(BaseModel):
    key: str
    label: str
    shortLabel: str
    score: float
    maxScore: float
    status: Literal["strong", "supporting", "watch"]
    summary: str


class SaasQualificationFingerprint(BaseModel):
    strongCount: int
    totalCount: int
    components: list[SaasQualificationComponent]


class SaasDailyCandle(BaseModel):
    time: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    sma50: float | None = None
    sma150: float | None = None
    sma200: float | None = None


class SaasScanLatestResponse(BaseModel):
    family: str = "minervini"
    code: str = "standard"
    asOfDate: str | None = None
    status: str
    completedAt: datetime.datetime | None = None
    resultCount: int = 0
    scanRunId: UUID | None = None
    message: str | None = None


class SaasScannerResult(BaseModel):
    id: str
    symbol: str
    companyName: str
    sector: str
    preset: Literal["standard"] = "standard"
    rank: int
    asOfDate: str
    close: float
    technicalScore: float
    grade: Literal["A", "B", "C"] | str
    rsRating: int
    pctFrom52WeekHigh: float
    adtvCrore: float
    dayChangePct: float
    sparkSeries: list[float] = Field(default_factory=list)
    atrRatio: float
    volumeDryUpRatio: float
    fingerprint: SaasQualificationFingerprint
    candles: list[SaasDailyCandle] = Field(default_factory=list)


class SaasScanResultsResponse(BaseModel):
    family: str = "minervini"
    code: str = "standard"
    asOfDate: str
    status: str
    completedAt: datetime.datetime | None = None
    scanRunId: UUID | None = None
    results: list[SaasScannerResult]


class SaasCandlesResponse(BaseModel):
    symbol: str
    companyName: str
    asOfDate: str | None = None
    candles: list[SaasDailyCandle]
