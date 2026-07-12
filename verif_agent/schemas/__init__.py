"""JSON Schemas for self-validation. Mirrors the 7 required output files."""
import os
import json
from pathlib import Path

_SCHEMAS_DIR = Path(__file__).parent

_DESIGN = {
    "type": "object",
    "required": ["top", "rtl_files", "ports", "clock", "reset",
                 "inferred_protocols", "primary_protocol"],
    "properties": {
        "top": {"type": "string"},
        "rtl_files": {"type": "array", "items": {"type": "string"}},
        "compile_order": {"type": "array"},
        "include_dirs": {"type": "array"},
        "clock": {"type": "array"},
        "reset": {"type": "array"},
        "parameters": {"type": "array"},
        "ports": {"type": "array", "items": {
            "type": "object",
            "required": ["name", "direction", "width", "protocol_group", "role"],
        }},
        "inferred_protocols": {"type": "array"},
        "primary_protocol": {"type": "string"},
        "schema_version": {"type": "integer"},
        "case_name": {"type": "string"},
    },
}

_SKELETON = {
    "type": "object",
    "required": ["clock_reset", "drivers", "monitors", "scoreboard", "dut_wiring", "testbench_source"],
    "properties": {
        "drivers": {"type": "array", "minItems": 0},
        "monitors": {"type": "array", "minItems": 0},
        "scoreboard": {"type": "object"},
        "testbench_source": {"type": "string"},
    },
}

_BINS = {
    "type": "object",
    "required": ["coverpoints"],
    "properties": {
        "coverpoints": {"type": "array", "minItems": 0, "items": {
            "type": "object",
            "required": ["name", "bins"],
            "properties": {
                "name": {"type": "string"},
                "bins": {"type": "array", "minItems": 1, "items": {
                    "type": "object",
                    "required": ["name", "scenario", "sampling_condition"],
                    "properties": {
                        "name": {"type": "string"},
                        "scenario": {"type": "string"},
                        "sampling_condition": {"type": "string", "minLength": 1},
                        "covered": {"type": "boolean"},
                        "hit_count": {"type": "integer", "minimum": 0},
                    },
                }},
            },
        }},
    },
}

_FUNCTIONAL = {
    "type": "object",
    "required": ["covered_bins", "valid_bins", "functional_coverage_pct", "per_coverpoint"],
    "properties": {
        "covered_bins": {"type": "integer", "minimum": 0},
        "valid_bins": {"type": "integer", "minimum": 0},
        "functional_coverage_pct": {"type": "number", "minimum": 0, "maximum": 100},
        "per_coverpoint": {"type": "array"},
        "bin_summary": {"type": "object"},
    },
}

_COVERAGE_RESULT = {
    "type": "object",
    "required": ["line", "branch", "functional", "combined_C"],
    "properties": {
        "line": {"type": "number", "minimum": 0, "maximum": 100},
        "branch": {"type": "number", "minimum": 0, "maximum": 100},
        "functional": {"type": "number", "minimum": 0, "maximum": 100},
        "combined_C": {"type": "number", "minimum": 0, "maximum": 100},
        "line_hits": {"type": "integer"},
        "line_total": {"type": "integer"},
        "branch_hits": {"type": "integer"},
        "branch_total": {"type": "integer"},
        "functional_hits": {"type": "integer"},
        "functional_total": {"type": "integer"},
    },
}

_CONSTRAINTS = {
    "type": "object",
    "required": ["seed", "num_seq", "random_variables"],
    "properties": {
        "seed": {"type": "integer"},
        "num_seq": {"type": "integer"},
        "random_variables": {"type": "array"},
        "protocol_constraints": {"type": "array"},
        "coverage_feedback_updates": {"type": "array"},
    },
}

_REPORT = {
    "type": "object",
    "required": ["stages", "outputs", "reproducible_command", "environment"],
    "properties": {
        "stages": {
            "type": "object",
            "required": ["parse", "skeleton_gen", "simulate", "coverage_collect"],
        },
        "outputs": {"type": "object"},
        "coverage_summary": {"type": "object"},
        "reproducible_command": {"type": "string", "minLength": 1},
        "environment": {"type": "object"},
        "failures": {"type": "array"},
    },
}


_SCHEMAS = {
    "design.json": _DESIGN,
    "verification_skeleton.json": _SKELETON,
    "coverage_bins.json": _BINS,
    "functional_coverage.json": _FUNCTIONAL,
    "coverage_result.json": _COVERAGE_RESULT,
    "constraints.json": _CONSTRAINTS,
    "report.json": _REPORT,
}


def schema_for(name: str) -> dict:
    return _SCHEMAS[name]


def all_schemas() -> dict[str, dict]:
    return dict(_SCHEMAS)


def _write_schemas_to_disk() -> None:
    for name, schema in _SCHEMAS.items():
        out = _SCHEMAS_DIR / name.replace(".json", ".schema.json")
        out.write_text(json.dumps(schema, indent=2, ensure_ascii=False), encoding="utf-8")


_write_schemas_to_disk()
