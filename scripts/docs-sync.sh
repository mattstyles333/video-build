#!/usr/bin/env bash
# Async docs sync for video-build → video-build-docs.
#
# Invoked by scripts/git-hooks/pre-push (detached, non-blocking). Can also be
# run manually:
#
#   scripts/docs-sync.sh                          # sync current working tree now
#   DOCSYNC_SKIP_LLM=1 scripts/docs-sync.sh       # skip the opencode polish pass
#
# Flow:
#   1. If given a pushed sha + ref, wait until that push actually lands on a
#      remote (push may fail — timeout and bail instead of syncing ahead).
#   2. Copy README.md / SKILL.md / install.md / poster.html / static/ into the
#      docs repo, rewriting links for the published site.
#   3. Optionally run opencode headless in the docs repo to polish the site.
#   4. Commit + push the docs repo. Any failure is logged, never fatal.
#
# Concurrency-guarded with flock: overlapping jobs exit immediately.
# The rsync content is the current working tree; if you push twice quickly,
# the first job may sync slightly-newer content. Benign for docs.

set -u

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOCS_REPO_DIR="${DOCS_REPO_DIR:-$HOME/Developer/video-build-docs}"
CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/video-build"
LOG_FILE="$CACHE_DIR/docs-sync.log"
LOCK_FILE="$CACHE_DIR/docs-sync.lock"
LOCAL_SHA="${1:-}"
REMOTE_REF="${2:-}"

log() { echo "[$(date '+%F %T')] $*"; }

mkdir -p "$CACHE_DIR"
exec >>"$LOG_FILE" 2>&1

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  log "docs sync already running — skipping"
  exit 0
fi

cd "$REPO_DIR" || exit 1

# -------- 1. wait for the push to land (if triggered from pre-push) ----------
if [ -n "$LOCAL_SHA" ] && [ -n "$REMOTE_REF" ]; then
  log "waiting for push of $LOCAL_SHA ($REMOTE_REF) to land…"
  landed=0
  for _ in $(seq 1 45); do
    for remote in $(git remote 2>/dev/null); do
      remote_sha="$(git ls-remote "$remote" "$REMOTE_REF" 2>/dev/null | cut -f1)"
      if [ -n "$remote_sha" ] && [ "$remote_sha" = "$LOCAL_SHA" ]; then
        landed=1
        break 2
      fi
    done
    sleep 2
  done
  if [ "$landed" != 1 ]; then
    log "push of $LOCAL_SHA did not land within 90s — aborting (push failed or offline)"
    exit 0
  fi
  log "push landed — syncing docs"
fi

if [ ! -d "$DOCS_REPO_DIR" ]; then
  log "docs repo not found at $DOCS_REPO_DIR — set DOCS_REPO_DIR or clone video-build-docs"
  exit 0
fi

# -------- 2. copy + rewrite --------------------------------------------------
DOCS_GUIDE="$DOCS_REPO_DIR/docs/guide"
DOCS_PUBLIC="$DOCS_REPO_DIR/docs/public"
mkdir -p "$DOCS_GUIDE" "$DOCS_PUBLIC"

sed -e 's|](\./SKILL\.md)|(/guide/skill)|g' \
    -e 's|](static/|(/|g' \
    README.md > "$DOCS_GUIDE/readme.md"

sed -e 's|skills/manim-video/|https://github.com/mattstyles333/video-build/tree/master/skills/manim-video/|g' \
    SKILL.md > "$DOCS_GUIDE/skill.md"

cp install.md "$DOCS_GUIDE/install.md"

cp -f static/* "$DOCS_PUBLIC/" 2>/dev/null || true
[ -f poster.html ] && cp -f poster.html "$DOCS_PUBLIC/poster.html"

# -------- 3. optional opencode polish pass ------------------------------------
find_opencode() {
  [ -n "${DOCSYNC_OPENCODE:-}" ] && { echo "$DOCSYNC_OPENCODE"; return; }
  command -v opencode2 2>/dev/null || command -v opencode 2>/dev/null || {
    find "$HOME/.local/share/mise/installs" -type f -path '*/node_modules/.bin/opencode2' 2>/dev/null | head -1
  }
}

cd "$DOCS_REPO_DIR" || exit 1

if [ "${DOCSYNC_SKIP_LLM:-0}" = "1" ]; then
  log "skipping opencode pass (DOCSYNC_SKIP_LLM=1)"
else
  OC="$(find_opencode)"
  if [ -n "$OC" ]; then
    log "running opencode polish pass in $DOCS_REPO_DIR"
    PROMPT="video-build docs sync. The source repo just pushed new changes and its README.md, SKILL.md, and install.md were copied into docs/guide/. Review what changed (git status + diff), then update this docs site so it stays coherent: fix nav/sidebar/cross-links (docs/.vitepress/config.mts), refresh docs/index.md summaries, correct any broken links or typos in the synced guide files. Prefer editing files owned here (index.md, config.mts) over the synced guide files — fix those upstream instead. Verify with 'npm run docs:build', then git add and commit all changes."
    "$OC" run --auto --standalone ${DOCSYNC_MODEL:+-m "$DOCSYNC_MODEL"} "$PROMPT" \
      || log "opencode pass failed (exit $?) — committing synced content anyway"
  else
    log "opencode not found — committing synced content without polish"
  fi
fi

# -------- 4. commit + push ----------------------------------------------------
git add -A
if git diff --cached --quiet; then
  log "docs repo already up to date — nothing to commit"
  exit 0
fi
git commit -q -m "docs: sync from video-build${LOCAL_SHA:+ @ ${LOCAL_SHA:0:7}}" \
  || log "commit failed (nothing to commit?)"
if git push -q origin HEAD 2>&1; then
  log "pushed docs repo"
else
  log "docs repo push failed (offline?) — committed locally"
fi
