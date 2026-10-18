"""Order affected-test commands and their generated-artifact prerequisites."""

CHECK_TAILWIND_COMMAND = "make check-tailwind"
CSS_BUILD_COMMAND = "make css-build"
PLAYWRIGHT_CORE_COMMAND = "make test-playwright-core"
PLAYWRIGHT_FULL_COMMAND = "make test-playwright"
PLAYWRIGHT_FILE_COMMAND_PREFIX = "uv run pytest playwright_tests/"
TAILWIND_CONTRACT_LABEL = "tests.test_tailwind_build.TailwindSourceContractTest"


def label_selects(label: str, target: str) -> bool:
    """Return whether a Django label selects ``target`` or one of its tests."""
    return label == target or target.startswith(f"{label}.") or label.startswith(f"{target}.")


def django_labels_need_css(labels: list[str]) -> bool:
    """Return whether selected Django labels include a compiled-CSS reader."""
    for label in labels:
        if label_selects(label, TAILWIND_CONTRACT_LABEL):
            return True
    return False


def _supplemental_commands(extra_commands: list[str], playwright: str, *, moved_tailwind_gate: bool) -> list[str]:
    commands: list[str] = []
    for command in extra_commands:
        if moved_tailwind_gate and command == CHECK_TAILWIND_COMMAND:
            continue
        if playwright == "full" and command.startswith(PLAYWRIGHT_FILE_COMMAND_PREFIX):
            continue
        commands.append(command)
    return commands


def ordered_commands(
    django_labels: list[str],
    django_command: str | None,
    extra_commands: list[str],
    playwright: str,
) -> list[str]:
    """Return the plan's commands in their executable dependency order."""
    commands: list[str] = []
    needs_css = bool(django_command) and django_labels_need_css(django_labels)
    has_tailwind_gate = CHECK_TAILWIND_COMMAND in extra_commands
    if needs_css:
        prerequisite = CSS_BUILD_COMMAND
        if has_tailwind_gate:
            prerequisite = CHECK_TAILWIND_COMMAND
        commands.append(prerequisite)
    if django_command:
        commands.append(django_command)
    commands.extend(_supplemental_commands(extra_commands, playwright, moved_tailwind_gate=needs_css))
    if playwright == "core":
        commands.append(PLAYWRIGHT_CORE_COMMAND)
    elif playwright == "full":
        commands.append(PLAYWRIGHT_FULL_COMMAND)
    return commands
