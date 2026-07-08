import copy
import json
import os
import sys

ALLOWED_HOSTS_BY_ENV = {
    "dev": ["dev.aishippinglabs.com"],
    "prod": [
        "aishippinglabs.com",
        "www.aishippinglabs.com",
    ],
}

# Django 4+ requires scheme-qualified origins. CSRF middleware uses these
# to validate the Origin / Referer header on POST over HTTPS, in addition
# to ALLOWED_HOSTS. Without this, any form POST in prod (login, payments,
# studio, etc.) fails with 403.
CSRF_TRUSTED_ORIGINS_BY_ENV = {
    "dev": ["https://dev.aishippinglabs.com"],
    "prod": [
        "https://aishippinglabs.com",
        "https://www.aishippinglabs.com",
    ],
}

# Canonical base URL per environment. Drives every absolute URL the app
# generates (unsubscribe, calendar invites, password resets, share URLs,
# OG / canonical meta, OAuth redirect URIs). Without this, dev tasks
# would default to the prod literal in settings.py and ship prod links
# from the dev environment.
SITE_BASE_URL_BY_ENV = {
    "dev": "https://dev.aishippinglabs.com",
    "prod": "https://aishippinglabs.com",
}

VALID_ROLES = {"combined", "web", "worker"}

# Issue #1141 Phase 2C: gunicorn worker count per environment. Dev runs 2 to
# ease pressure in the shared 512 MB task; prod keeps 3. Read at boot from
# ``os.environ['GUNICORN_WORKERS']`` (NOT get_config -- see
# ``scripts/entrypoint_init._gunicorn_worker_count``).
GUNICORN_WORKERS_BY_ENV = {
    "dev": "2",
    "prod": "3",
}


def _set_env_var(environment, name, value):
    for env_var in environment:
        if env_var["name"] == name:
            env_var["value"] = value
            return
    environment.append({"name": name, "value": value})


def _remove_env_var(environment, name):
    environment[:] = [env_var for env_var in environment if env_var["name"] != name]


