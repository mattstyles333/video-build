---
name: video-build-install
description: Install video-build into the current agent (Grok, Claude Code, Codex, Hermes, Openclaw, etc.) and wire up ffmpeg + an xAI or ElevenLabs API key so the user can start editing immediately.
---

# video-build install

Use this file only for first-time install or reconnect. For daily editing, read `SKILL.md`. Always read `helpers/` — that's where the scripts live.

## What you're doing

You're setting up a conversation-driven video editor for the user. After install, the user drops raw footage into any folder, runs their agent (`grok`, `claude`, `codex`, etc.) there, and says "edit these into a launch video." You do the rest by reading `SKILL.md`.

Three things must exist on this machine:

1. The `video-build` repo cloned somewhere stable.
2. `ffmpeg` on `$PATH` (plus optional `yt-dlp` for online sources).
3. An `XAI_API_KEY` and/or `ELEVENLABS_API_KEY` in `.env` at the repo root. Either is enough. Existing ElevenLabs-only installs keep working.

And one thing must be true about the current agent:

4. It can discover `SKILL.md` — either via a global skills directory (`~/.grok/skills/`, `~/.claude/skills/`, `~/.codex/skills/`) or via a system-prompt import.

## Install prompt contract

- Do everything yourself. Only ask the user for things you cannot generate — an xAI or ElevenLabs API key if neither is already set, and confirmation before `brew install`.
- Prefer a stable clone path like `~/Developer/video-build` (not `/tmp`, not `~/Downloads`).
- The skill references helpers by bare name (`transcribe.py`, `render.py`). That works because SKILL.md and `helpers/` ship together — keep them as siblings when you register the skill.
- After install, verify by running one real command against one real file. Don't declare success on file-existence checks alone.

## Steps

### 1. Clone to a stable path

```bash
test -d ~/Developer/video-build || git clone https://github.com/mattstyles333/video-build ~/Developer/video-build
cd ~/Developer/video-build
```

If the repo is already there, `git pull --ff-only` and continue.

### 2. Install Python deps

```bash
# Prefer uv if available; fall back to pip.
command -v uv >/dev/null && uv sync || pip install -e .
```

`pyproject.toml` lists `requests`, `pillow`, `numpy`. After `uv sync`, helpers are available two ways:

- **Console commands** (preferred): `video-build-inventory`, `video-build-render`, `video-build-transcribe`, `video-build-self-eval`, etc.
- **Legacy shims** (still documented in SKILL.md): `uv run python helpers/<name>.py`

Both require deps installed (`uv sync` or `pip install -e .`). Bare system Python without the venv will fail on imports like `PIL`.

### 3. Install ffmpeg (+ optional yt-dlp)

`ffmpeg` and `ffprobe` are hard requirements. `yt-dlp` is only needed if the user wants to pull sources from URLs. Animation engines such as HyperFrames, Remotion, and Manim are installed lazily the first time a project actually needs them.

```bash
# macOS
command -v ffmpeg >/dev/null || brew install ffmpeg
command -v yt-dlp >/dev/null || brew install yt-dlp     # optional

# Debian / Ubuntu
# sudo apt-get update && sudo apt-get install -y ffmpeg
# pip install yt-dlp

# Arch
# sudo pacman -S ffmpeg yt-dlp
```

If `brew` / `apt` / `pacman` requires a sudo prompt, tell the user the exact command and wait. Do not invent a password.

### 4. Register the skill with the current agent

Figure out which agent you are running under, and register once. A symlink of the whole repo directory is the right shape — helpers/ needs to sit next to SKILL.md.

- **Grok** (`~/.grok/` present):

    ```bash
    mkdir -p ~/.grok/skills
    ln -sfn ~/Developer/video-build ~/.grok/skills/video-build
    ```

- **Claude Code** (`~/.claude/` present):

    ```bash
    mkdir -p ~/.claude/skills
    ln -sfn ~/Developer/video-build ~/.claude/skills/video-build
    ```

- **Codex** (`$CODEX_HOME` set, or `~/.codex/` present):

    ```bash
    mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
    ln -sfn ~/Developer/video-build "${CODEX_HOME:-$HOME/.codex}/skills/video-build"
    ```

- **Hermes / Openclaw / another agent with a skills directory**: symlink `~/Developer/video-build` into that agent's skills directory under the name `video-build`. If the agent has no skills directory, add a line to its system prompt / config pointing at `~/Developer/video-build/SKILL.md`.

If you can't tell which agent you're in, ask the user once: "which agent am I running under — Grok, Claude Code, Codex, or something else?" Then pick the right target.

### 5. Transcription API key (xAI or ElevenLabs)

