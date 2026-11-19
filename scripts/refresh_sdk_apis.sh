#!/bin/sh
# Rebuild tests/data/ncp_sdk_apis.txt from the official NCP Go SDK.
# Usage: sh scripts/refresh_sdk_apis.sh   (from the repository root; needs git)
# Then run: uv run pytest tests/test_sdk_apis.py
set -eu

out="tests/data/ncp_sdk_apis.txt"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

git clone -q --depth 1 https://github.com/NaverCloudPlatform/ncloud-sdk-go-v2 "$tmp/sdk"
rev=$(git -C "$tmp/sdk" log -1 --format='%h (%cs)')

{
  echo "# Every API in the official NCP Go SDK (github.com/NaverCloudPlatform/ncloud-sdk-go-v2)."
  echo "# Snapshot of commit $rev. Columns: service, HTTP method used by the SDK, path."
  echo "# Refresh: see CONTRIBUTING.md."
  for dir in "$tmp"/sdk/services/*/; do
    svc=$(basename "$dir")
    # README rows look like: | **Post** /getServerInstanceList |
    grep -hoE '\| \*\*(Get|Post|Put|Patch|Delete)\*\* [^ |]+' "$dir/README.md" 2>/dev/null \
      | sed "s/^| \*\*\([A-Za-z]*\)\*\* /$svc \1 /" || true
  done | LC_ALL=C sort
} > "$out"

echo "wrote $(grep -vc '^#' "$out") APIs from SDK $rev to $out"
