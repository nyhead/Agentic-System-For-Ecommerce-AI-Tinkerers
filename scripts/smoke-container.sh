#!/usr/bin/env bash
set -euo pipefail
image="${1:?Usage: bash scripts/smoke-container.sh IMAGE}"
container_id="$(docker run --detach --publish 127.0.0.1::8000 "$image")"
cleanup() {
  docker rm --force "$container_id" >/dev/null
}
trap cleanup EXIT
endpoint="http://$(docker port "$container_id" 8000/tcp)/api/health"
for attempt in {1..30}; do
  if curl --fail --silent --show-error --max-time 2 "$endpoint" 2>/dev/null | \
    python3 -c 'import json,sys; sys.exit(0 if json.load(sys.stdin).get("status") == "ok" else 1)' 2>/dev/null; then
    printf 'Container readiness passed: /api/health returned status=ok.\n'
    exit 0
  fi
  if [[ "$(docker inspect --format '{{.State.Running}}' "$container_id")" != "true" ]]; then
    break
  fi
  sleep 1
done
docker logs --tail 100 "$container_id"
printf 'Container failed readiness; no live API keys were provided.\n' >&2
exit 1
