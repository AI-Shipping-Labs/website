import sys
import json


def update_task_definition(input_file, new_tag, output_file):
    print(f"Updating task definition from {input_file} with tag {new_tag}")

    try:
        with open(input_file, "r") as f:
            task_def = json.load(f)["taskDefinition"]

        for container_def in task_def["containerDefinitions"]:
            if "image" in container_def:
                base_image, _ = container_def["image"].split(":")
                container_def["image"] = f"{base_image}:{new_tag}"

            environment = container_def.get("environment", [])
            version_found = False
            for env_var in environment:
                if env_var["name"] == "VERSION":
                    env_var["value"] = new_tag
                    version_found = True
            if not version_found:
                environment.append({"name": "VERSION", "value": new_tag})

        # Validate expected containers are present
        container_names = {c["name"] for c in task_def["containerDefinitions"]}
        expected = {"ai-shipping-labs", "ai-shipping-labs-worker"}
        missing = expected - container_names
        if missing:
            print(f"ERROR: Missing containers in task definition: {missing}")
            print("This likely means the task definition was manually edited incorrectly.")
            sys.exit(1)

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
    if len(sys.argv) != 4:
        print("Usage: python update_task_def.py <input_file> <new_tag> <output_file>")
        sys.exit(1)

    update_task_definition(sys.argv[1], sys.argv[2], sys.argv[3])
