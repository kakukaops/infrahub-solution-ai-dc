#!/usr/bin/env bash
set -euo pipefail

usage() {
    printf 'Usage: %s {start|stop|status} [--edition community|enterprise]\n' "${0##*/}"
}

case "${1:-}" in
    start) compose_args=(up -d --no-build) ;;
    # Give Neo4j time to complete its shutdown before Docker sends SIGKILL.
    stop) compose_args=(stop --timeout 60) ;;
    status) compose_args=(ps --all) ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
esac
shift

edition=${INFRAHUB_EDITION:-community}
if (( $# != 0 )); then
    if [[ $# -ne 2 || $1 != --edition || -z $2 ]]; then
        usage >&2
        exit 2
    fi
    edition=$2
fi

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

case "$edition" in
    community)
        export INFRAHUB_BASE_IMAGE=registry.opsmill.io/opsmill/infrahub
        export INFRAHUB_SOLUTION_IMAGE=opsmill/infrahub-solution-ai-dc
        export INFRAHUB_IMAGE_USER=root
        ;;
    enterprise)
        export INFRAHUB_BASE_IMAGE=registry.opsmill.io/opsmill/infrahub-enterprise
        export INFRAHUB_SOLUTION_IMAGE=opsmill/infrahub-enterprise-solution-ai-dc
        export INFRAHUB_IMAGE_USER=infrahub
        ;;
    *) usage >&2; exit 2 ;;
esac

compose_edition=community
if [[ ! -r docker-compose.yml ]]; then
    printf 'Error: docker-compose.yml is missing or unreadable.\n' >&2
    exit 1
fi
if grep -Fq 'registry.opsmill.io/opsmill/infrahub-enterprise' docker-compose.yml; then
    compose_edition=enterprise
fi
if [[ $edition != "$compose_edition" ]]; then
    printf 'Error: docker-compose.yml is for %s, but %s was requested.\n' "$compose_edition" "$edition" >&2
    exit 1
fi

if [[ -z ${INFRAHUB_BASE_VERSION:-} ]]; then
    INFRAHUB_BASE_VERSION=$(awk -F '"' '
        /^\[\[package\]\]/ { found = 0 }
        $1 == "name = " && $2 == "infrahub-testcontainers" { found = 1 }
        found && $1 == "version = " { print $2; exit }
    ' uv.lock)
fi
: "${INFRAHUB_BASE_VERSION:?Set INFRAHUB_BASE_VERSION to the Infrahub image tag}"
export INFRAHUB_BASE_VERSION

exec docker compose "${compose_args[@]}"
