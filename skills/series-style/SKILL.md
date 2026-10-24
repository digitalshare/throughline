---
name: series-style
description: House style and prompt rules for rendering shots of an AI-generated video series (FLUX.2 keyframes, then fal or MachGen video). Use before compiling, editing or rendering any shot prompt.
---

# Series style

Every shot prompt comes from the `compile_prompts` tool, which builds it from the series graph. Do not write prompts from scratch, because only the graph knows each character's current outfit and which props still exist.

## Rules
1. **The Characters section wins.** If the shot description and the Characters section disagree (for example the script says "black jacket" but the wardrobe timeline says "black jacket with red scarf"), keep the Characters section and say so in one line.
2. **Respect corrections.** Any `corrections` entry on a shot is a continuity fix. Tell the user about it before rendering.
3. **One visible action per shot.** If a shot description holds two actions, split it into two shots.
4. **Format:** vertical 9:16. Default to 5 seconds per shot; use 8 for establishing shots and up to 12 for a final cliffhanger shot.
5. **Never put on-screen text, logos or real people's names in a prompt.**
6. **Cost:** say how many seconds you are about to render before calling `generate_shot`. It needs the user's approval.

## Checklist before rendering
- [ ] `run_checks` shows no open `error` findings for this episode
- [ ] Every character in the shot has a canonical look and an outfit source
- [ ] No destroyed or lost prop is in the prompt
- [ ] The user has approved the render
