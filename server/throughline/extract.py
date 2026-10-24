"""Turns an episode script into structured story events with OpenAI, cached per episode on disk."""

from __future__ import annotations

import json
import os
from pathlib import Path

from openai import OpenAI

from . import fountain
from .models import EpisodeExtract, SceneExtract

DEFAULT_MODEL = os.environ.get("THROUGHLINE_MODEL", "gpt-5.5")

INSTRUCTIONS = """You are a script supervisor for an AI-generated video series.
Extract the continuity facts of ONE episode as structured data.

Rules:
- Use the ids from the series bible for characters, outfits, locations and props. Invent a new snake_case id only for something the bible does not have.
- Reuse fact ids from KNOWN FACTS whenever a line refers to one of them.
- Split every scene into the shots a video model would render (one visible action each) and list each shot's cast.
  Set a cast member's outfit ONLY when the script text in that scene describes what they wear. Otherwise outfit is null:
  the series graph already tracks their current look. Never fall back to the bible's default outfit.
- Events:
  - establishes: a story fact becomes true or is revealed. known_by = every character who sees or hears it.
    kind: secret (hidden information), setup (something planted to pay off later), cliffhanger (an episode-ending open question), event.
    Questions, reactions and banter are not facts.
  - references: a character says or acts on a fact they would need to already know.
  - tells: a character passes a fact to another character on screen.
  - resolves: a cliffhanger or setup is paid off.
  - prop_state: a prop appears, is held, destroyed, lost or recovered.
  - outfit_change: a character's look changes on screen.
  - dies: a character dies.
- quote: copy a short exact snippet (3-10 words) from the script line that proves the event or shot. Never paraphrase.
"""


def _prompt(script: fountain.Script, episode: int, bible: dict, known_facts: dict[str, str]) -> str:
    return "\n\n".join(
        [
            f"EPISODE: {episode}",
            "SERIES BIBLE:\n" + json.dumps(bible, indent=2),
            "KNOWN FACTS (id: text):\n" + "\n".join(f"{k}: {v}" for k, v in known_facts.items()),
            "SCRIPT:\n" + "\n".join(script.lines),
        ]
    )


def extract_episode(
    script: fountain.Script, episode: int, bible: dict, known_facts: dict[str, str], model: str = DEFAULT_MODEL
) -> EpisodeExtract:
    client = OpenAI()
    response = client.responses.parse(
        model=model,
        instructions=INSTRUCTIONS,
        input=_prompt(script, episode, bible, known_facts),
        text_format=EpisodeExtract,
    )
    return response.output_parsed


def extract_scene(
    script: fountain.Script, episode: int, scene_num: int, previous: SceneExtract, bible: dict,
    known_facts: dict[str, str], model: str = DEFAULT_MODEL,
) -> SceneExtract:
    """Re-extract one edited scene, using its previous extraction as the baseline so only the edit's effects change."""
    scene = script.scenes[scene_num - 1]
    response = OpenAI().responses.parse(
        model=model,
        instructions=INSTRUCTIONS + (
            "\nYou are updating ONE scene after a script edit. Start from PREVIOUS EXTRACTION and change only what the "
            "edited text changes. Keep ids, shot numbering and granularity identical everywhere else. Do not add new facts "
            "unless the edit itself introduces one."
        ),
        input="\n\n".join([
            f"EPISODE: {episode}, SCENE: {scene_num}",
            "SERIES BIBLE:\n" + json.dumps(bible, indent=2),
            "KNOWN FACTS (id: text):\n" + "\n".join(f"{k}: {v}" for k, v in known_facts.items()),
            "PREVIOUS EXTRACTION:\n" + previous.model_dump_json(indent=2),
            "EDITED SCENE:\n" + "\n".join([scene.heading, *(text for _, text in scene.lines)]),
        ]),
        text_format=SceneExtract,
    )
    return response.output_parsed


def load_or_extract(series_dir: Path, episode: int, known_facts: dict[str, str], live: bool = False) -> EpisodeExtract:
    """Use the cached extraction unless `live` is set or no cache exists yet."""
    cache = series_dir / "extracted" / f"ep{episode}.json"
    if cache.exists() and not live:
        return EpisodeExtract.model_validate_json(cache.read_text())
    script = fountain.parse_file(series_dir / "scripts" / f"ep{episode}.fountain")
    bible = json.loads((series_dir / "bible.json").read_text())
    extracted = extract_episode(script, episode, bible, known_facts)
    cache.parent.mkdir(exist_ok=True)
    cache.write_text(extracted.model_dump_json(indent=2) + "\n")
    return extracted
