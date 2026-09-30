#!/usr/bin/env bash
# Converts the real hero video / poster and the collection photo into the
# files the home page serves (web/public/media/site/), within the budgets of
# scripts/measure-assets.mjs: video <= 4 MB per file, stills <= 400 KB.
#
#   web/scripts/prepare-site-media.sh \
#     [--video FILE] [--video-mobile FILE] [--start SECONDS] [--duration SECONDS] \
#     [--poster FILE] [--poster-mobile FILE] [--collection FILE]
#
#   --video          landscape clip; -> hero-desktop.{mp4,webm} 1920x1080
#   --video-mobile   portrait clip; without it the landscape clip is centre-cropped to 9:16
#   --start/--duration  cut (default: from 0, 9 s)
#   --poster         landscape still; without it, a frame of the video at --start + 1 s
#   --poster-mobile  portrait still; without it, the poster centre-cropped to 9:16
#   --collection     photo for "entrada a la colección"; -> collection-entry 1200x1500 (4:5)
#
# Every output: no audio track, no metadata (EXIF/GPS, creation time, device),
# fixed sizes (cover crop, centred). Needs ffmpeg with libx264, libvpx-vp9,
# libaom-av1 and libwebp. Nothing outside web/public/media/site/ is touched,
# and existing files there are replaced (they are generated).
set -euo pipefail

VIDEO="" VIDEO_MOBILE="" START=0 DURATION=9 POSTER="" POSTER_MOBILE="" COLLECTION=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --video) VIDEO="$2"; shift 2 ;;
    --video-mobile) VIDEO_MOBILE="$2"; shift 2 ;;
    --start) START="$2"; shift 2 ;;
    --duration) DURATION="$2"; shift 2 ;;
    --poster) POSTER="$2"; shift 2 ;;
    --poster-mobile) POSTER_MOBILE="$2"; shift 2 ;;
    --collection) COLLECTION="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
for f in "$VIDEO" "$VIDEO_MOBILE" "$POSTER" "$POSTER_MOBILE" "$COLLECTION"; do
  [[ -z "$f" || -f "$f" ]] || { echo "not a file: $f" >&2; exit 2; }
done
[[ -n "$VIDEO$POSTER$COLLECTION" ]] || { echo "nothing to do: give --video, --poster and/or --collection" >&2; exit 2; }

OUT="$(cd "$(dirname "$0")/.." && pwd)/public/media/site"
mkdir -p "$OUT"
FF=(ffmpeg -hide_banner -loglevel error -y)
MAX_VIDEO=$((4 * 1024 * 1024))
MAX_STILL=$((400 * 1024))
size() { stat -c %s "$1"; }

# cover crop to WxH, centred
cover() { echo "scale=$1:$2:force_original_aspect_ratio=increase,crop=$1:$2,setsar=1"; }

still() { # src W H basename
  local src="$1" w="$2" h="$3" name="$4" vf; vf="$(cover "$w" "$h")"
  "${FF[@]}" -i "$src" -frames:v 1 -vf "$vf" -map_metadata -1 -q:v 3 "$OUT/$name.jpg"
  "${FF[@]}" -i "$src" -frames:v 1 -vf "$vf" -map_metadata -1 -c:v libwebp -quality 78 "$OUT/$name.webp"
  "${FF[@]}" -i "$src" -frames:v 1 -vf "$vf,format=yuv420p" -map_metadata -1 -c:v libaom-av1 -still-picture 1 -crf 34 -cpu-used 6 "$OUT/$name.avif"
  for ext in jpg webp avif; do
    if (( $(size "$OUT/$name.$ext") > MAX_STILL )); then
      echo "WARNING: $name.$ext is $(( $(size "$OUT/$name.$ext") / 1024 )) KB (> 400 KB budget)" >&2
    fi
  done
  echo "  $name  ${w}x${h}  jpg $(( $(size "$OUT/$name.jpg") / 1024 )) KB · webp $(( $(size "$OUT/$name.webp") / 1024 )) KB · avif $(( $(size "$OUT/$name.avif") / 1024 )) KB"
}

clip() { # src W H basename [extra-filter]
  local src="$1" w="$2" h="$3" name="$4" vf kbps
  vf="$(cover "$w" "$h"),fps=30,format=yuv420p"
  # bitrate that keeps the file under ~3.6 MB for the chosen duration
  kbps=$(( 3600 * 8 / DURATION ))
  (( kbps > 4000 )) && kbps=4000
  "${FF[@]}" -ss "$START" -t "$DURATION" -i "$src" -an -map_metadata -1 -vf "$vf" \
    -c:v libx264 -profile:v high -preset slow -b:v "${kbps}k" -maxrate "$((kbps * 12 / 10))k" -bufsize "$((kbps * 2))k" \
    -movflags +faststart "$OUT/$name.mp4"
  "${FF[@]}" -ss "$START" -t "$DURATION" -i "$src" -an -map_metadata -1 -vf "$vf" \
    -c:v libvpx-vp9 -b:v "$((kbps * 8 / 10))k" -maxrate "${kbps}k" -row-mt 1 -deadline good -cpu-used 2 "$OUT/$name.webm"
  for ext in mp4 webm; do
    if (( $(size "$OUT/$name.$ext") > MAX_VIDEO )); then
      echo "FAIL: $name.$ext is $(( $(size "$OUT/$name.$ext") / 1024 )) KB (> 4 MB); use a shorter --duration" >&2
      exit 1
    fi
  done
  echo "  $name  ${w}x${h}  ${DURATION}s  mp4 $(( $(size "$OUT/$name.mp4") / 1024 )) KB · webm $(( $(size "$OUT/$name.webm") / 1024 )) KB  (no audio)"
}

echo "Writing to $OUT"
if [[ -n "$VIDEO" ]]; then
  clip "$VIDEO" 1920 1080 hero-desktop
  clip "${VIDEO_MOBILE:-$VIDEO}" 1080 1920 hero-mobile
  if [[ -z "$POSTER" ]]; then
    POSTER="$OUT/.frame.png"
    "${FF[@]}" -ss "$(awk "BEGIN{print $START + 1}")" -i "$VIDEO" -frames:v 1 "$POSTER"
  fi
fi
if [[ -n "$POSTER" ]]; then
  still "$POSTER" 1920 1080 hero-poster-desktop
  still "${POSTER_MOBILE:-$POSTER}" 900 1600 hero-poster-mobile
fi
[[ -n "$COLLECTION" ]] && still "$COLLECTION" 1200 1500 collection-entry
rm -f "$OUT/.frame.png"
echo "Done. Next: point web/src/content/site.ts at /media/site/, set provisional: false and write real alt texts."
