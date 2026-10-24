"""Renders shots from the graph: FLUX.2 character references and keyframes (Black Forest Labs), then
image-to-video on fal (default) or MachGen. Every artifact is recorded on its node in the graph."""

from __future__ import annotations

import base64
import os
import subprocess
import tempfile
import time
from pathlib import Path

import fal_client
import httpx
from falkordb import Graph

from .prompts import active_outfit, compile_episode

BFL_API = "https://api.bfl.ai/v1"
BFL_MODEL = os.environ.get("THROUGHLINE_IMAGE_MODEL", "flux-2-pro")
FAL_VIDEO_MODEL = os.environ.get("THROUGHLINE_FAL_VIDEO_MODEL", "fal-ai/kling-video/v3/standard/image-to-video")
MACHGEN_API = "https://api.machgen.ai/api/v0"
MACHGEN_VIDEO_MODEL = os.environ.get("THROUGHLINE_MACHGEN_VIDEO_MODEL", "Kling-v3")
VERTICAL = (768, 1344)  # 9:16, multiples of 16 as FLUX.2 requires


class RenderError(RuntimeError):
    pass


# ---------- Black Forest Labs ----------

def _bfl(prompt: str, refs: list[Path], size: tuple[int, int], timeout: float = 180) -> bytes:
    key = os.environ.get("BFL_API_KEY") or _missing("BFL_API_KEY")
    body: dict = {"prompt": prompt, "width": size[0], "height": size[1], "output_format": "png"}
    for i, ref in enumerate(refs[:8]):
        body["input_image" if i == 0 else f"input_image_{i + 1}"] = base64.b64encode(ref.read_bytes()).decode()
    with httpx.Client(headers={"x-key": key}, timeout=60) as http:
        job = http.post(f"{BFL_API}/{BFL_MODEL}", json=body)
        if job.is_error:
            raise RenderError(f"BFL {job.status_code}: {job.text[:300]}")
        polling_url = job.json()["polling_url"]
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = http.get(polling_url).json()
            status = result.get("status")
            if status == "Ready":
                return http.get(result["result"]["sample"]).content  # signed URL, valid 10 minutes
            if status not in ("Pending", "Processing", "Queued"):
                raise RenderError(f"BFL generation {status}: {result.get('error') or result}")
            time.sleep(1.5)
    raise RenderError(f"BFL generation still pending after {timeout}s")


def reference_path(out_dir: Path, cid: str, outfit: str) -> Path:
    return out_dir / f"{cid}__{outfit}.png"


def character_reference(g: Graph, cid: str, outfit: str, out_dir: Path, style: str) -> Path:
    """One full-body reference image per (character, outfit), made once and reused for every shot.
    A new outfit is drawn from an existing reference of the same character, so the face stays the same."""
    path = reference_path(out_dir, cid, outfit)
    if not path.exists():
        name, look = g.query("MATCH (c:Character {id: $cid}) RETURN c.name, c.canonical_look", {"cid": cid}).result_set[0]
        description = g.query("MATCH (o:Outfit {id: $id}) RETURN o.description", {"id": outfit}).result_set[0][0]
        existing = sorted(out_dir.glob(f"{cid}__*.png"))
        same_person = "The same person as image 1, same face and hair, " if existing else ""
        prompt = (
            f"Character reference sheet, single full-body front view on a plain dark grey background. {same_person}"
            f"{name}: {look}, wearing {description}. Neutral pose, even lighting, face clearly visible. "
            f"Style: {style}. No text, no labels."
        )
        out_dir.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_bfl(prompt, existing[:1], (768, 1024)))
    return path


def avatar_path(out_dir: Path, cid: str) -> Path:
    return out_dir / f"{cid}.png"


def character_avatar(g: Graph, cid: str, render_dir: Path) -> Path:
    """Square head-and-shoulders portrait for the UI, drawn from the character's reference in their default outfit."""
    path = avatar_path(render_dir / "avatars", cid)
    if not path.exists():
        style = g.query("MATCH (s:Series) RETURN s.style LIMIT 1").result_set[0][0]
        name, outfit = g.query(
            "MATCH (c:Character {id: $cid})-[:WEARS {from_order: 0}]->(o:Outfit) RETURN c.name, o.id", {"cid": cid}
        ).result_set[0]
        ref = character_reference(g, cid, outfit, render_dir / "characters", style)
        prompt = (
            f"Head-and-shoulders portrait of the person in image 1 ({name}): same face, hair and outfit, looking at the "
            f"camera, centered, plain dark background with a soft magenta and teal rim light. Any scars are old, faint and "
            f"fully healed. Style: {style}. No text."
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_bfl(prompt, [ref], (512, 512)))
    g.query("MATCH (c:Character {id: $cid}) SET c.avatar = $path", {"cid": cid, "path": str(path)})
    return path


