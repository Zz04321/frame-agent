from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Anchor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=100)
    evidence_segment_ids: list[str] = Field(min_length=1)


class Chapter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=100)
    summary: str = Field(min_length=1, max_length=4000)
    segment_ids: list[str] = Field(min_length=1)
    anchors: list[Anchor] = Field(min_length=1)


class Analysis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    overview: str = Field(min_length=1, max_length=4000)
    chapters: list[Chapter] = Field(min_length=1, max_length=30)


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["llm"] = "llm"


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    query: str = Field(min_length=1, max_length=500)


class SearchHit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=1000)
    segment_ids: list[str] = Field(min_length=1, max_length=12)


class SearchAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: str = Field(min_length=1, max_length=2000)
    matches: list[SearchHit] = Field(max_length=8)
