---
name: episode-structure
description: Structure rules for 60-90 second episodes of a vertical micro-drama on TikTok and YouTube Shorts. Use when reviewing an episode script, planning the next episode, or turning audience feedback into script notes.
---

# Episode structure for short-form series

## The shape of an episode (60-90 seconds)
| Beat | Time | Job |
|---|---|---|
| Hook | 0-2 s | A face, a threat or a question. No establishing shots first. |
| Recap | 2-6 s | One line or image that re-anchors last episode's cliffhanger. |
| Turn | middle | One new piece of information that changes what a character wants. |
| Cliffhanger | last 3-5 s | An open question. Pay it off within two episodes. |

## Reviewing a script
1. Run `run_checks` for the episode. Lead with `error` findings, then `warning`, and cite scene and line for each.
2. For each finding, call `explain_finding` and show the path in one line, e.g. `Juno → REFERENCES → "Elena" ← LEARNS ← Voss (E3S3), no meeting between them`.
3. Propose the smallest fix: usually one changed line. Use `fix_script_line` only after the user approves.

## Planning the next episode
1. Call `audience_report`.
2. Use the characters viewers like most (`likes` and `positive` count) for the hook.
3. Any `viewer_flags` that match an open finding are confirmed problems. Fix them in the script, or acknowledge them on screen.
4. Pay off any cliffhanger that is two or more episodes old.
5. Borrow at most one trending hashtag or format from `niche_hashtags` / `niche_top_posts`, and only if it fits the story.
