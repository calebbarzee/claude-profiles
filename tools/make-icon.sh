#!/usr/bin/env bash
# make-icon.sh — render assets/icon.png from swap_icon.svg.
#
# The source is a Noun Project export: a 100x125 viewBox whose bottom 25 units
# hold a 5px attribution line, illegible at icon size. Crop back to the 100x100
# artwork box; the attribution lives in the README.
#
# Shapes carry no fill attribute, so one CSS rule recolors them. BRAND comes
# from sample-brand-color.sh. A mid-tone orange reads on light and dark Raycast
# themes, so no icon@dark.png variant is needed.
set -euo pipefail

BRAND="${BRAND:-#D97757}"

cd "$(dirname "$0")/.."
mkdir -p assets .tmp

command -v rsvg-convert >/dev/null 2>&1 || {
  echo "error: rsvg-convert not found (brew install librsvg / apt install librsvg2-bin)" >&2
  exit 1
}

crop='s{<text.*?</text>}{}s; s{viewBox="0 0 100 125"}{viewBox="0 0 100 100"}'
tint="s{<defs\\s+id=\"defs3\"\\s*/>}{<defs><style>*{fill:$BRAND}</style></defs>}"

perl -0777 -pe "$crop; $tint" swap_icon.svg > .tmp/icon.svg
rsvg-convert -w 512 -h 512 .tmp/icon.svg -o assets/icon.png

# Dimensions come straight out of the PNG IHDR chunk, so this reports the same
# way on macOS and Linux with no sips or ImageMagick dependency.
python3 - assets/icon.png "$BRAND" <<'PY'
import os, struct, sys

path, brand = sys.argv[1], sys.argv[2]
with open(path, 'rb') as fh:
    width, height = struct.unpack('>II', fh.read(24)[16:24])
print(f'{path}  {width}x{height}  {os.path.getsize(path)} bytes  fill {brand}')
PY
