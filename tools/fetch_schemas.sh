#!/usr/bin/env bash
# Copy Microsoft's published Fabric/Power BI JSON schemas (MIT licence) at a pinned commit into .schemas/.
set -euo pipefail
COMMIT=49f4a76cb7a75fee2669b7cc6ba12ca4332050f7   # microsoft/json-schemas main, 2026-10-12
DEST="${1:-.schemas/json-schemas}"
if [ -d "$DEST/.git" ] && [ "$(git -C "$DEST" rev-parse HEAD)" = "$COMMIT" ]; then
  echo "schemas already at $COMMIT"; exit 0
fi
rm -rf "$DEST"
git init -q "$DEST"
git -C "$DEST" remote add origin https://github.com/microsoft/json-schemas.git
git -C "$DEST" sparse-checkout set fabric/item/report fabric/item/semanticModel fabric/gitIntegration fabric/pbip
git -C "$DEST" fetch -q --depth 1 --filter=blob:none origin "$COMMIT"
git -C "$DEST" checkout -q FETCH_HEAD
echo "schemas at $(git -C "$DEST" rev-parse HEAD)"
