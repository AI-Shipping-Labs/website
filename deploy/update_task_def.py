import json
import sys

ALLOWED_HOSTS_BY_ENV = {
    "dev": ["dev.aishippinglabs.com"],
    "prod": [
        "aishippinglabs.com",
        "www.aishippinglabs.com",
        "prod.aishippinglabs.com",
    ],
}


def _set_env_var(environment, name, value):
    for env_var in environment:
        if env_var["name"] == name:
            env_var["value"] = value
            return
    environment.append({"name": name, "value": value})


def _required_allowed_hosts(deploy_env):
    return ALLOWED_HOSTS_BY_ENV.get(deploy_env, ALLOWED_HOSTS_BY_ENV["dev"])


def _allowed_hosts_for_env(deploy_env):
    return ",".join(_required_allowed_hosts(deploy_env))


def update_task_definition(input_file, new_tag, output_file, deploy_env="dev"):
    print(f"Updating task definition from {input_file} with tag {new_tag}")

    try:
        with open(input_file, "r") as f:
            task_def = json.load(f)["taskDefinition"]

        for container_def in task_def["containerDefinitions"]:
            if "image" in container_def:
                base_image, _ = container_def["image"].split(":")
                container_def["image"] = f"{base_image}:{new_tag}"

            environment = container_def.get("environment", [])
            _set_env_var(environment, "VERSION", new_tag)
            _set_env_var(
                environment,
                "ALLOWED_HOSTS",
                _allowed_hosts_for_env(deploy_env),
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
