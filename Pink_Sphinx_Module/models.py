from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


Mode = Literal["normal", "strict", "savage", "roast"]
Judgement = Literal["PASS", "CONDITIONAL_PASS", "REVISION_REQUIRED", "REJECT"]
Area = Literal["purpose", "scope", "feasibility", "validation", "deliverable", "logic"]


class WonConfirmRequest(BaseModel):
    text: str = Field(..., min_length=1, description="Text to review")
    mode: Mode = Field(default="normal", description="Review mode")
    rewrite: bool = Field(default=True, description="Whether to produce a structured rewrite")


class Score(BaseModel):
    purpose: int = Field(ge=0, le=2)
    scope: int = Field(ge=0, le=2)
    feasibility: int = Field(ge=0, le=2)
    validation: int = Field(ge=0, le=2)
    deliverable: int = Field(ge=0, le=2)
    logic: int = Field(ge=0, le=2)
    total: int = Field(ge=0, le=12)


class WonConfirmResponse(BaseModel):
    judgement: Judgement
    score: Score
    weakest_area: Area
    sarcastic_slot: str
    professor_comment: str
    problems: list[str]
    revision_orders: list[str]
    rewritten_version: str