def shot_cast(g: Graph, shot_id: str) -> list[tuple[str, str, str]]:
    """(character id, name, outfit id) for everyone in the shot, with the outfit from the wardrobe timeline."""
    order = g.query("MATCH (s:Scene)-[:HAS_SHOT]->(:Shot {id: $shot}) RETURN s.order", {"shot": shot_id}).result_set[0][0]
    cast = g.query("MATCH (c:Character)-[:IN_SHOT]->(:Shot {id: $shot}) RETURN c.id, c.name ORDER BY c.id", {"shot": shot_id}).result_set
    return [(cid, name, active_outfit(g, cid, order)[0]) for cid, name in cast]


def keyframe(g: Graph, shot_id: str, prompt: str, out_dir: Path) -> Path:
    """First frame of a shot, conditioned on each character's reference in the outfit they wear in that scene."""
    style = g.query("MATCH (s:Series) RETURN s.style LIMIT 1").result_set[0][0]
    cast = shot_cast(g, shot_id)
    refs = [character_reference(g, cid, outfit, out_dir.parent / "characters", style) for cid, _, outfit in cast]
    mapping = " ".join(f"Image {i + 1} is {name}; keep their face, hair and outfit exactly." for i, (_, name, _) in enumerate(cast))
    path = out_dir / f"{shot_id}.png"
    out_dir.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_bfl(f"{mapping} Cinematic film still, first frame of the shot. {prompt}", refs, VERTICAL))
    return path


# ---------- image to video ----------

def _fal_video(image_url: str, prompt: str, seconds: int) -> str:
    os.environ.get("FAL_KEY") or _missing("FAL_KEY")
    result = fal_client.subscribe(
        FAL_VIDEO_MODEL,
        arguments={"start_image_url": image_url, "prompt": prompt, "duration": str(seconds), "generate_audio": False},
        client_timeout=600,
    )
    return result["video"]["url"]


def _machgen_video(image_url: str, prompt: str, seconds: int, out: Path, timeout: float = 900) -> None:
    key = os.environ.get("MACHGEN_API_KEY") or _missing("MACHGEN_API_KEY")
    with httpx.Client(headers={"Authorization": f"Bearer {key}"}, timeout=60) as http:
        job = http.post(f"{MACHGEN_API}/generate", json={
            "model": MACHGEN_VIDEO_MODEL, "task_type": "I2V", "prompt": prompt, "src_image_urls": [image_url],
            "video_config": {"duration_secs": seconds, "height": 720, "aspect_ratio": "9:16"},
        })
        if job.is_error:
            raise RenderError(f"MachGen {job.status_code}: {job.text[:300]}")
        task_id = job.json()["task_id"]
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            task = http.get(f"{MACHGEN_API}/tasks/{task_id}").json()
            if task["status"] == "COMPLETED":
                out.write_bytes(http.get(f"{MACHGEN_API}/assets/{task_id}", follow_redirects=True).content)
                return
            if task["status"] == "FAILED":
                raise RenderError(f"MachGen task {task_id} failed: {task.get('error_msg')}")
            time.sleep(5)
    raise RenderError(f"MachGen task {task_id} still running after {timeout}s")


def generate_shot(g: Graph, render_dir: Path, shot_id: str, seconds: int = 5, provider: str | None = None) -> dict:
    provider = provider or os.environ.get("THROUGHLINE_VIDEO_PROVIDER", "fal")
    episode = int(shot_id.split("S")[0].lstrip("E"))
    compiled = next((p for p in compile_episode(g, episode) if p.shot == shot_id), None)
    if compiled is None:
        raise ValueError(f"no shot {shot_id}")

    frame = keyframe(g, shot_id, compiled.prompt, render_dir / "keyframes")
    frame_url = fal_client.upload_file(frame)  # public URL both video providers can fetch
    clip = render_dir / "clips" / f"{shot_id}.mp4"
    clip.parent.mkdir(parents=True, exist_ok=True)
    if provider == "fal":
        clip.write_bytes(httpx.get(_fal_video(frame_url, compiled.prompt, seconds), timeout=120, follow_redirects=True).content)
        model = FAL_VIDEO_MODEL
    elif provider == "machgen":
        _machgen_video(frame_url, compiled.prompt, seconds, clip)
        model = MACHGEN_VIDEO_MODEL
    else:
        raise ValueError(f"unknown video provider {provider!r} (use 'fal' or 'machgen')")

    g.query(
        """MATCH (t:Shot {id: $id})
           SET t.keyframe = $frame, t.clip_path = $clip, t.rendered_prompt = $prompt,
               t.render_model = $model, t.rendered_at = timestamp()""",
        {"id": shot_id, "frame": str(frame), "clip": str(clip), "prompt": compiled.prompt, "model": f"{provider}:{model}"},
    )
    return {
        "shot": shot_id, "keyframe": str(frame), "clip": str(clip), "seconds": seconds,
        "image_model": f"bfl:{BFL_MODEL}", "video_model": f"{provider}:{model}", "corrections": compiled.corrections,
    }


