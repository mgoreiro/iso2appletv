#!/usr/bin/env bash
# Crea unos vídeos de prueba en /tmp/transcodex-demo para grabar docs/demo.gif
set -e
D=/tmp/transcodex-demo
rm -rf "$D"; mkdir -p "$D/in" "$D/out"
for n in pelicula serie-e01 serie-e02; do
  ffmpeg -loglevel error -y -f lavfi -i testsrc=duration=20:size=1280x720:rate=24 \
    -f lavfi -i sine=duration=20 -c:v libx264 -c:a aac -shortest "$D/in/$n.mkv"
done
