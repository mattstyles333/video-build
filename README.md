<p align="center">
  <img src="static/video-build-banner.png" alt="video-build" width="100%">
</p>

# video-build

Edit videos with Grok (or any coding agent). Drop footage in a folder, confirm a strategy, get `final.mp4`. Talking heads, montages, tutorials, travel, interviews — no presets, no menus.

A fork of [video-use](https://github.com/browser-use/video-use) with a first-class bin, Imagine/TTS, and a rebuildable edit program.

## What it does

- **Indexes the whole bin once** — video, stills, and audio — into `bin.md` plus contact-sheet thumbs
- **Cuts out filler words** (`umm`, `uh`, false starts) and dead space between takes
- **Confirms a strategy file** (`strategy.md`) that maps beats to bin assets or generated plates
- **Fills coverage gaps** with Grok Imagine (`imagine.py`), seeded from bin refs and looks
- **Drafts a strategy** with Grok (`strategy.py draft`) from the bin + packed speech — still confirmed before the cut
- **Speaks** via Grok TTS (`tts.py`) as a mixable voiceover bed, or Imagine `--voice` for on-screen speech
- **Music beds** loop to the cut and duck under speech (`audio_tracks` + `loop`/`duck`)
- **Auto color grades** every segment (warm cinematic, neutral punch, or any custom ffmpeg chain)
- **30ms audio fades** at every cut so you never hear a pop
- **Burns subtitles** in your style — 2-word UPPERCASE chunks by default, set per-EDL
- **Composites stills, Ken Burns, positioned overlays, and fade-to-black** from the EDL
- **Generates animation overlays** via [HyperFrames](https://github.com/heygen-com/hyperframes), [Remotion](https://www.remotion.dev/), [Manim](https://www.manim.community/), or PIL — spawned in parallel sub-agents, one per animation
- **Self-evaluates the rendered output** at every cut boundary before showing you anything
- **Persists session memory** in `project.md` so next week's session picks up where you left off
- **Snapshots the program** (`edl.json` / strategy / gaps) so you can restore a cut or one beat and re-render — pixels stay out of git

## Setup prompt

Paste into Grok, Claude Code, Codex, Hermes, Openclaw, or any agent with shell access:

```text
Set up video-build from https://github.com/mattstyles333/video-build for me.

Read install.md first to install this repo, wire up ffmpeg, register the skill as video-build with whichever agent you're running under, and set up an xAI or ElevenLabs API key — ask me to paste it when you need it. Then read SKILL.md for daily usage, and always read helpers/ because that's where the editing scripts live. After install, don't transcribe anything on your own — just tell me it's ready and wait for me to drop footage into a folder.
```

The agent handles the clone, dependencies, skill registration, and prompts you once for a key — [xAI](https://console.x.ai/team/default/api-keys) or [ElevenLabs](https://elevenlabs.io/app/settings/api-keys). Either works. Existing `ELEVENLABS_API_KEY` setups keep working.

Then point your agent at a folder of raw takes:

```bash
cd /path/to/your/videos
grok      # or claude, codex, hermes, etc.
```

And in the session:

> edit these into a launch video

It inventories the sources, proposes a strategy, waits for your OK, then produces `edit/final.mp4` next to your sources. All outputs live in `<videos_dir>/edit/` — the skill directory stays clean.

## Manual install

If you'd rather do it by hand:

```bash
# 1. Clone and symlink into your agent's skills directory
git clone https://github.com/mattstyles333/video-build ~/Developer/video-build
ln -sfn ~/Developer/video-build ~/.grok/skills/video-build          # Grok
# ln -sfn ~/Developer/video-build ~/.claude/skills/video-build      # Claude Code
# ln -sfn ~/Developer/video-build ~/.codex/skills/video-build       # Codex

# 2. Install deps
cd ~/Developer/video-build
uv sync                         # or: pip install -e .
brew install ffmpeg             # required
brew install yt-dlp             # optional, for downloading online sources

# 3. Add an xAI and/or ElevenLabs API key (either is enough)
cp .env.example .env
$EDITOR .env                    # XAI_API_KEY=... and/or ELEVENLABS_API_KEY=...
```

## How it works

The LLM does not dump every frame. It **reads** the cut — through three compact layers.

<p align="center">
  <img src="static/timeline-view.svg" alt="timeline_view composite — filmstrip + speaker track + waveform + word labels + silence-gap cut candidates" width="100%">
</p>

**Layer 1 — Asset bin (always loaded).** One pass over the working directory catalogs every video, still, and audio file into `bin.md` plus thumbs. Looks are written once. This is how silent B-roll, product stills, and generated plates stay visible.

**Layer 2 — Audio transcript (speech clips).** One Grok STT or ElevenLabs Scribe call per clip with audio gives word-level timestamps, speaker diarization, and filler-word retention. If both keys are set, Grok is used unless you pass `--provider elevenlabs`. Speech packs into `takes_packed.md`.

```
## C0103  (duration: 43.0s, 8 phrases)
  [002.52-005.36] S0 Ninety percent of what a web agent does is completely wasted.
  [006.08-006.74] S0 We fixed this.
```

**Layer 3 — Visual composite (on demand).** `timeline_view` produces a filmstrip + waveform + word labels PNG for any time range. Called at decision points — ambiguous pauses, retake comparisons, cut-point sanity checks.

> Naive approach: 30,000 frames × 1,500 tokens = **45M tokens of noise**.
> video-build: **bin.md + 12KB of speech + a handful of PNGs**.

Same idea as browser-use giving an LLM a structured DOM instead of a screenshot — but for video.

## Pipeline

```
Inventory ──> Transcribe ──> Strategy ──> Generate gaps ──> EDL ──> Render ──> Self-Eval
                                                                                  │
                                                                                  └─ issue? fix + re-render (max 3)
```

The self-eval loop runs `timeline_view` on the _rendered output_ at every cut boundary — catches visual jumps, audio pops, hidden subtitles. You see the preview only after it passes.

## Design principles

1. **Compact always-on context.** No frame-dumping. The bin and the speech transcript are the surface.
2. **Speech cuts snap to words. Coverage comes from the bin.** Both are first-class.
3. **Ask → confirm → execute → self-eval → persist.** Never touch the cut without strategy approval.
4. **Zero assumptions about content type.** Look, ask, then edit.
5. **Hard rules, artistic freedom elsewhere.** Production-correctness is non-negotiable. Taste isn't.

See [`SKILL.md`](./SKILL.md) for the full production rules and editing craft.
