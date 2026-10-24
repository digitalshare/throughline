# Ideas

## S1: Throughline (building this one)

A graph-powered series bible for AI-generated video series (TikTok micro-dramas, YouTube Shorts episodes, AI anime).

**Problem.** Each AI video generation starts from scratch. Faces, hair and outfits drift between shots and episodes. The story breaks across episodes: a character knows a secret they never learned, a destroyed prop comes back, a cliffhanger is never paid off. Creators find these after paying for renders, or when commenters point them out.

**What it does**
- Checks a new episode script against every earlier episode before rendering. Each issue comes with the graph path that proves it.
- Compiles each shot's video prompt from the graph, and explains every detail ("red scarf, because she took it in E2S1").
- Checks generated frames for drift against the character bible (OpenAI vision).
- Pulls TikTok/YouTube views, comments and trends back into the graph (Bright Data) and plans the next episode from them.
- Asks for approval before spending generation credits and before publishing.

**Tracks**
- FalkorDB: the series bible. Multi-hop checks across episodes, one graph shared by all the agents, memory across sessions.
- Bright Data: TikTok/YouTube scrapers for posts, comments, trends.
- TrueForge + OpenAI: a showrunner agent with continuity, prompt, QA and audience subagents. Sandbox for ffmpeg frames, skills for house style, approvals for spending and publishing.
- Qodo: small PRs throughout.

**Demo line.** "Check episode 4 before we render" → six findings, each with its path → approve a fix → compile prompts → "what did viewers say about episode 3?"

## P1: BOM Scout (backup)

A parts-sourcing agent for robotics builds and competitions.

- **Input:** a BOM, a URDF, or "6-DOF arm, 2kg payload".
- **FalkorDB:** a compatibility graph. Part → spec (voltage, current, protocol, connector, mount) → compatible-with → ROS driver → parts that can substitute. Multi-hop: "XL430 out of stock → XC430 uses the same protocol, bracket and U2D2 adapter → in stock at RobotShop."
- **Bright Data:** live price, stock and lead time from Digi-Key, Mouser, Amazon, AliExpress, RobotShop (sites that block plain scripts).
- **TrueForge:** sandbox runs torque/power budget maths. Approval before adding to cart or ordering. Remembers what the team already owns.
- **Demo line.** "It's 2pm at the hackathon and the servo just burned out. What can I get delivered to the venue today?"
- **Variant (Pit Crew):** mechanical, electrical and budget agents share one graph for FIRST/RoboCup/VEX teams and resolve conflicts like "fits the torque budget but breaks the cost cap".
