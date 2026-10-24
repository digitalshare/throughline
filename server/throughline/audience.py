"""Audience loop: Bright Data pulls posts, comments and niche trends; OpenAI links comments to the story graph."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Literal

import httpx
from falkordb import Graph
from openai import OpenAI
from pydantic import BaseModel

from .extract import DEFAULT_MODEL

API = "https://api.brightdata.com/datasets/v3"
DATASETS = {
    "tiktok_posts": "gd_lu702nij2f790tmv9h",
    "tiktok_comments": "gd_lkf2st302ap89utw5k",
    "youtube_videos": "gd_lk56epmy2i5g7lzu0k",
    "youtube_comments": "gd_lk9q0ew71spt1mxywf",
}


class BrightDataError(RuntimeError):
    pass


class BrightData:
    """Async-job client for Bright Data's Web Scraper API: trigger, poll, download."""

    def __init__(self, token: str | None = None, timeout: float = 900, poll_every: float = 5):
        token = token or os.environ.get("BRIGHTDATA_API_TOKEN")
        if not token:
            raise BrightDataError("BRIGHTDATA_API_TOKEN is not set")
        self.http = httpx.Client(headers={"Authorization": f"Bearer {token}"}, timeout=60)
        self.timeout = timeout
        self.poll_every = poll_every

    def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        for attempt in range(3):
            try:
                response = self.http.request(method, url, **kwargs)
            except httpx.TransportError as exc:
                if attempt == 2:
                    raise BrightDataError(f"network error calling Bright Data: {exc}") from exc
            else:
                if response.status_code < 500 and response.status_code != 429:
                    if response.is_error:
                        raise BrightDataError(f"Bright Data {response.status_code}: {response.text[:300]}")
                    return response
                if attempt == 2:
                    raise BrightDataError(f"Bright Data {response.status_code} after retries: {response.text[:300]}")
            time.sleep(2**attempt)
        raise AssertionError("unreachable")

    def collect(self, dataset: str, inputs: list[dict], discover_by: str | None = None, limit: int | None = None) -> list[dict]:
        params: dict = {"dataset_id": DATASETS.get(dataset, dataset), "include_errors": "true"}
        if discover_by:
            params |= {"type": "discover_new", "discover_by": discover_by}
        if limit:
            params["limit_per_input"] = limit
        snapshot = self._request("POST", f"{API}/trigger", params=params, json=inputs).json()["snapshot_id"]

        deadline = time.monotonic() + self.timeout
        while True:
            status = self._request("GET", f"{API}/progress/{snapshot}").json().get("status")
            if status == "ready":
                break
            if status in ("failed", "canceled"):
                raise BrightDataError(f"snapshot {snapshot} {status}")
            if time.monotonic() > deadline:
                raise BrightDataError(f"snapshot {snapshot} still {status} after {self.timeout}s")
            time.sleep(self.poll_every)
        records = self._request("GET", f"{API}/snapshot/{snapshot}", params={"format": "json"}).json()
        return [r for r in records if isinstance(r, dict)]


# ---------- normalising raw records (field names differ per platform) ----------

def _first(record: dict, *keys, default=None):
    for key in keys:
        if record.get(key) not in (None, ""):
            return record[key]
    return default


def normalize_post(record: dict) -> dict | None:
    url = _first(record, "url", "post_url")
    if not url or record.get("error"):
        return None
    tags = _first(record, "hashtags", "tags", default=[]) or []
    return {
        "url": url,
        "platform": "youtube" if "youtube.com" in url or "youtu.be" in url else "tiktok",
        "caption": _first(record, "description", "title", "caption", default=""),
        "author": _first(record, "profile_username", "account_id", "youtuber", "channel_name", default=""),
        "views": int(_first(record, "play_count", "views", "view_count", default=0) or 0),
        "likes": int(_first(record, "digg_count", "likes", "like_count", default=0) or 0),
        "comments": int(_first(record, "comment_count", "num_comments", default=0) or 0),
        "shares": int(_first(record, "share_count", "shares", default=0) or 0),
        "hashtags": sorted({str(t).lstrip("#").lower() for t in tags if t}),
        "sound": _first(record, "music", "original_sound", default=None),
    }


