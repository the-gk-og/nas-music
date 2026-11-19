#!/usr/bin/env bash
# Fails if an APK contains Google Play Services / Firebase / Play Core code or manifest entries.
# The `foss` flavour must pass; the `full` flavour (Google Cast) must fail, which CI also asserts so this
# check can't silently stop detecting anything.
#   scripts/check-no-gms.sh app-foss-release.apk            -> exit 0 if clean
#   scripts/check-no-gms.sh --expect-gms app-full-release.apk -> exit 0 if Google code IS found
set -euo pipefail
expect=0
if [[ "${1:-}" == "--expect-gms" ]]; then expect=1; shift; fi
apk="${1:?usage: check-no-gms.sh [--expect-gms] <apk>}"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
unzip -q -o "$apk" 'classes*.dex' AndroidManifest.xml -d "$tmp"
# Dex files store type descriptors as plain MUTF-8 strings, e.g. "Lcom/google/android/gms/cast/CastDevice;".
pattern='Lcom/google/android/gms/|Lcom/google/firebase/|Lcom/google/android/play/core/|Lcom/google/mlkit/'
hits="$(cat "$tmp"/classes*.dex | grep -aoE "(${pattern})[A-Za-z0-9_/\$]*" | sort -u || true)"
# The binary manifest stores strings as UTF-16LE; strip NULs to search it.
mhits="$(tr -d '\000' < "$tmp/AndroidManifest.xml" | grep -aoE 'com\.google\.(android\.gms|firebase)[A-Za-z0-9_.]*' | sort -u || true)"
n=$(( $(printf '%s' "$hits" | grep -c . || true) + $(printf '%s' "$mhits" | grep -c . || true) ))
if (( expect )); then
  if (( n == 0 )); then echo "FAIL: expected Google Play Services code in $apk but found none (is the check broken?)"; exit 1; fi
  echo "OK: $apk contains Google code as expected ($n references)"; exit 0
fi
if (( n > 0 )); then
  echo "FAIL: $apk contains Google Play Services / Firebase references:"
  printf '%s\n' "$hits" "$mhits" | grep . | head -40
  exit 1
fi
echo "OK: $apk contains no Google Play Services / Firebase code"
