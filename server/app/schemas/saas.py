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
    code: Literal["standard", "strict"] = "standard"
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
    preset: Literal["standard", "strict", "custom"] = "standard"
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
    code: Literal["standard", "strict"] = "standard"
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


class SaasVariantCreateRequest(BaseModel):
    minRsRating: Literal[60, 70, 80, 90] = 80
    maxDistance52WeekHighPct: Literal[5, 10, 15, 25] = 15
    minAdtvCrore: Literal[10, 25, 50, 100] = 25
    stage2ChecksRequired: Literal[4, 5] = 5
    contraction: Literal["balanced", "tight", "very_tight"] = "tight"
    volumeDryUp: Literal["normal", "strong", "extreme"] = "strong"
    minimumTechnicalScore: Literal[70, 80, 90] = 80


class SaasVariantRunResponse(BaseModel):
    runId: UUID
    status: str
    asOfDate: str
    quotaRemaining: int
    results: list[SaasScannerResult] = Field(default_factory=list)
    message: str | None = None
