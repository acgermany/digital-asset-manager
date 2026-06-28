#!/usr/bin/env bash
# Transcode a media file to H.264/AAC MP4.
# Usage: transcode_h264_mp4.sh <input_file>
# Output is written alongside the source file with _h264.mp4 suffix.
set -euo pipefail

INPUT="$1"
OUTPUT="${INPUT%.*}_h264.mp4"

ffmpeg -hide_banner -i "$INPUT" \
  -c:v libx264 -crf 18 -preset slow \
  -c:a aac -b:a 192k \
  -movflags +faststart \
  "$OUTPUT"

echo "Done: $OUTPUT"
