#!/bin/sh
# Wrap office.html (an Artifact fragment) in the page skeleton the Artifact host adds,
# so it can be opened directly in a browser.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
out=${1:-/tmp/fieldbook-office.html}
{
  printf '<!doctype html><html lang="en"><head><meta charset="utf-8">'
  printf '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
  printf '<style>:root{color-scheme:light;padding:env(safe-area-inset-top) 0 env(safe-area-inset-bottom)}body{margin:0}[hidden]{display:none!important}</style>'
  printf '</head><body>'
  cat "$here/office.html"
  printf '</body></html>\n'
} > "$out"
echo "Wrote $out"
