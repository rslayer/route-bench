#!/usr/bin/env bash
#
# Build an OSRM routing graph from an OpenStreetMap extract.
#
# OSRM is the difference between a real benchmark and a straight-line estimate:
# without it RouteBench falls back to haversine and withholds the grade
# entirely. This script exists because the manual version is four docker
# invocations with a filename convention that is easy to get subtly wrong, and
# getting it wrong produces an OSRM that starts and then 400s every request.
#
# Usage:
#   scripts/bootstrap_osrm.sh                    # default region (texas)
#   scripts/bootstrap_osrm.sh california
#   scripts/bootstrap_osrm.sh --url https://download.geofabrik.de/europe/monaco-latest.osm.pbf
#
# The region name becomes OSRM_REGION in .env, which docker-compose reads. No
# renaming: the previous docs renamed everything to "region.osrm" via a shell
# loop, which is a lot of ceremony to avoid one variable.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="${REPO_ROOT}/osrm-data"
PROFILE="/opt/car.lua"
IMAGE="osrm/osrm-backend:latest"

REGION="texas"
URL=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --url) URL="$2"; shift 2 ;;
    --help|-h) sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) REGION="$1"; shift ;;
  esac
done

if [[ -n "$URL" ]]; then
  PBF_NAME="$(basename "$URL")"
  REGION="${PBF_NAME%%-latest.osm.pbf}"
  REGION="${REGION%%.osm.pbf}"
else
  # Geofabrik's US extracts. Anything outside the US needs --url.
  URL="https://download.geofabrik.de/north-america/us/${REGION}-latest.osm.pbf"
  PBF_NAME="${REGION}-latest.osm.pbf"
fi

BASE="${PBF_NAME%.osm.pbf}"

if ! docker info >/dev/null 2>&1; then
  echo "error: Docker is not running. Start Docker Desktop and retry." >&2
  exit 1
fi

mkdir -p "$DATA_DIR"

echo "==> Region:  ${REGION}"
echo "==> Source:  ${URL}"
echo "==> Target:  ${DATA_DIR}/${BASE}.osrm"
echo

if [[ -f "${DATA_DIR}/${BASE}.osrm.mldgr" ]]; then
  echo "==> Graph already built. Delete ${DATA_DIR} to rebuild."
  exit 0
fi

if [[ ! -f "${DATA_DIR}/${PBF_NAME}" ]]; then
  echo "==> Downloading extract (this is the slow part; a US state is ~0.5-1.5 GB)"
  curl -fL --progress-bar -o "${DATA_DIR}/${PBF_NAME}.part" "$URL"
  # Only rename once complete, so an interrupted download is never mistaken
  # for a usable extract on the next run.
  mv "${DATA_DIR}/${PBF_NAME}.part" "${DATA_DIR}/${PBF_NAME}"
else
  echo "==> Extract already downloaded"
fi

# extract -> partition -> customize is the MLD pipeline, and the compose file
# runs osrm-routed with --algorithm mld. The two must agree: pairing an MLD
# graph with the CH algorithm (or vice versa) starts cleanly and then fails
# every query at runtime.
run_osrm() {
  docker run --rm -t -v "${DATA_DIR}:/data" "$IMAGE" "$@"
}

echo "==> osrm-extract (needs RAM roughly equal to the .pbf size)"
run_osrm osrm-extract -p "$PROFILE" "/data/${PBF_NAME}"

echo "==> osrm-partition"
run_osrm osrm-partition "/data/${BASE}.osrm"

echo "==> osrm-customize"
run_osrm osrm-customize "/data/${BASE}.osrm"

ENV_FILE="${REPO_ROOT}/.env"
touch "$ENV_FILE"
if grep -q '^OSRM_REGION=' "$ENV_FILE"; then
  # BSD sed (macOS) needs the empty -i argument; GNU sed does not accept it.
  if sed --version >/dev/null 2>&1; then
    sed -i "s|^OSRM_REGION=.*|OSRM_REGION=${BASE}|" "$ENV_FILE"
  else
    sed -i '' "s|^OSRM_REGION=.*|OSRM_REGION=${BASE}|" "$ENV_FILE"
  fi
else
  echo "OSRM_REGION=${BASE}" >> "$ENV_FILE"
fi

cat <<EOF

==> Done. Graph built at ${DATA_DIR}/${BASE}.osrm
    OSRM_REGION=${BASE} written to .env

Start it:
    docker compose up -d osrm

Verify (should return a durations matrix, not an error):
    curl -s "http://localhost:5000/table/v1/driving/-96.797,32.777;-96.780,32.800" | head -c 200

Then point RouteBench at it (already the default):
    OSRM_HOST=http://localhost:5000
EOF
