# DEV routing outage, 24 October 2026

Tracking: website issue `#2005`, Hub task
`5237a12c-f875-4dc7-a657-67ed10389ed7`.

## Findings

| Evidence | Finding |
|---|---|
| Last successful scheduled DEV run | 23 October 2026, 06:15 UTC |
| First failed scheduled DEV run | 23 October 2026, 12:43 UTC |
| DNS and live proxy | DEV and production resolve to the active Contabo host |
| Existing source web | Healthy; responds HTTP 200 with a full revision to `/ping` using the DEV Host |
| Source routing metadata | `traefik.enable=false`; saved source web FQDN is null |
| Rolling public Application | Production domains configured; DEV absent from its domains and routing labels |
| Traefik file-provider fallback | `default_redirect_503.yaml` routes unmatched requests to `noop` with `servers: {}` |
| Existing proxy connectivity | Proxy is already attached to the source UUID network and resolves the stable source web name |

DEV had no matching router with an available backend. Requests fell through to
the empty fallback service, explaining HTTP 503 `no available server` despite
a healthy source web. No stopped DEV container or failed source healthcheck
was found. There is no separate DEV container in the current topology.

The exact original routing-removal time and responsible change are not proven
by the available live evidence. The current rolling Application was created
at 13:15 UTC on 23 October, after the first failure. The exact-head release
change `#1996` deployed in run `40011239610`, created at 19:25 UTC, with the
runtime deployed around 19:50 UTC. That deployment was later than the first
failure, so it did not initiate this outage. Its production-only public probe
did leave a confirmed verification blind spot for the independently routed
DEV hostname.

## Repair and prevention

The repository now owns `deploy/coolify/dev-route.yml`. It attaches only the
DEV Host to the already healthy source web through Traefik's watched
file-provider directory, preserving production Docker labels and domains.
Atomic installation requires no container restart or recreation. The route
includes an active `/ping` healthcheck using the DEV Host.

The exact-release helper's final public gate now requires the expected full
revision from both production and DEV for three consecutive probe cycles,
using the existing no-redirect HTTP reader. Offline tests prove that successful
production responses cannot hide unavailable, empty, stale or redirecting DEV
responses.

The operational handoff records repeated DEV and production HTTP 200 checks,
unchanged source web/worker, rolling web and proxy identities/start times, and
the manually dispatched DEV readiness result in the Hub task. See
[`../coolify-dev-route.md`](../coolify-dev-route.md) for installation,
verification and rollback.
