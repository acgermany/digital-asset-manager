#!/usr/bin/env bash
# Deploy a CI-built image tag on the homelab docker host.
# Usage: deploy/homelab/deploy.sh <image-tag>     (run from a checkout, on docker.jib)
# Copies the compose files into /opt/dam, sets DAM_TAG in /opt/dam/.env, pulls and restarts.
set -euo pipefail
tag=$1
src=$(cd "$(dirname "$0")/../.." && pwd)
dst=/opt/dam

[ -f "$dst/.env" ] || { echo "missing $dst/.env (created when the host was set up)" >&2; exit 1; }
cp "$src/docker-compose.yml" "$dst/docker-compose.yml"
cp "$src/deploy/homelab/compose.override.yml" "$dst/compose.override.yml"

cd "$dst"
sed -i "s/^DAM_TAG=.*/DAM_TAG=$tag/" .env
docker compose pull --quiet app frontend
docker compose up -d --no-build --remove-orphans
docker compose ps --format 'table {{.Name}}\t{{.Image}}\t{{.Status}}'
