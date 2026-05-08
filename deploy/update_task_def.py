import copy
import json
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


def _set_env_var(environment, name, value):
    for env_var in environment:
        if env_var["name"] == name:
            env_var["value"] = value
            return
    environment.append({"name": name, "value": value})


def _run_migrations_for_container(container_name):
    # entrypoint.sh runs `manage.py migrate` only when RUN_MIGRATIONS=true.
    # Two containers in the same task race on migrations with mixed DDL +
    # data steps and deadlock (issue #336). Pick the web container as the
    # single migrator; everything else (workers, sidecars) skips.
    if container_name and container_name.endswith("-worker"):
        return "false"
    return "true"


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


def _ensure_worker_container(containers):
    # Source-of-truth for the qcluster sidecar. Cloning from the web
    # container keeps image, secrets, and log config in sync — only the
    # name, command, port mappings, and essential flag differ.
    if any(c["name"].endswith("-worker") for c in containers):
        return

    web = next(c for c in containers if c.get("essential"))
    worker = copy.deepcopy(web)
    worker["name"] = f"{web['name']}-worker"
    worker["essential"] = False
    worker["command"] = ["uv", "run", "python", "manage.py", "qcluster"]
    worker["portMappings"] = []
    containers.append(worker)


def update_task_definition(input_file, new_tag, output_file, deploy_env="dev"):
    print(f"Updating task definition from {input_file} with tag {new_tag}")

    try:
        with open(input_file, "r") as f:
            task_def = json.load(f)["taskDefinition"]

        _ensure_worker_container(task_def["containerDefinitions"])

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
    if len(sys.argv) not in {4, 5}:
        print(
            "Usage: python update_task_def.py <input_file> <new_tag> "
            "<output_file> [deploy_env]"
        )
        sys.exit(1)

    deploy_env = sys.argv[4] if len(sys.argv) == 5 else "dev"
    update_task_definition(sys.argv[1], sys.argv[2], sys.argv[3], deploy_env)
