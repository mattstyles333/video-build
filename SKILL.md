---
name: video-build
description: Edit any video by conversation. Inventory a folder of footage and stills, transcribe speech, confirm a strategy, cut, grade, overlay graphics, burn subtitles, generate missing plates — talking heads, montages, tutorials, travel, interviews. No presets, no menus. Ask questions, confirm the plan, execute, iterate, persist. Production-correctness rules are hard; everything else is artistic freedom.
---

# Video Build

## Principle

1. **Always-loaded context is compact.** Two derived artifacts earn their keep: the packed speech transcript (`takes_packed.md`) and the asset bin (`bin.md` + thumbs). Filler tagging, retake detection, shot classification, emphasis scoring — derive those at decision time, don't precompute taxonomies.
2. **Speech cuts snap to words. Visual coverage comes from the bin.** Both are first-class. Drill into `timeline_view` at cut points — the bin is not a substitute for checking a cut. Never edit a folder you have not inventoried.
3. **Ask → confirm `strategy.md` → execute → iterate → persist.** Never touch the cut until the user has confirmed the strategy file.
4. **Generalize.** Do not assume what kind of video this is. Look at the bin, ask the user, then edit.
5. **Artistic freedom is the default.** Every specific value, preset, font, color, duration, pitch structure, and technique in this document is a *worked example* from one proven video — not a mandate. Read them to understand what's possible and why each worked. Then make your own taste calls based on what the material actually is and what the user actually wants. **The only things you MUST do are in the Hard Rules section below.** Everything else is yours.
6. **Invent freely.** If the material calls for a technique not described here — split-screen, picture-in-picture, reaction cuts, speed ramps, freeze frames, match cuts, L-cuts, J-cuts, whatever — build it. The helpers are ffmpeg and PIL. They can do anything the format supports. Do not wait for permission.
7. **Verify your own output before showing it to the user.** If you wouldn't ship it, don't present it.

## Hard Rules (production correctness — non-negotiable)

These are the things where deviation produces silent failures or broken output. They are not taste, they are correctness. Memorize them.

1. **Subtitles are applied LAST in the filter chain**, after every overlay. Otherwise overlays hide captions. Silent failure.
2. **Per-segment extract → lossless `-c copy` concat**, not single-pass filtergraph. Otherwise you double-encode every segment when overlays are added.
3. **30ms audio fades at every segment boundary** (`afade=t=in:st=0:d=0.03,afade=t=out:st={dur-0.03}:d=0.03`). Otherwise audible pops at every cut.
4. **Overlays use `setpts=PTS-STARTPTS+T/TB`** to shift the overlay's frame 0 to its window start. Otherwise you see the middle of the animation during the overlay window.
5. **Master SRT uses output-timeline offsets**: `output_time = word.start - segment_start + segment_offset`. Otherwise captions misalign after segment concat.
6. **Never cut inside a word.** Snap every cut edge to a word boundary from the word-level transcript.
7. **Pad every cut edge.** Working window: 30–200ms. ASR timestamps drift tens of milliseconds — padding absorbs the drift. Tighter for fast-paced, looser for cinematic.
8. **Word-level verbatim ASR only.** Never SRT/phrase mode (loses sub-second gap data). Never normalized fillers (loses editorial signal).
9. **Cache transcripts per source.** Never re-transcribe unless the source file itself changed.
10. **Parallel sub-agents for multiple animations.** Never sequential. Spawn N at once via the `Agent` tool; total wall time ≈ slowest one.
11. **Strategy confirmation before execution.** Never fill or render until `session.py confirm` has been run after the user approved `strategy.md`.
12. **All session outputs in `<videos_dir>/edit/`.** Never write inside the `video-build/` project directory.
13. **Inventory once.** Run `inventory.py` before transcribing or cutting. Re-run only when sources change. Preserve `look` lines.
14. **Generate gaps through `imagine.py`.** Write plates to `edit/generated/`. Seed from the bin. Do not call Imagine for exact text, numbers, or diagrams.
15. **Snapshot after the user accepts a preview.** `history.py snapshot -m "..."`. Never snapshot every draft render. Restore from `edit/history/`, then re-render. Never version `final.mp4`.

Everything else in this document is a worked example. Deviate whenever the material calls for it.

## Directory layout

The skill lives in `video-build/`. User footage lives wherever they put it. All session outputs go into `<videos_dir>/edit/`.