# ---------- whole episodes ----------

# Rough list prices in USD, for telling the user what a render will cost before it runs.
IMAGE_COST = 0.045  # FLUX.2 [pro], one keyframe or reference image
VIDEO_COST_PER_SECOND = {"fal": 0.084, "machgen": 0.084}  # Kling v3 Standard, no audio


def episode_shots(g: Graph, episode: int) -> list[tuple[str, str]]:
    """(shot id, scene id) in story order."""
    return [tuple(r) for r in g.query(
        "MATCH (:Episode {num: $ep})-[:HAS_SCENE]->(s:Scene)-[:HAS_SHOT]->(t:Shot) RETURN t.id, s.id ORDER BY t.order",
        {"ep": episode},
    ).result_set]


def estimate_cost(g: Graph, render_dir: Path, episode: int, seconds: int = 5, provider: str = "fal", force: bool = False) -> dict:
    shots = [shot for shot, _ in episode_shots(g, episode) if force or not (render_dir / "clips" / f"{shot}.mp4").exists()]
    refs = {
        reference_path(render_dir / "characters", cid, outfit)
        for shot in shots for cid, _, outfit in shot_cast(g, shot)
    }
    new_refs = [r for r in refs if not r.exists()]
    usd = len(shots) * (IMAGE_COST + seconds * VIDEO_COST_PER_SECOND[provider]) + len(new_refs) * IMAGE_COST
    return {"episode": episode, "shots_to_render": shots, "new_reference_images": len(new_refs), "seconds_per_shot": seconds, "usd": round(usd, 2)}


def render_episode(g: Graph, render_dir: Path, episode: int, seconds: int = 5, provider: str = "fal", force: bool = False) -> dict:
    """Render every shot that has no clip yet (or all, with force), then assemble the episode.
    A failed shot is reported and skipped, so a rerun picks up where this one stopped."""
    rendered, skipped, failed = [], [], {}
    for shot, _ in episode_shots(g, episode):
        if not force and (render_dir / "clips" / f"{shot}.mp4").exists():
            skipped.append(shot)
            continue
        try:
            generate_shot(g, render_dir, shot, seconds, provider)
            rendered.append(shot)
        except Exception as exc:  # keep going; one bad shot shouldn't waste the rest of the batch
            failed[shot] = str(exc)
    result = {"episode": episode, "rendered": rendered, "already_rendered": skipped, "failed": failed}
    if not failed:
        result["episode_video"] = str(assemble_episode(g, render_dir, episode))
    return result


def assemble_episode(g: Graph, render_dir: Path, episode: int, fade: float = 0.3) -> Path:
    """Join the shot clips into one vertical episode, fading in and out at scene boundaries."""
    shots = episode_shots(g, episode)
    missing = [shot for shot, _ in shots if not (render_dir / "clips" / f"{shot}.mp4").exists()]
    if missing:
        raise RenderError(f"episode {episode} is missing clips for {', '.join(missing)}")
    out = render_dir / "episodes" / f"ep{episode}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        parts = []
        for i, (shot, scene) in enumerate(shots):
            clip = render_dir / "clips" / f"{shot}.mp4"
            duration = float(subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(clip)],
                capture_output=True, text=True, check=True,
            ).stdout)
            filters = ["scale=720:1280:force_original_aspect_ratio=decrease", "pad=720:1280:(ow-iw)/2:(oh-ih)/2", "fps=24"]
            if i == 0 or shots[i - 1][1] != scene:
                filters.append(f"fade=t=in:st=0:d={fade}")
            if i == len(shots) - 1 or shots[i + 1][1] != scene:
                filters.append(f"fade=t=out:st={max(duration - fade, 0):.3f}:d={fade}")
            part = Path(tmp) / f"{i:03d}.mp4"
            subprocess.run(
                ["ffmpeg", "-v", "error", "-y", "-i", str(clip), "-vf", ",".join(filters), "-an",
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", str(part)],
                check=True,
            )
            parts.append(part)
        listing = Path(tmp) / "parts.txt"
        listing.write_text("".join(f"file '{p}'\n" for p in parts))
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(out)], check=True)
    g.query("MATCH (e:Episode {num: $ep}) SET e.video_path = $path", {"ep": episode, "path": str(out)})
    return out


def _missing(var: str):
    raise RenderError(f"{var} is not set")
