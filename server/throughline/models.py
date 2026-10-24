"""Schema for what the extractor returns per episode. Mirrors samples/*/extracted/*.json."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

FactKind = Literal["secret", "setup", "cliffhanger", "event"]
PropState = Literal["present", "held", "destroyed", "lost", "recovered"]


class Cast(BaseModel):
    character: str
    outfit: str | None = Field(description="outfit id only when the script describes the look in this scene, else null")


class Shot(BaseModel):
    num: int
    description: str
    characters: list[Cast] = Field(default_factory=list)
    props: list[str] = Field(default_factory=list)
    quote: str


class Event(BaseModel):
    type: Literal["establishes", "references", "tells", "resolves", "prop_state", "outfit_change", "dies"]
    quote: str
    character: str | None = None
    to: str | None = None
    fact: str | None = None
    kind: FactKind | None = None
    text: str | None = None
    known_by: list[str] = Field(default_factory=list)
    prop: str | None = None
    state: PropState | None = None
    holder: str | None = None
    outfit: str | None = None


class SceneExtract(BaseModel):
    num: int
    heading: str
    location: str
    characters: list[str]
    shots: list[Shot]
    events: list[Event]
    flashback: bool = False


class EpisodeExtract(BaseModel):
    episode: int
    title: str
    scenes: list[SceneExtract]
