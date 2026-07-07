"""Output and cell formatting tests for ``asl_cli.output``."""

from __future__ import annotations

import json

from asl_cli import output


def test_json_is_pretty_printed(capsys):
    output.print_output({"a": 1, "b": [1, 2]}, fmt="json")
    out = capsys.readouterr().out
    assert out.endswith("\n")
    # Indented (multi-line) pretty output, not a single compact line.
    assert '\n  "a": 1' in out
    assert json.loads(out) == {"a": 1, "b": [1, 2]}


def test_json_preserves_non_ascii(capsys):
    output.print_output({"name": "café ☕"}, fmt="json")
    out = capsys.readouterr().out
    assert "café ☕" in out
    assert "\\u" not in out


def test_raw_is_a_single_compact_line(capsys):
    data = [{"a": 1}, {"b": 2}]
    output.print_output(data, fmt="raw")
    out = capsys.readouterr().out
    assert out.count("\n") == 1
    assert json.loads(out) == data


def test_table_renders_aligned_header_and_separator(capsys):
    rows = [{"id": 1, "name": "alice"}, {"id": 22, "name": "bob"}]
    output.print_output(rows, fmt="table")
    lines = capsys.readouterr().out.splitlines()

    assert lines[0].split() == ["id", "name"]
    # Separator line is only dashes and spaces.
    assert set(lines[1]) <= {"-", " "}
    assert "-" in lines[1]
    # Both data rows present, and the id column is padded to width 2.
    assert lines[2].startswith("1 ")
    assert lines[3].startswith("22")
    assert "alice" in lines[2]
    assert "bob" in lines[3]


def test_table_wraps_a_single_dict_into_one_row(capsys):
    output.print_output({"id": 1, "name": "solo"}, fmt="table")
    lines = capsys.readouterr().out.splitlines()
    # header + separator + exactly one data row
    assert len(lines) == 3
    assert "solo" in lines[2]


def test_table_prints_nothing_for_empty_list(capsys):
    output.print_output([], fmt="table")
    assert capsys.readouterr().out == ""


def test_table_truncates_long_cells(capsys):
    rows = [{"c": "x" * 100}]
    output.print_output(rows, fmt="table")
    out = capsys.readouterr().out
    assert "x" * 100 not in out
    assert ("x" * 47 + "...") in out


def test_format_cell_booleans():
    assert output._format_cell(True) == "true"
    assert output._format_cell(False) == "false"


def test_format_cell_none_is_empty():
    assert output._format_cell(None) == ""


def test_format_cell_nested_values_are_json():
    assert output._format_cell({"k": "v"}) == '{"k": "v"}'
    assert output._format_cell([1, 2]) == "[1, 2]"


def test_table_renders_mixed_cell_types_without_crashing(capsys):
    rows = [{"flag": True, "empty": None, "nested": {"a": 1}}]
    output.print_output(rows, fmt="table")
    out = capsys.readouterr().out
    assert "true" in out
    assert '{"a": 1}' in out
    # No raw Python repr leaks (dict repr uses single quotes / True).
    assert "'a'" not in out
    assert "True" not in out
