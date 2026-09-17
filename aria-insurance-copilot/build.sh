#!/usr/bin/env bash
#
# Assembles the two builds from the four source parts in src/.
#
#   index.html  standalone document, open directly in a browser or serve it
#   embed.html  body-only build for embedding inside a host page
#
# There is no bundler, transpiler or package install step. The source parts are
# concatenated in order, which is the whole build.
#
set -euo pipefail
cd "$(dirname "$0")"

PARTS=(src/01-styles.html src/02-markup.html src/03-prompt.html src/04-app.html)

for p in "${PARTS[@]}"; do
  [ -f "$p" ] || { echo "missing source part: $p" >&2; exit 1; }
done

cat "${PARTS[@]}" > embed.html

{
  echo '<!doctype html>'
  echo '<html lang="en">'
  echo '<head>'
  echo '<meta charset="utf-8">'
  echo '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'
  echo '<meta name="description" content="ARIA, an insurance underwriting copilot: structured risk intake, tier recommendation and an underwriting worksheet.">'
  echo '<style>:root{color-scheme:light;padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}body{margin:0}</style>'
  echo '</head>'
  echo '<body>'
  cat "${PARTS[@]}"
  echo '</body>'
  echo '</html>'
} > index.html

echo "built:"
wc -c index.html embed.html
