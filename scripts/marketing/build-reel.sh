#!/bin/bash
#
# Assemble the launch reel from captured frames + rendered overlays.
# Run capture-reel-frames.mjs and render-reel-overlays.mjs first.
#
#   ./scripts/marketing/build-reel.sh
#
# Writes branding/marketing/melting-moments-launch-reel.mp4 (gitignored).
#
# Overlay timings below are tied to the shot boundaries in
# capture-reel-frames.mjs — re-time them together if the shot list changes.
# marks.json in the work dir records where each segment actually starts.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORK="${WORK:-${TMPDIR:-/tmp}/mm-reel}"
WORK="${WORK%/}"
DEST="$REPO_ROOT/branding/marketing"
OUT="$DEST/melting-moments-launch-reel.mp4"

for d in "$WORK/frames" "$WORK/ov"; do
  [ -d "$d" ] || { echo "missing $d — run the capture and overlay scripts first" >&2; exit 1; }
done
mkdir -p "$DEST"

# ── 1. site footage + timed text overlays ───────────────────────────────
ffmpeg -y -loglevel error \
  -framerate 30 -i "$WORK/frames/f%05d.png" \
  -loop 1 -i "$WORK/ov/t1.png" \
  -loop 1 -i "$WORK/ov/t2.png" \
  -loop 1 -i "$WORK/ov/t3.png" \
  -loop 1 -i "$WORK/ov/pill.png" \
  -filter_complex "\
[1]format=rgba,fade=in:st=0.5:d=0.5:alpha=1,fade=out:st=3.4:d=0.5:alpha=1[t1];\
[2]format=rgba,fade=in:st=8.7:d=0.5:alpha=1,fade=out:st=11.6:d=0.5:alpha=1[t2];\
[3]format=rgba,fade=in:st=13.0:d=0.5:alpha=1,fade=out:st=15.6:d=0.5:alpha=1[t3];\
[4]format=rgba,fade=in:st=4.3:d=0.5:alpha=1,fade=out:st=17.1:d=0.5:alpha=1[pill];\
[0][t1]overlay=enable='between(t,0.5,3.9)'[v1];\
[v1][t2]overlay=enable='between(t,8.7,12.1)'[v2];\
[v2][t3]overlay=enable='between(t,13.0,16.1)'[v3];\
[v3][pill]overlay=enable='between(t,4.3,17.6)',format=yuv420p[vout]" \
  -map "[vout]" -t 18.0 -r 30 \
  -c:v libx264 -preset slow -crf 18 -profile:v high -level 4.0 "$WORK/base.mp4"

# ── 2. end card, slow push-in ───────────────────────────────────────────
ffmpeg -y -loglevel error -loop 1 -i "$WORK/ov/endcard.png" \
  -vf "zoompan=z='min(zoom+0.00030,1.05)':d=135:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920:fps=30,format=yuv420p" \
  -frames:v 135 -r 30 \
  -c:v libx264 -preset slow -crf 18 -profile:v high -level 4.0 "$WORK/end.mp4"

# ── 3. crossfade into the end card ──────────────────────────────────────
#
# The silent audio track is deliberate: some platforms mishandle a video with
# no audio stream at all, and the music gets picked in-app anyway.
ffmpeg -y -loglevel error \
  -i "$WORK/base.mp4" -i "$WORK/end.mp4" \
  -f lavfi -t 30 -i anullsrc=channel_layout=stereo:sample_rate=48000 \
  -filter_complex "[0:v][1:v]xfade=transition=fade:duration=0.6:offset=17.4,format=yuv420p[v]" \
  -map "[v]" -map 2:a -shortest \
  -c:v libx264 -preset slow -crf 18 -profile:v high -level 4.0 \
  -c:a aac -b:a 128k -movflags +faststart "$OUT"

echo "-> $OUT"
ffprobe -v error -show_entries format=duration,size:stream=codec_name,width,height \
  -of default=noprint_wrappers=1 "$OUT"
