#!/usr/bin/env bash
# Render assets/icon.png from swap_icon.svg.
#
# The source is a Noun Project export: a 100x125 viewBox where the bottom 25
# units hold a 5px attribution line, illegible at icon size. We crop back to
# the 100x100 artwork box and carry the attribution in the README instead.
#
# Shapes carry no fill attribute, so a CSS rule recolors them. BRAND is the
# most common opaque non-white pixel in /Applications/Claude.app's own icon
# (see sample_color.sh). One mid-tone orange reads on light and dark Raycast
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

# Read the dimensions straight out of the PNG IHDR chunk, so this reports the
# same way on macOS and Linux without depending on sips or ImageMagick.
python3 - assets/icon.png "$BRAND" <<'PY'
import struct, sys
path, brand = sys.argv[1], sys.argv[2]
with open(path, 'rb') as fh:
    head = fh.read(24)
w, h = struct.unpack('>II', head[16:24])
import os
print(f'{path}  {w}x{h}  {os.path.getsize(path)} bytes  fill {brand}')
PY