def normalize_comment(record: dict) -> dict | None:
    text = _first(record, "comment_text", "text")
    if not text or record.get("error"):
        return None
    return {
        "id": str(_first(record, "comment_id", "id", default=hash(text))),
        "text": text,
        "likes": int(_first(record, "num_likes", "likes", default=0) or 0),
        "date": _first(record, "date_created", "date_iso", "date", default=None),
        "post_url": _first(record, "post_url", "url", default=None),
    }


# ---------- linking comments to the story ----------

class CommentLink(BaseModel):
    comment_id: str
    characters: list[str]
    facts: list[str]
    topic: Literal["continuity", "story", "visual", "character", "praise", "other"]
    sentiment: Literal["positive", "neutral", "negative"]
    confused: bool


class CommentLinks(BaseModel):
    links: list[CommentLink]


def link_comments(g: Graph, comments: list[dict], model: str = DEFAULT_MODEL) -> list[CommentLink]:
    characters = dict(g.query("MATCH (c:Character) RETURN c.id, c.name + ': ' + c.canonical_look").result_set)
    facts = dict(g.query("MATCH (f:Fact) RETURN f.id, f.text").result_set)
    response = OpenAI().responses.parse(
        model=model,
        instructions=(
            "Link viewer comments on an episodic AI video series to the series graph. For each comment, list the "
            "character ids and fact ids it is about (use only the given ids), its topic, sentiment, and whether the "
            "viewer is confused or points out an inconsistency (confused=true). 'continuity' = the viewer noticed "
            "something that doesn't match earlier episodes; 'visual' = the look of a character or scene."
        ),
        input=json.dumps({"characters": characters, "facts": facts, "comments": [{"id": c["id"], "text": c["text"]} for c in comments]}),
        text_format=CommentLinks,
    )
    known = set(characters) | set(facts)
    links = response.output_parsed.links
    for link in links:  # drop any id the model invented
        link.characters = [c for c in link.characters if c in known]
        link.facts = [f for f in link.facts if f in known]
    return links


# ---------- graph ----------

def load_episode_post(g: Graph, episode: int, post: dict, comments: list[dict], links: list[CommentLink]) -> None:
    g.query(
        """MATCH (e:Episode {num: $ep}) SET e.published_url = $url
           MERGE (p:Post {url: $url})
           SET p.platform = $platform, p.views = $views, p.likes = $likes, p.comments = $comments, p.shares = $shares,
               p.fetched_at = timestamp()
           MERGE (p)-[:OF]->(e)""",
        {"ep": episode, **{k: post[k] for k in ("url", "platform", "views", "likes", "comments", "shares")}},
    )
    by_id = {link.comment_id: link for link in links}
    for c in comments:
        link = by_id.get(c["id"])
        g.query(
            """MATCH (p:Post {url: $url})
               MERGE (c:Comment {id: $id})
               SET c.text = $text, c.likes = $likes, c.date = $date,
                   c.topic = $topic, c.sentiment = $sentiment, c.confused = $confused
               MERGE (c)-[:ON]->(p)""",
            {
                "url": post["url"], "id": c["id"], "text": c["text"], "likes": c["likes"], "date": c["date"],
                "topic": link.topic if link else None, "sentiment": link.sentiment if link else None,
                "confused": link.confused if link else False,
            },
        )
        for target in (link.characters + link.facts) if link else []:
            g.query(
                "MATCH (c:Comment {id: $cid}), (n {id: $target}) WHERE n:Character OR n:Fact MERGE (c)-[:MENTIONS]->(n)",
                {"cid": c["id"], "target": target},
            )


def load_trends(g: Graph, keyword: str, posts: list[dict]) -> None:
    for post in posts:
        g.query(
            """MERGE (p:TrendPost {url: $url})
               SET p.platform = $platform, p.caption = $caption, p.author = $author,
                   p.views = $views, p.likes = $likes, p.comments = $comments, p.shares = $shares,
                   p.keyword = $keyword, p.fetched_at = timestamp()""",
            {**{k: post[k] for k in ("url", "platform", "caption", "author", "views", "likes", "comments", "shares")}, "keyword": keyword},
        )
        for tag in post["hashtags"]:
            g.query(
                "MATCH (p:TrendPost {url: $url}) MERGE (h:Hashtag {tag: $tag}) MERGE (p)-[:TAGGED]->(h)",
                {"url": post["url"], "tag": tag},
            )