def _predeploy_migrate_check_enabled():
    value = os.environ.get("PREDEPLOY_MIGRATE_CHECK_ENABLED", "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _run_migrations_for_container(container_name):
    # entrypoint.sh runs `manage.py migrate` only when RUN_MIGRATIONS=true.
    # Two containers in the same task race on migrations with mixed DDL +
    # data steps and deadlock (issue #336). Pick the web container as the
    # single migrator; everything else (workers, sidecars) skips.
    #
    # Issue #1141 Phase 2A keeps RUN_MIGRATIONS for the legacy BOOT_MODE-absent
    # path. While aws-infra#12 is not applied, deploys intentionally use that
    # legacy path so they do not need ecs:RunTask.
    if container_name and container_name.endswith("-worker"):
        return "false"
    return "true"


def _boot_mode_for_container(container_name):
    # Issue #1141 Phase 2A: the SERVING task def sets BOOT_MODE=web on the
    # essential (web) container and BOOT_MODE=worker on the worker
    # container/sidecar. Serving containers then SKIP migrate + check (those
    # run once in the pre-deploy one-off task with a BOOT_MODE=predeploy
    # override) and bind fast. The worker is identified by its `-worker`
    # name suffix, matching _run_migrations_for_container / _ensure_worker_sidecar.
    if container_name and container_name.endswith("-worker"):
        return "worker"
    return "web"


def _required_allowed_hosts(deploy_env):
    return ALLOWED_HOSTS_BY_ENV.get(deploy_env, ALLOWED_HOSTS_BY_ENV["dev"])


def _allowed_hosts_for_env(deploy_env):
    return ",".join(_required_allowed_hosts(deploy_env))


def _csrf_trusted_origins_for_env(deploy_env):
    return ",".join(
        CSRF_TRUSTED_ORIGINS_BY_ENV.get(deploy_env, CSRF_TRUSTED_ORIGINS_BY_ENV["dev"])
    )


def _site_base_url_for_env(deploy_env):
    return SITE_BASE_URL_BY_ENV.get(deploy_env, SITE_BASE_URL_BY_ENV["dev"])


def _gunicorn_workers_for_env(deploy_env):
    return GUNICORN_WORKERS_BY_ENV.get(deploy_env, GUNICORN_WORKERS_BY_ENV["dev"])


def _ensure_worker_sidecar(containers):
    # Combined-role task: web + worker share one ECS task. Clone the web
    # container so image, secrets, and log config stay in sync — only
    # name, command, port mappings, and essential flag differ.
    #
    # ``command`` is set here for ECS console readability only. The
    # Dockerfile sets ENTRYPOINT to ``scripts/entrypoint_init.py`` without
    # consuming ``$@``, so the actual web/worker/predeploy dispatch is driven
    # by the ``BOOT_MODE`` env var (``web`` on the essential container,
    # ``worker`` on this sidecar; ``predeploy`` is a run-task override).
    # ``RUN_MIGRATIONS`` is kept only for the legacy BOOT_MODE-absent
    # fallback. If you need to change worker behaviour, edit
    # ``scripts/entrypoint_init.py`` — editing this command field has no
    # runtime effect.
    if any(c["name"].endswith("-worker") for c in containers):
        return

    web = next(c for c in containers if c.get("essential"))
    worker = copy.deepcopy(web)
    worker["name"] = f"{web['name']}-worker"
    worker["essential"] = False
    worker["command"] = ["uv", "run", "python", "manage.py", "qcluster"]
    worker["portMappings"] = []
    containers.append(worker)


def _apply_role(task_def, role):
    containers = task_def["containerDefinitions"]
    if role == "web":
        # Worker runs as its own ECS service (prod). Strip any sidecar
        # left over from earlier two-container revisions so the new
        # revision is single-container.
        task_def["containerDefinitions"] = [
            c for c in containers if not c["name"].endswith("-worker")
        ]
    elif role == "worker":
        # Worker-only task def. Keep only the worker container in case
        # the source task def carries an unrelated container.
        task_def["containerDefinitions"] = [
            c for c in containers if c["name"].endswith("-worker")
        ]
    elif role == "combined":
        _ensure_worker_sidecar(containers)


def update_task_definition(
    input_file, new_tag, output_file, deploy_env="dev", role="combined"
):
    if role not in VALID_ROLES:
        print(f"Invalid role: {role}. Must be one of {sorted(VALID_ROLES)}.")
        sys.exit(1)

    print(
        f"Updating task definition from {input_file} with tag {new_tag} "
        f"(env={deploy_env}, role={role})"
    )

    try:
        with open(input_file, "r") as f:
            task_def = json.load(f)["taskDefinition"]

        _apply_role(task_def, role)

        predeploy_enabled = _predeploy_migrate_check_enabled()

        for container_def in task_def["containerDefinitions"]:
            if "image" in container_def:
                base_image, _ = container_def["image"].split(":")
                container_def["image"] = f"{base_image}:{new_tag}"

            environment = container_def.get("environment", [])
            _set_env_var(environment, "VERSION", new_tag)
            _set_env_var(environment, "DEBUG", "False")
            _set_env_var(
                environment,
                "ALLOWED_HOSTS",
                _allowed_hosts_for_env(deploy_env),
            )
            _set_env_var(
                environment,
                "CSRF_TRUSTED_ORIGINS",
                _csrf_trusted_origins_for_env(deploy_env),
            )
            _set_env_var(
                environment,
                "SITE_BASE_URL",
                _site_base_url_for_env(deploy_env),
            )
            _set_env_var(
                environment,
                "RUN_MIGRATIONS",
                _run_migrations_for_container(container_def.get("name")),
            )
            if predeploy_enabled:
                _set_env_var(
                    environment,
                    "BOOT_MODE",
                    _boot_mode_for_container(container_def.get("name")),
                )
            else:
                _remove_env_var(environment, "BOOT_MODE")
            _set_env_var(
                environment,
                "GUNICORN_WORKERS",
                _gunicorn_workers_for_env(deploy_env),
            )
            container_def["environment"] = environment

        # Remove fields not allowed in register-task-definition
        for key in [
            "status",
            "revision",
            "taskDefinitionArn",
            "requiresAttributes",
            "compatibilities",
            "registeredAt",
            "registeredBy",
        ]:
            task_def.pop(key, None)

        with open(output_file, "w") as f:
            json.dump(task_def, f, indent=4)

        print(f"Updated task definition saved to {output_file}")

    except FileNotFoundError:
        print(f"File not found: {input_file}")
        sys.exit(1)
    except json.JSONDecodeError:
        print("Invalid JSON input.")
        sys.exit(1)
    except KeyError:
        print("Invalid task definition structure.")
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) not in {4, 5, 6}:
        print(
            "Usage: python update_task_def.py <input_file> <new_tag> "
            "<output_file> [deploy_env] [role]"
        )
        sys.exit(1)

    deploy_env = sys.argv[4] if len(sys.argv) >= 5 else "dev"
    role = sys.argv[5] if len(sys.argv) == 6 else "combined"
    update_task_definition(sys.argv[1], sys.argv[2], sys.argv[3], deploy_env, role)
