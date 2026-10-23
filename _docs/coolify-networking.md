# Coolify networking

The AISHL Coolify service needs two pre-existing networks when Docker creates
each application container:

- the service UUID network for Coolify and Traefik;
- the external `coolify` network for PostgreSQL DNS and connectivity.

The raw Compose saved through the Coolify API is the production authority. It
contains both `web` and `worker` plus their current images, commands,
environment, volumes, health checks, restart policies, and deployment
metadata. The repository's `deploy/coolify/docker-compose.staging.yml` is a
historical, intentionally web-only template and is not consumed by the live
deployment. Never apply that template to the production service.

## Creation-time topology

The 2026-10-23 incident established the startup race. Concurrent restart and
deployment activities invoked the installed Coolify `StartService` path,
which ran `docker compose up` before its automatic
`docker network connect coolify ... || true` step. The application could
therefore start before the PostgreSQL hostname existed in container DNS, and
the ignored connect failure could leave it there. Automatic network attachment
did not provide a startup-order guarantee.

The saved raw and generated Compose now express the network topology at
container creation. This sanitized excerpt omits every non-network field and
replaces the actual service UUID:

```yaml
services:
  web:
    networks:
      - <service-uuid>
      - coolify
  worker:
    networks:
      - <service-uuid>
      - coolify

networks:
  <service-uuid>:
    external: true
    name: <service-uuid>
  coolify:
    external: true
    name: coolify
```

Docker connects both networks before either entrypoint starts. The UUID
network preserves Coolify and Traefik connectivity, while `coolify` exposes
the standalone PostgreSQL hostname.

## Updating the saved Compose

Read the current raw Compose through the Coolify API and keep a complete,
private pre-change backup. Make a minimal parsed-YAML modification to the
saved document: preserve the current UUID network, add `coolify` to both
`web` and `worker`, and declare the existing `coolify` network as external.
Preserve every other field exactly, especially the worker, images, commands,
environment, volumes, health checks, restart policies, and deployment
metadata. Do not construct the update from the repository template.

Save through the API, read the raw Compose back, and inspect Coolify's
generated Compose. The generated `web` and `worker` network lists must each
contain the service UUID and `coolify`, and both top-level definitions must
refer to the pre-existing networks. Sanitize diagnostic output and never
record environment values or credentials.

The API update persists parsed Compose in Coolify's database. It intentionally
does not rewrite the host's current `docker-compose.yml` or alter the running
containers. Do not manually run or overwrite that stale deployment file as a
validation step. On the next normal deployment, `StartService` parses the
saved definition and `saveComposeConfigs` writes the corrected file before
`docker compose up`.

## Non-disruptive validation

Saving the definition does not require restarting a healthy stack. Record the
active `web` and `worker` container IDs before the save and confirm that they
remain unchanged afterward. Confirm both containers are running on both
networks, PostgreSQL resolves from `web`, and `/ping` returns HTTP 200.

Prove the next-start behavior with a disposable Compose project that uses the
deployed image and the same generated network declarations. Override its
entrypoint to run only `getent hosts <postgres-service-hostname>`. Do not
publish ports, start the application, run migrations, or pass production
secrets. A successful lookup on the first process invocation proves that DNS
is available at container creation. Remove the disposable resources and
confirm the healthy application container IDs are unchanged.

At the next routine deployment, inspect the new `web` and `worker` containers
and repeat the two-network, PostgreSQL DNS, worker-running, and public `/ping`
checks. Serialize deploy and restart requests: wait for the current Coolify
activity to finish before retrying, because overlapping Compose operations can
leave a container in `Created`. Do not force a production redeploy merely to
validate a persisted configuration.

## Rollback

Restore the prior saved raw Compose through the Coolify API from the complete,
private pre-change backup, without replacing unrelated fields. Read it back
before any deployment action. Never publish the backup or include it in an
incident report because it may contain secrets. Removing the creation-time
network declaration restores the startup race, so keep healthy containers
running and deploy the rollback only for a demonstrated fault.