```
<videos_dir>/
├── <source video, images, audio — any nesting except edit/>
└── edit/
    ├── bin.json / bin.md        ← asset catalog; thumbs in bin/thumbs/
    ├── strategy.md              ← confirmed plan (beats → assets / generate)
    ├── project.md               ← memory; appended every session
    ├── takes_packed.md          ← phrase-level transcripts of speech clips
    ├── edl.json                 ← cut decisions
    ├── gaps.json                ← machine list of Imagine fills (from strategy)
    ├── history/<nnn>-<slug>/    ← snapshots of the program (not pixels)
    ├── generated/               ← imagine.py stills + clips (re-inventoried)
    ├── graphics/                ← lower-thirds, title cards
    ├── transcripts/<id>.json    ← cached raw STT JSON (id = bin id)
    ├── animations/slot_<id>/    ← per-animation source + render + reasoning
    ├── clips_graded/            ← per-segment extracts with grade + fades
    ├── master.srt               ← output-timeline subtitles
    ├── downloads/               ← yt-dlp outputs
    ├── verify/                  ← debug frames / timeline PNGs
    ├── preview.mp4
    └── final.mp4
```

## Setup

First-time install lives in `install.md` (clone, deps, ffmpeg, skill registration, API key). Don't re-run it every session; on cold start just verify:

