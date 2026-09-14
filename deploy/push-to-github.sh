#!/usr/bin/env bash
# Push this project to GitHub. Run it on YOUR machine, where your git
# credentials live -- not in a sandbox that has none.
#
# The token never has to be pasted into a chat. Either let git prompt you, or
# export GITHUB_TOKEN in your own shell first.
#
# Usage:
#   bash deploy/push-to-github.sh                    # safe default: new branch
#   PUSH_TARGET=main bash deploy/push-to-github.sh   # merge alongside the guides
#   PUSH_REPO=https://github.com/<you>/<other>.git bash deploy/push-to-github.sh
#
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"

REPO="${PUSH_REPO:-https://github.com/rihito0san-cloud/araena.git}"
TARGET="${PUSH_TARGET:-fleet}"          # branch name; 'main' merges with the guides
REMOTE_NAME="${REMOTE_NAME:-origin}"

echo "== MinerPotatos -> GitHub =="
echo "  project : $ROOT"
echo "  remote  : $REPO"
echo "  branch  : $TARGET"
echo

# ---------------------------------------------------------------- preflight
if ! git -C "$ROOT" rev-parse --git-dir >/dev/null 2>&1; then
  echo "ERROR: $ROOT is not a git repository." >&2
  echo "Run:  git init -b main && git add -A && git commit -m 'MinerPotatos fleet'" >&2
  exit 1
fi

LOCAL_BRANCH="$(git -C "$ROOT" branch --show-current)"
if [ -z "$LOCAL_BRANCH" ]; then
  echo "ERROR: no checked-out branch; commit your work first." >&2
  exit 1
fi

# Refuse to push operator state. These hold spending history and the selected
# signer address, and .gitignore already excludes them -- this is the check
# that .gitignore is actually being honoured.
if git -C "$ROOT" ls-files | grep -qE 'launch\.sqlite|active-signer\.json'; then
  echo "ERROR: operator state is tracked by git. Untrack it before pushing:" >&2
  echo "  git rm --cached controller/launch.sqlite* controller/active-signer.json" >&2
  exit 1
fi
echo "  preflight: no operator state tracked"

# A leaked key would be permanent, so scan what is about to be published.
if git -C "$ROOT" grep -nIE \
  '0x[0-9a-fA-F]{64}' -- . ':!tests/' ':!worker/vectors.inc' >/dev/null 2>&1; then
  echo "WARNING: a 32-byte hex string appears outside tests/ and vectors.inc." >&2
  echo "Verify it is not a private key before continuing:" >&2
  git -C "$ROOT" grep -nIE '0x[0-9a-fA-F]{64}' -- . ':!tests/' ':!worker/vectors.inc' | head -5 >&2
  read -r -p "Continue anyway? [y/N] " ans
  [ "$ans" = "y" ] || [ "$ans" = "Y" ] || exit 1
fi

# ------------------------------------------------------------------ remote
if git -C "$ROOT" remote get-url "$REMOTE_NAME" >/dev/null 2>&1; then
  CURRENT="$(git -C "$ROOT" remote get-url "$REMOTE_NAME")"
  if [ "$CURRENT" != "$REPO" ]; then
    echo "  remote '$REMOTE_NAME' is $CURRENT; switching to $REPO"
    git -C "$ROOT" remote set-url "$REMOTE_NAME" "$REPO"
  fi
else
  git -C "$ROOT" remote add "$REMOTE_NAME" "$REPO"
fi

echo "  fetching remote state..."
git -C "$ROOT" fetch "$REMOTE_NAME" --quiet || {
  echo "ERROR: fetch failed. Check the URL and that you have push access." >&2
  exit 1
}

# --------------------------------------------------------------- push plan
REMOTE_HAS_TARGET=0
if git -C "$ROOT" rev-parse --verify --quiet \
     "$REMOTE_NAME/$TARGET" >/dev/null 2>&1; then
  REMOTE_HAS_TARGET=1
fi

if [ "$TARGET" = "main" ]; then
  # The remote main already carries the two specification guides. Merge them
  # in rather than overwriting: this project is an implementation of those
  # guides and belongs next to them, not on top of them.
  if git -C "$ROOT" rev-parse --verify --quiet "$REMOTE_NAME/main" >/dev/null 2>&1; then
    echo "  remote main exists; merging its history so the guides are kept"
    git -C "$ROOT" merge --allow-unrelated-histories --no-edit \
      "$REMOTE_NAME/main" || {
      echo "ERROR: merge conflict. Resolve it, then re-run." >&2
      exit 1
    }
  fi
  git -C "$ROOT" push "$REMOTE_NAME" "$LOCAL_BRANCH:main"
else
  if [ "$REMOTE_HAS_TARGET" -eq 1 ]; then
    echo "  remote branch '$TARGET' already exists; pushing as an update"
  else
    echo "  creating new remote branch '$TARGET' (main is left untouched)"
  fi
  git -C "$ROOT" push -u "$REMOTE_NAME" "$LOCAL_BRANCH:$TARGET"
fi

echo
echo "== done =="
git -C "$ROOT" log --oneline -3
echo
echo "The remote main still holds MINERPOTATOS_AI_BUILD_GUIDE.md and"
echo "PRSPCT_Complete_Infrastructure_Guide.md unless you pushed to main."
