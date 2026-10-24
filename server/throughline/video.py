"""Renders shots from the graph: FLUX.2 character references and keyframes (Black Forest Labs), then
image-to-video on fal (default) or MachGen. Every artifact is recorded on its node in the graph."""

from __future__ import annotations

import base64
import os
import time
from pathlib import Path

import fal_client
import httpx
from falkordb import Graph

from .prompts import compile_episode

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


def character_reference(g: Graph, cid: str, out_dir: Path, style: str) -> Path:
    """One full-body reference image per character, made once from the bible and reused for every shot."""
    path = out_dir / f"{cid}.png"
    if not path.exists():
        name, look, outfit = g.query(
            """MATCH (c:Character {id: $cid})-[w:WEARS]->(o:Outfit)
               RETURN c.name, c.canonical_look, o.description ORDER BY w.from_order DESC LIMIT 1""",
            {"cid": cid},
        ).result_set[0]
        prompt = (
            f"Character reference sheet, single full-body front view on a plain dark grey background: {name}, {look}, "
            f"wearing {outfit}. Neutral pose, even lighting, face clearly visible. Style: {style}. No text, no labels."
        )
        out_dir.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_bfl(prompt, [], (768, 1024)))
    g.query("MATCH (c:Character {id: $cid}) SET c.ref_image = $path", {"cid": cid, "path": str(path)})
    return path


def keyframe(g: Graph, shot_id: str, prompt: str, out_dir: Path) -> Path:
    """First frame of a shot, conditioned on the reference image of every character in it."""
    style = g.query("MATCH (s:Series) RETURN s.style LIMIT 1").result_set[0][0]
    cast = g.query("MATCH (c:Character)-[:IN_SHOT]->(:Shot {id: $shot}) RETURN c.id, c.name ORDER BY c.id", {"shot": shot_id}).result_set
    refs = [character_reference(g, cid, out_dir.parent / "characters", style) for cid, _ in cast]
    mapping = " ".join(f"Image {i + 1} is {name}; keep their face, hair and outfit exactly." for i, (_, name) in enumerate(cast))
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


def _missing(var: str):
    raise RenderError(f"{var} is not set")