Either key works. An existing `ELEVENLABS_API_KEY`-only `.env` is a complete install — do not ask for xAI. If both are set, Grok STT is used unless the user passes `--provider elevenlabs`.

1. Check existing state in this order and stop at the first hit:

    ```bash
    [ -n "$XAI_API_KEY" ] && echo "xai-env"
    grep -q '^XAI_API_KEY=..' ~/Developer/video-build/.env 2>/dev/null && echo "xai-dotenv"
    [ -n "$ELEVENLABS_API_KEY" ] && echo "elevenlabs-env"
    grep -q '^ELEVENLABS_API_KEY=..' ~/Developer/video-build/.env 2>/dev/null && echo "elevenlabs-dotenv"
    ```

2. If neither key is set, ask the user exactly once:

    > I need an API key for transcription (word-level timestamps, speaker diarization, filler tagging). xAI (`XAI_API_KEY`, https://console.x.ai/team/default/api-keys) or ElevenLabs (`ELEVENLABS_API_KEY`, https://elevenlabs.io/app/settings/api-keys) — either works. Paste one here and I'll write it to `~/Developer/video-build/.env`. Or if you already have it exported, say "use env" and I'll skip.

    When the user pastes a key, upsert **only that variable**. Never truncate `.env` — an existing ElevenLabs line must survive adding xAI, and vice versa:

    ```bash
    ENV=~/Developer/video-build/.env
    touch "$ENV"
    # KEY_NAME is XAI_API_KEY or ELEVENLABS_API_KEY
    if grep -q "^${KEY_NAME}=" "$ENV"; then
      tmp=$(mktemp)
      sed "s|^${KEY_NAME}=.*|${KEY_NAME}=${KEY}|" "$ENV" > "$tmp" && mv "$tmp" "$ENV"
    else
      printf '%s=%s\n' "$KEY_NAME" "$KEY" >> "$ENV"
    fi
    chmod 600 "$ENV"
    ```

    Never echo the key back in tool output. Never commit `.env`.

3. Sanity check with a cheap, quota-free call for whichever key you just wrote (or found):

    ```bash
    # xAI
    curl -s -o /dev/null -w '%{http_code}\n' \
      -H "Authorization: Bearer $(sed -n 's/^XAI_API_KEY=//p' ~/Developer/video-build/.env)" \
      https://api.x.ai/v1/models

    # ElevenLabs
    curl -s -o /dev/null -w '%{http_code}\n' \
      -H "xi-api-key: $(sed -n 's/^ELEVENLABS_API_KEY=//p' ~/Developer/video-build/.env)" \
      https://api.elevenlabs.io/v1/user
    ```

    `200` means the key works. `401` means the user pasted a wrong/expired key — ask once more and stop. Anything else (network, 5xx), move on and verify during first real transcription.

### 6. Verify end-to-end

Run one real thing. Prefer the lightest verification that still proves the pipeline is wired up:

```bash
python ~/Developer/video-build/helpers/timeline_view.py --help >/dev/null && echo "helpers OK"
ffprobe -version | head -1
```

Full transcription test is optional at install time — it burns Scribe credits. Better to wait until the user hands you their first clip.

### 7. Hand off

Tell the user, in one short message:

- Where the skill is installed (`~/Developer/video-build`).
- That they should `cd` into their footage folder and start their agent there (e.g. `grok`, `claude`).
- That a good first message is: *"edit these into a launch video"* or *"inventory this folder and write a strategy."*
- That all outputs land in `<videos_dir>/edit/` — the repo stays clean.

## Keeping the skill current

- `cd ~/Developer/video-build && git pull --ff-only` pulls the latest code. The symlink auto-picks it up on the next run.
- If `pyproject.toml` changed deps, re-run `uv sync` / `pip install -e .` after pulling.

## Cold-start reminders

- Symlink the **whole directory**, not just `SKILL.md`. The helpers need to sit next to it.
- If `.env` exists but the key is empty, treat it the same as missing — don't assume existence means validity.
- `ffmpeg` from static builds works fine. Any modern (≥ 4.x) build is enough.
- `yt-dlp` is optional. Don't block install on it; install lazily the first time a user asks to pull from a URL.
- Node.js/npm are only needed for HyperFrames or Remotion slots. HyperFrames currently requires Node.js 22+.
- HyperFrames, Remotion, and Manim are optional animation engines. Don't install or prefer one globally during setup; pick the engine per animation slot in `SKILL.md`. HyperFrames can run through `npx --yes hyperframes ...` in the slot directory. Remotion can be scaffolded with `npx create-video@latest` or installed inside the slot before rendering.
- Never run transcription as part of install verification unless the user explicitly asks — STT costs real money.
- If the user is on Linux without a package manager the agent recognizes, print the manual `ffmpeg` install URL and wait rather than guessing.