def report(g: Graph) -> dict:
    """What the audience is telling us, joined to the story graph."""
    characters = g.query(
        """MATCH (c:Comment)-[:MENTIONS]->(ch:Character)
           RETURN ch.name, count(c) AS mentions, sum(c.likes) AS likes,
                  sum(CASE WHEN c.sentiment = 'positive' THEN 1 ELSE 0 END) AS positive,
                  sum(CASE WHEN c.sentiment = 'negative' THEN 1 ELSE 0 END) AS negative
           ORDER BY likes DESC"""
    ).result_set
    # a confused viewer comment and an open finding that point at the same character or fact
    flagged = g.query(
        """MATCH (c:Comment)-[:ON]->(:Post)-[:OF]->(e:Episode) WHERE c.confused
           OPTIONAL MATCH (c)-[:MENTIONS]->(n)
           OPTIONAL MATCH (n)<-[:ABOUT]-(f:Finding {status: 'open'})
           RETURN e.num, c.text, c.likes, collect(DISTINCT n.id), collect(DISTINCT f.id) ORDER BY c.likes DESC"""
    ).result_set
    hashtags = g.query(
        """MATCH (p:TrendPost)-[:TAGGED]->(h:Hashtag)
           RETURN h.tag, count(p) AS posts, sum(p.views) AS views ORDER BY views DESC LIMIT 15"""
    ).result_set
    top_posts = g.query(
        """MATCH (p:TrendPost) RETURN p.url, p.author, p.caption, p.views, p.likes ORDER BY p.views DESC LIMIT 5"""
    ).result_set
    return {
        "characters": [dict(zip(["name", "mentions", "likes", "positive", "negative"], r)) for r in characters],
        "viewer_flags": [dict(zip(["episode", "comment", "likes", "mentions", "matching_findings"], r)) for r in flagged],
        "niche_hashtags": [dict(zip(["tag", "posts", "views"], r)) for r in hashtags],
        "niche_top_posts": [dict(zip(["url", "author", "caption", "views", "likes"], r)) for r in top_posts],
    }


# ---------- end-to-end ----------

def sync_trends(
    g: Graph, keyword: str, cache_dir: Path | None = None, limit: int = 20, max_age_hours: float = 6,
    client: BrightData | None = None,
) -> list[dict]:
    """Keyword discovery takes minutes on Bright Data, so a cache younger than `max_age_hours` is used instead."""
    cache = cache_dir / f"trends_{keyword.strip('#').lower()}.json" if cache_dir else None
    if cache and cache.exists() and time.time() - cache.stat().st_mtime < max_age_hours * 3600:
        records = json.loads(cache.read_text())
    else:
        records = (client or BrightData()).collect(
            "tiktok_posts", [{"search_keyword": keyword, "num_of_posts": limit}], discover_by="keyword", limit=limit
        )
        if cache:
            _save(cache.parent, cache.name, records)
    posts = [p for p in map(normalize_post, records) if p]
    load_trends(g, keyword, posts)
    return posts


def sync_episode(g: Graph, episode: int, url: str, cache_dir: Path | None = None, limit: int = 100, client: BrightData | None = None) -> dict:
    client = client or BrightData()
    youtube = "youtube.com" in url or "youtu.be" in url
    post_records = client.collect("youtube_videos" if youtube else "tiktok_posts", [{"url": url}])
    comment_records = client.collect("youtube_comments" if youtube else "tiktok_comments", [{"url": url, "num_of_comments": limit}])
    if cache_dir:
        _save(cache_dir, f"ep{episode}_post.json", post_records)
        _save(cache_dir, f"ep{episode}_comments.json", comment_records)
    post = next((p for p in map(normalize_post, post_records) if p), None) or {
        "url": url, "platform": "youtube" if youtube else "tiktok", "views": 0, "likes": 0, "comments": 0, "shares": 0,
    }
    comments = [c for c in map(normalize_comment, comment_records) if c]
    links = link_comments(g, comments) if comments else []
    load_episode_post(g, episode, post, comments, links)
    return {"post": post, "comments": len(comments), "linked": sum(1 for link in links if link.characters or link.facts)}


def _save(cache_dir: Path, name: str, records: list[dict]) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / name).write_text(json.dumps(records, indent=2, ensure_ascii=False) + "\n")