- `XAI_API_KEY` or `ELEVENLABS_API_KEY` resolves — environment or `.env` at the video-build repo root. Either is enough for transcription. If both are set, Grok STT is used unless you pass `--provider elevenlabs`. **Imagine gap-fills require `XAI_API_KEY`.** An existing ElevenLabs-only `.env` keeps working for STT; ask for an xAI key only when a confirmed strategy has `generate` beats. If neither is set, ask the user to paste one and write it to `.env` (never to the user's `<videos_dir>`). Do not overwrite the other key if `.env` already has it.
- `ffmpeg` + `ffprobe` on PATH.
- Python deps installed (`uv sync` or `pip install -e .` inside the repo).
- Node.js + npm available if the session needs HyperFrames or Remotion slots. HyperFrames currently requires Node.js 22+.
- `yt-dlp`, HyperFrames, Remotion, Manim installed only on first use.
- First-use animation setup happens inside the slot directory, never at the video-build repo root. HyperFrames can be invoked with `npx --yes hyperframes ...`; Remotion can be scaffolded with `npx create-video@latest` or installed as a project-local dependency before using its `remotion render` command.
- This skill vendors `skills/manim-video/`. Read its SKILL.md when building a Manim slot.

Helpers (`helpers/transcribe.py`, `helpers/render.py`, etc.) live alongside this SKILL.md. Resolve their paths relative to the directory containing this file — the skill is typically symlinked at `~/.grok/skills/video-build/`, `~/.claude/skills/video-build/`, or `~/.codex/skills/video-build/`.

## Helpers

Commands work as `video-build-<tool>` after `uv sync`, or as `uv run python helpers/<tool>.py` (legacy shims). Examples below use the console form.

- **`video-build-inventory <videos_dir>`** — walk video + image + audio (skips `edit/`). Writes `bin.json`, `bin.md`, contact-sheet/still/waveform thumbs. Hash-cached. `--set-look ID="one line"` to persist a visual description. `--force` rebuilds thumbs.
- **`video-build-transcribe <video>`** — single-file STT call. Auto: Grok if `XAI_API_KEY` is set, else ElevenLabs Scribe. Force with `--provider grok|elevenlabs`. `--num-speakers N` optional (Scribe only). Cached.
- **`video-build-transcribe-batch <videos_dir>`** — parallel STT of videos that have an audio track. Recurses; skips silent B-roll and images.
- **`video-build-pack-transcripts --edit-dir <dir>`** — `transcripts/**/*.json` → `takes_packed.md` (phrase-level, break on silence ≥ 0.5s).
- **`video-build-timeline-view <video> <start> <end>`** — filmstrip + waveform PNG. On-demand visual drill-down. **`--edl <edl.json>`** renders a full-project overview (uses `preview.mp4` when present). **Not a scan tool** — use at decision points.
- **`video-build-imagine still|video|shot|edit|extend|fill`** — Grok Imagine. `shot` = still then animate. `edit` changes an existing clip. `extend` continues it from the last frame (`--duration` is **added** seconds). `--ref` / `--video` are bin ids. `fill` runs `edit/gaps.json`.
- **`video-build-tts say|voices`** — Grok TTS. Writes `edit/generated/<slug>.wav` (48 kHz) plus a word-level transcript under `transcripts/generated/`. Mix via EDL `audio_tracks`.
- **`video-build-strategy draft`** — Grok writes a **draft** `strategy.md` + `gaps.json` from bin + packed speech + `--brief`. Still wait for confirmation.
- **`video-build-history init|snapshot|list|restore`** — version the program (EDL / strategy / gaps), not the pixels. `restore N --beat CITY` splices one beat. `init <videos_dir>` writes a media-safe `.gitignore`.
- **`video-build-session status|confirm|check`** — gate. `confirm` after the user accepts `strategy.md`. `fill` and `render` refuse to run until then (`--force` overrides).
- **`video-build-graphic lower-third|title|card -o <png> --title ...`** — static transparent overlay. Use for names, title cards, stat callouts. Animated graphics still go through HyperFrames / Remotion / Manim / PIL slots.
- **`video-build-render <edl.json> -o <out>`** — per-segment extract → concat → overlays (PTS-shifted) → subtitles LAST. `--preview` for 720p fast. `--build-subtitles` to generate master.srt inline. `--strict` fails on word-boundary warnings. Stills in `sources` become held (or Ken Burns) clips.
- **`video-build-grade <in> -o <out>`** — ffmpeg filter chain grade. Presets + `--filter '<raw>'` for custom.
- **`video-build-validate-edl <edl.json>`** — validate EDL structure + file refs before render.
- **`video-build-self-eval --edl <edl.json> [--video preview.mp4]`** — PNGs at every cut boundary + duration check. Run after preview render, before showing the user.

For animations, create `<edit>/animations/slot_<id>/` with `Bash` and spawn a sub-agent via the `Agent` tool.

## The process

1. **Inventory.** `history.py init <videos_dir>` once (safe `.gitignore`; add `--git` if they want a repo). `inventory.py <videos_dir>`. Read `bin.md`. **Look at every thumb once** and persist a one-line look: `inventory.py <videos_dir> --set-look ID="talking head, warm interior"`. Do not skip images or silent B-roll — they are coverage. If `edit/history/` exists, `history.py list` and mention the latest snapshot in one line.
2. **Transcribe speech.** `transcribe_batch.py` (skips silent files). `pack_transcripts.py` → `takes_packed.md`. Sample `timeline_view` only where the bin look is ambiguous.
3. **Pre-scan for problems.** One pass over `takes_packed.md` to note verbal slips, obvious mis-speaks, or phrasings to avoid. Plain list, feed into the editor brief.
4. **Converse.** Describe the bin and the speech in plain English. Ask questions *shaped by the material*. Collect: content type, target length/aspect, aesthetic/brand direction, pacing feel, must-preserve moments, must-cut moments, animation and grade preferences, subtitle needs. Do not use a fixed checklist — the right questions are different every time.
5. **Draft + confirm strategy.** After you have the brief, `strategy.py draft --edit-dir <edit> --brief "..."`. Read it, revise with the user. When they accept it, `session.py confirm --edit-dir <edit>`. Do not treat the draft as approved. `session.py status` on startup.
6. **Fill gaps.** Confirmed `gaps.json` → `imagine.py fill --edit-dir <edit> --videos-dir <folder>`. Confirmed Voiceover lines → `tts.py say --edit-dir <edit> --slug vo --text "..." --voice eve`. Then re-pack transcripts if VO was added.
7. **Execute.** Produce `edl.json` from the confirmed strategy. Drill into `timeline_view` at ambiguous moments. Build `graphic.py` overlays and animation slots. Compose via `render.py`.
8. **Preview.** `video-build-render edl.json -o preview.mp4 --preview`.
9. **Self-eval (before showing the user).** `video-build-self-eval --edl edit/edl.json --video edit/preview.mp4` — or manually run `video-build-timeline-view` on the **rendered output** at every cut boundary (±1.5s window). Check each image for:
   - Visual discontinuity / flash / jump at the cut
   - Waveform spike at the boundary (audio pop that slipped past the 30ms fade)
   - Subtitle hidden behind an overlay (Rule 1 violation)
   - Overlay misaligned or showing wrong frames (Rule 4 violation)

   Also sample: first 2s, last 2s, and 2–3 mid-points — check grade consistency, subtitle readability, overall coherence. Run `ffprobe` on the output to verify duration matches the EDL expectation.

   If anything fails: fix → re-render → re-eval. **Cap at 3 self-eval passes** — if issues remain after 3, flag them to the user rather than looping forever. Only present the preview once the self-eval passes.
10. **Iterate + persist.** When the user accepts a preview (or asks to keep this version), `history.py snapshot --edit-dir <edit> -m "<what changed>"` before the next change. Natural-language feedback, re-plan, re-render. “Undo the city plate” → `history.py restore <n> --beat CITY` then `render.py --preview`. Never re-transcribe. Never rebuild the bin unless a source file changed. Final render on confirmation. Append to `project.md`.

## Cut craft (techniques)

- **Speech cuts from words.** Candidate A-roll cuts from word boundaries and silence gaps.
- **Picture from the bin.** Insert B-roll, stills, and generated plates on the beat they illustrate — don't leave a talking head on screen because that's all the transcript contains.
- **Preserve peaks.** Laughs, punchlines, emphasis beats. Extend past punchlines to include reactions — the laugh IS the beat.
- **Speaker handoffs** benefit from air between utterances. Common values: 400–600ms. Less for fast-paced, more for cinematic. Taste call.
- **Audio events as signals.** `(laughs)`, `(sighs)`, `(applause)` mark beats. Extend past them.
- **Silence gaps are cut candidates.** Silences ≥400ms are usually the cleanest. 150–400ms phrase boundaries are usable with a visual check. <150ms is unsafe (mid-phrase).
- **Example cut padding** (the launch video shipped with this): 50ms before the first kept word, 80ms after the last. Tighter for montage energy, looser for documentary. Stay in the 30–200ms working window (Hard Rule 7).
- **Never reason audio and video independently.** Every cut must work on both tracks.

## The packed transcript (speech reading view)

`pack_transcripts.py` reads all `transcripts/**/*.json` and produces one markdown file where each take is a list of phrase-level lines, each prefixed with its `[start-end]` time range. Phrases break on any silence ≥ 0.5s OR speaker change. Read it next to `bin.md` — speech timing from here, coverage from the bin.

Example line:
```
## C0103  (duration: 43.0s, 8 phrases)
  [002.52-005.36] S0 Ninety percent of what a web agent does is completely wasted.
  [006.08-006.74] S0 We fixed this.
```

## Editor sub-agent brief (for multi-take selection)

When the task is "pick the best take of each beat across many clips," spawn a dedicated sub-agent with a brief shaped like this. The structure is load-bearing; the pitch-shape example is not.

```
You are editing a <type> video. Pick the best take of each beat and 
assemble them chronologically by beat, not by source clip order.

INPUTS:
  - strategy.md (confirmed plan — honor its beat list and generate marks)
  - bin.md + bin/thumbs/ (every source: speech, B-roll, stills, audio, generated)
  - takes_packed.md (time-annotated phrase-level transcripts of speech clips)
  - Product/narrative context: <2 sentences from the user>
  - Speaker(s): <name, role, delivery style note>
  - Expected structure: <from strategy.md>
  - Verbal slips to avoid: <list from the pre-scan pass>
  - Target runtime: <seconds>

Common structural archetypes (pick, adapt, or invent):
  - Tech launch / demo:   HOOK → PROBLEM → SOLUTION → BENEFIT → EXAMPLE → CTA
  - Tutorial:             INTRO → SETUP → STEPS → GOTCHAS → RECAP
  - Interview:            (QUESTION → ANSWER → FOLLOWUP) repeat
  - Travel / event:       ARRIVAL → HIGHLIGHTS → QUIET MOMENTS → DEPARTURE
  - Documentary:          THESIS → EVIDENCE → COUNTERPOINT → CONCLUSION
  - Music / performance:  INTRO → VERSE → CHORUS → BRIDGE → OUTRO
  - Or invent your own.

RULES:
  - Start/end times must fall on word boundaries from the transcript.
  - Pad cut boundaries (working window 30–200ms).
  - Prefer silences ≥ 400ms as cut targets.
  - Unavoidable slips are kept if no better take exists. Note them in "reason".
  - If over budget, revise: drop a beat or trim tails. Report total and self-correct.

OUTPUT (JSON array, no prose):
  [{"source": "C0103", "start": 2.42, "end": 6.85, "beat": "HOOK",
    "quote": "...", "reason": "..."}, ...]

Return the final EDL and a one-line total runtime check.
```

## Color grade (when requested)

Your job is to **reason about the image**, not apply a preset. Look at a frame (via `timeline_view`), decide what's wrong, adjust one thing, look again.

Mental model is ASC CDL. Per channel: `out = (in * slope + offset) ** power`, then global saturation. `slope` → highlights, `offset` → shadows, `power` → midtones.

**Example filter chains** (`grade.py` has `--list-presets`; use them as starting points or mix your own):

- **`warm_cinematic`** — retro/technical, subtle teal/orange split, desaturated. Shipped in a real launch video. Safe for talking heads.
- **`neutral_punch`** — minimal corrective: contrast bump + gentle S-curve. No hue shifts.
- **`none`** — straight copy. Default when the user hasn't asked.

For anything else — portraiture, nature, product, music video, documentary — invent your own chain. `grade.py --filter '<raw ffmpeg>'` accepts any filter string.

Hard rules: apply **per-segment during extraction** (not post-concat, which re-encodes twice). Never go aggressive without testing skin tones.

## Subtitles (when requested)

Subtitles have three dimensions worth reasoning about: **chunking** (1/2/3/sentence per line), **case** (UPPER/Title/Natural), and **placement** (margin from bottom). The right combo depends on content.

**Worked styles** — pick, adapt, or invent:

**`bold-overlay`** (default) — short-form social. 2-word chunks, UPPERCASE, Helvetica 18 Bold, `MarginV=90` (vertical safe zone). Set in the EDL:

```json
"subtitle_style": {"chunk_words": 2, "case": "upper", "margin_v": 90, "size": 18}
```

**`natural-sentence`** — narrative / documentary. 4–7 word chunks, sentence case, larger `margin_v` if you want captions higher.

```json
"subtitle_style": {"chunk_words": 6, "case": "natural", "margin_v": 70, "size": 20}
```

Or pass a raw ASS `force_style` string as `"subtitle_style": "FontName=..."`. Hard rules: subtitles LAST (Rule 1), output-timeline offsets (Rule 5).

## Animations (when requested)

Animations match the content and the brand. **Get the palette, font, and visual language from the conversation** — never assume a default. If the user hasn't told you, propose a palette in the strategy phase and wait for confirmation before building anything.

**Tool options:**

Pick the engine per animation slot. Do not default to Remotion just because the animation is web-adjacent.

- **HyperFrames** — Browser-native HTML/CSS/GSAP video compositions: product UI motion, website-to-video or mockup-to-video captures, kinetic typography, landing-page/storyboard promos, data-driven UI states, transparent WebM overlays, and clips that need deterministic frame capture plus HyperFrames lint/validate/render checks. Best when the animation should be authored and verified like a web composition instead of a React component tree.
- **Remotion** — React/CSS compositions with component state, reusable React primitives, or an existing Remotion brand system. Best when the user specifically asks for React/Remotion or when React composition is the simpler authoring model.
- **Manim** — formal diagrams, state machines, equation derivations, graph morphs. Read `skills/manim-video/SKILL.md` and its references for depth.
- **`graphic.py`** — static lower-third / title / stat card as a transparent PNG. First choice when the graphic does not need to move.
- **PIL + PNG sequence + ffmpeg** — simple overlay cards: counters, typewriter text, single bar reveals, progressive draws. Fast to iterate, any aesthetic you want. The launch video used this.

For HyperFrames slots, scaffold the slot inside `edit/animations/slot_<id>/` with `npx --yes hyperframes init . --example blank --non-interactive --skip-skills`, build the HTML composition there, run the HyperFrames checks that fit the slot (`lint`, `validate`, and a draft render when practical), then produce the final overlay video with `npx --yes hyperframes render . -o render.mp4` or `--format webm -o render.webm` when alpha is required. Point the EDL overlay `file` at the actual rendered path.

For Remotion slots, keep the Remotion project isolated inside the same slot directory, scaffold with `npx create-video@latest` or install Remotion locally there, render the composition to `render.mp4` with the project-local `remotion render` command, and verify duration and dimensions with `ffprobe`.

None is mandatory. Invent hybrids if useful (e.g., PIL background with a HyperFrames or Remotion layer on top).

**Duration rules of thumb, context-dependent:**

- **Sync-to-narration explanations.** A viewer needs to parse the content at 1×. Rough floor 3s, typical 5–7s for simple cards, 8–14s for complex diagrams. The launch video shipped at 5–7s per simple card.
- **Beat-synced accents** (music video, fast montage). 0.5–2s is fine — they're visual accents, not information. The "readable at 1×" rule becomes *"recognizable at 1×"*, not *"fully parseable."*
- **Hold the final frame ≥ 1s** before the cut (universal).
- **Over voiceover:** total duration ≥ `narration_length + 1s` (universal).
- **Never parallel-reveal independent elements** — the eye can't track two new things at once. One thing, pause, next thing.

**Animation payoff timing (rule for sync-to-narration):** get the payoff word's timestamp. Start the overlay `reveal_duration` seconds earlier so the landing frame coincides with the spoken payoff word. Without this sync the animation feels disconnected.

**Easing** (universal — never `linear`, it looks robotic):

```python
def ease_out_cubic(t):    return 1 - (1 - t) ** 3
def ease_in_out_cubic(t):
    if t < 0.5: return 4 * t ** 3
    return 1 - (-2 * t + 2) ** 3 / 2
```

`ease_out_cubic` for single reveals (slow landing). `ease_in_out_cubic` for continuous draws.

**Typing text anchor trick:** center on the FULL string's width, not the partial-string width — otherwise text slides left during reveal.

**Example palette** (the launch video — one aesthetic among infinite):
- Background `(10, 10, 10)` near-black
- Accent `#FF5A00` / `(255, 90, 0)` orange
- Labels `(110, 110, 110)` dim gray
- Font: Menlo Bold at `/System/Library/Fonts/Menlo.ttc` (index 1)
- ≤ 2 accent colors, ~40% empty space, minimal chrome
- Result: terminal / retro tech feel

This is one style. If the brand is warm and serif, use that. If it's colorful and playful, use that. If the user handed you a style guide, follow it. If they didn't, propose one and confirm.

**Parallel sub-agent brief** — each animation is one sub-agent spawned via the `Agent` tool. Each prompt is self-contained (sub-agents have no parent context). Include:

1. One-sentence goal: *"Build ONE animation: [spec]. Nothing else."*
2. Absolute output path (`<edit>/animations/slot_<id>/render.mp4`)
3. Exact technical spec: resolution, fps, codec, pix_fmt, CRF, duration
4. Style palette as concrete values (RGB tuples, hex, or reference to a design system)
5. Font path with index
6. Frame-by-frame timeline (what happens when, with easing)
7. Anti-list ("no chrome, no extras, no titles unless specified")
8. Code pattern reference (copy helpers inline, don't import across slots)
9. Deliverable checklist (script, render, verify duration via ffprobe, report)
10. **"Do not ask questions. If anything is ambiguous, pick the most obvious interpretation and proceed."**

One sub-agent = one file (unique filenames, parallel agents don't overwrite each other).

## Strategy — `edit/strategy.md`

Write this file, then stop. Do not build an EDL until the user confirms it.

```markdown
# Strategy

**Target:** 45s · 1080×1920 · social
**Grade:** auto
**Subtitles:** bold-overlay (2-word, UPPER, MarginV=90)
**Voice:** none
**Palette:** bg #0A0A0A · accent #FF5A00 · type Helvetica

## Beats

| beat | spoken | visual | asset | notes |
|------|--------|--------|-------|-------|
| HOOK | "We fixed this." | a-roll | C0103 | 6.08–6.74, pad to words |
| PRODUCT | (continues) | still | stills/hero | kenburns 4s |
| CITY | — | generate | generated/street | 6s plate, seed stills/alley |
| CTA | "Try it tonight." | a-roll + lower-third | C0108 | graphic.py name card |

## Generate

- `generated/street`: 6s night street, match look of `stills/alley` (warm sodium, wet asphalt).
```

Also write the machine form next to it — `edit/gaps.json`:

```json
{
  "gaps": [
    {
      "slug": "street",
      "kind": "shot",
      "prompt": "Night street, wet asphalt, warm sodium lights. Slow camera push-in, single subject.",
      "refs": ["stills/alley"],
      "duration": 6,
      "aspect_ratio": "9:16",
      "voices": ["eve"]
    }
  ]
}
```

`visual` is one of: `a-roll` | `b-roll` | `still` | `generate` | `graphic`. `asset` is a bin id (or a path under `edit/generated/` / `edit/graphics/` you will create). `kind` is `shot` (default), `still`, `video`, `edit` (change a clip), or `extend` (lengthen a clip). Edit/extend set `video` to the bin id.

## Generate gaps

Only for beats marked `generate`. Do not invent footage when the bin already has coverage. Needs `XAI_API_KEY` (same key as Grok STT).

1. Closest bin id goes in `refs` (subject, grade, or palette). The client injects that asset's `look` into the prompt and sends the file as an edit/reference image. Video refs become a mid-frame still first.
2. `imagine.py fill --edit-dir <edit> --videos-dir <folder>` — or one `shot` / `still` / `video` call per gap.
3. Default path is **shot**: generate/edit a still, then image-to-video (6s, 720p). Prefer more short shots over one long take.
   - Clip is right but wrong in one way → `kind: edit` + `video: <bin id>`.
   - Clip is right but too short → `kind: extend` + `video: <bin id>` + `duration` = seconds to **add**.
4. Outputs: `edit/generated/<slug>.png`, `.mp4`, `.json`. `fill` re-runs inventory. Point the EDL `sources` entry at the generated path.
5. Recurring subject: one canonical still (`kind: still`), then later shots use `--image generated/<slug>` or `refs: ["generated/<slug>"]` — never a fresh text-only still for the same person/object.

One-off:

```bash
python helpers/imagine.py shot \
  --edit-dir <edit> --slug street \
  --prompt "Night street, wet asphalt, warm sodium. Slow push-in." \
  --ref stills/alley --duration 6 --aspect 9:16
```

If Imagine is blocked (no key, moderation), say so and use bin coverage or a `graphic.py` card. Do not stall the edit. Do not retry a moderation block with a paraphrased prompt.

Exact text, numbers, UI chrome, diagrams: build with `graphic.py`, HyperFrames, Remotion, or Manim — not Imagine.

Speaking Imagine shots: `voices: ["eve"]` (or `--voice eve`). That is on-screen speech baked into the clip. Off-screen VO is `tts.py`, then an `audio_tracks` entry. Do not use both for the same line.

## Voice — `tts.py`

Use when the strategy has a Voiceover script (no usable A-roll, or the brief asked for narration).

```bash
python helpers/tts.py voices
python helpers/tts.py say --edit-dir <edit> --slug vo \
  --text "We fixed this." --voice eve --inventory
```

Writes `edit/generated/vo.wav` and `transcripts/generated/vo.json` (word times from TTS). Point EDL `audio_tracks` at the wav. Re-run `pack_transcripts.py` so captions can include the VO.

Speech tags are allowed in the text (`[pause]`, `<whisper>…</whisper>`). Default voice is `eve`. Custom console voice IDs work as `--voice`.

Do not rebuild transcription. Word-level Grok STT is already `transcribe.py` / `transcribe_batch.py`. TTS timestamps cover generated VO.

## Output spec

Match the source unless the user asked for something specific. Common targets: `1920×1080@24` cinematic, `1920×1080@30` screen content, `1080×1920@30` vertical social, `3840×2160@24` 4K cinema, `1080×1080@30` square. `render.py` defaults the scale to 1080p from any source; pass `--filter` or edit the extract command for other targets. Worth asking the user which delivery format matters.

## EDL format

```json
{
  "version": 1,
  "sources": {
    "C0103": "/abs/path/C0103.MP4",
    "stills/hero": "/abs/path/stills/hero.png",
    "generated/street": "/abs/path/edit/generated/street.mp4"
  },
  "ranges": [
    {"source": "C0103", "start": 2.42, "end": 6.85,
     "beat": "HOOK", "quote": "...", "reason": "Cleanest delivery."},
    {"source": "C0103", "start": 6.08, "end": 10.08,
     "picture": {"source": "stills/hero", "start": 0, "kenburns": true},
     "beat": "PRODUCT"},
    {"source": "stills/hero", "start": 0, "end": 4.0,
     "kenburns": true, "transition_out": "fade", "transition_duration": 0.4,
     "beat": "HOLD"},
    {"source": "C0103", "start": 14.30, "end": 20.90,
     "beat": "CTA"}
  ],
  "grade": "auto",
  "overlays": [
    {"file": "edit/graphics/lt.png", "start_in_output": 14.3, "duration": 4.0,
     "beat": "CTA", "fade_in": 0.25, "fade_out": 0.25},
    {"file": "edit/animations/slot_1/render.mp4", "start_in_output": 0.0, "duration": 5.0,
     "beat": "HOOK", "x": 80, "y": 80, "w": 400, "opacity": 0.95}
  ],
  "subtitles": "edit/master.srt",
  "audio_tracks": [
    {"file": "edit/generated/vo.wav", "start_in_output": 0.0, "volume": 1.0},
    {"file": "music.wav", "volume": 0.22, "loop": true, "duck": true, "duck_db": 8}
  ],
  "subtitle_style": {"chunk_words": 2, "case": "upper", "margin_v": 90},
  "total_duration_s": 15.03
}
```

- `sources` values are videos **or stills**. A still range is a held clip; `kenburns: true` adds a slow push-in. `start` on a still is ignored; duration is `end - start`.
- `picture` on a range keeps **sound** from `source`/`start`/`end` and puts another bin asset on **screen**. That is B-roll under VO. `"picture": "stills/hero"` or `{"source": "broll/street", "start": 3.0, "kenburns": true}`. If the picture clip is shorter than the VO, the last frame holds.
- `transition_out: "fade"` is fade-to-black on this clip and fade-in on the next (`transition_duration` default 0.4s). Or set `fade_in` / `fade_out` on a range directly.
- Overlay fields: `file`, `start_in_output`, `duration`, optional `beat`, `x`, `y`, `w`, `h`, `opacity`, `fade_in`, `fade_out`. Set `beat` so `history.py restore --beat` can target the overlay. Images are looped for the window. Full-frame `graphic.py` PNGs sit at `x=0,y=0`.
- `grade` is a preset name, raw ffmpeg filter, or `auto`.
- `subtitle_style` is a map (`chunk_words`, `case` = upper|title|natural, `font`, `size`, `margin_v`, …) or a raw ASS `force_style` string. `subtitles` is optional and applied LAST.
- `audio_tracks` mix extra beds onto the concat. `start_in_output` is delay; `volume` defaults to 1.0. Loudnorm still runs after.
  - VO / SFX: `{"file": "edit/generated/vo.wav", "start_in_output": 0, "volume": 1.0}`
  - Music: `{"file": "music.wav", "volume": 0.22, "loop": true, "duck": true, "duck_db": 8}` — loops to the cut, drops ~8 dB while A-roll (or another VO track) is talking, then comes back up. Still-only ranges do not duck. Set `"duck": false` on a talking range to keep music up (e.g. under cheers).

## Memory — `project.md`

Append one section per session at `<edit>/project.md`:

```markdown
## Session N — YYYY-MM-DD

**Strategy:** one paragraph describing the approach
**Decisions:** take choices, cuts, grades, animations + why
**Reasoning log:** one-line rationale for non-obvious decisions
**Outstanding:** deferred items
```

On startup, read `project.md` if it exists and summarize the last session in one sentence before asking whether to continue. If `edit/history/` exists, also name the latest snapshot.

## History — `edit/history/`

The rebuildable program is `strategy.md` + `gaps.json` + `edl.json`. `preview.mp4` / `final.mp4` / `clips_graded/` / Imagine binaries are build output.

```bash
python helpers/history.py init <videos_dir>          # media-safe .gitignore
python helpers/history.py init <videos_dir> --git    # plus git init
python helpers/history.py snapshot --edit-dir <edit> -m "tighter hook"
python helpers/history.py list --edit-dir <edit>
python helpers/history.py restore 3 --edit-dir <edit>
python helpers/history.py restore 3 --beat CITY --edit-dir <edit>
```

Full restore overwrites those three files from the snapshot, then you re-render. Beat restore splices that beat's ranges and overlapping overlays, and shifts later overlays if the beat got longer or shorter. Overlay `beat` fields make this precise — set them when you write the EDL.

Do not `git add` source video, generated plates, or finals. `init` writes a `.gitignore` that keeps the program and ignores the pixels. Optional `--git` on snapshot commits text if the footage folder is already a repo.

## Anti-patterns

Things that consistently fail regardless of style:

- **Hierarchical pre-computed USABILITY / tone / shot-layer taxonomies.** Over-engineering. A compact bin (probe + thumb + one-line look) is the visual equivalent of `takes_packed.md` — build that, not a classifier.
- **Skipping the bin because the folder is "just talking heads."** Still run `inventory.py`. You will miss stills, B-roll, and music sitting next to the takes.
- **Generating footage that already exists in the bin.** Look first.
- **Hand-tuned moment-scoring functions.** The LLM picks better than any heuristic you'll write.
- **Whisper SRT / phrase-level output.** Loses sub-second gap data. Always word-level verbatim.
- **Running Whisper locally on CPU.** Slow and it normalizes fillers. Use hosted Grok STT (or ElevenLabs Scribe).
- **Burning subtitles into base before compositing overlays.** Overlays hide them. (Hard Rule 1.)
- **Single-pass filtergraph when you have overlays.** Double re-encodes. Use per-segment extract → concat.
- **Linear animation easing.** Looks robotic. Always cubic.
- **Hard audio cuts at segment boundaries.** Audible pops. (Hard Rule 3.)
- **Typing text centered on the partial string.** Text slides left as it grows.
- **Sequential sub-agents for multiple animations.** Always parallel.
- **Editing before confirming `strategy.md`.** Never.
- **Re-transcribing cached sources.** Immutable outputs of immutable inputs.
- **Assuming what kind of video it is.** Look first, ask second, edit last.
