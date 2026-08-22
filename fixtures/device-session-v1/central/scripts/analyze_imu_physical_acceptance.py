#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "jsonschema[format]>=4.23,<5",
# ]
# ///
"""Dry-run analyzer for ylx.imu-physical-acceptance.v1 evidence JSON."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from imu_physical_acceptance import (
    ImuPhysicalAcceptanceError,
    analyze_imu_physical_acceptance,
    load_strict_json_file,
)


CONTRACTS = Path(__file__).resolve().parents[1]
SCHEMA = CONTRACTS / "schemas" / "ylx-imu-physical-acceptance-v1.schema.json"


def _load_json(path: Path) -> Any:
    return load_strict_json_file(path)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Validate and analyze an existing IMU physical acceptance evidence JSON. "
            "The analyzer is read-only by default and never controls hardware."
        )
    )
    parser.add_argument("evidence_json", type=Path)
    parser.add_argument(
        "--skip-schema",
        action="store_true",
        help="Skip JSON Schema validation and run only procedural analysis.",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print the JSON report.",
    )
    args = parser.parse_args()

    try:
        evidence = _load_json(args.evidence_json)
    except ImuPhysicalAcceptanceError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from error
    schema_errors: list[str] = []
    schema_status = "SKIPPED" if args.skip_schema else "VALID"
    if args.skip_schema:
        schema_errors = ["schema validation skipped; analyzer output is non-certifying"]
    else:
        try:
            schema = _load_json(SCHEMA)
        except ImuPhysicalAcceptanceError as error:
            print(str(error), file=sys.stderr)
            raise SystemExit(1) from error
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        schema_errors = [
            ".".join(str(part) for part in error.absolute_path) + ": " + error.message
            for error in sorted(validator.iter_errors(evidence), key=lambda item: list(item.absolute_path))
        ]
        schema_status = "VALID" if not schema_errors else "INVALID"

    report = analyze_imu_physical_acceptance(evidence)
    report["input_path"] = str(args.evidence_json)
    report["schema_errors"] = schema_errors
    report["schema_status"] = schema_status
    if args.skip_schema:
        report["overall_status"] = "NON_CERTIFYING"
    else:
        report["overall_status"] = (
            "VALID"
            if report["gate_status"] == "VALID" and report["schema_status"] == "VALID"
            else "INVALID"
        )
    print(
        json.dumps(
            report,
            allow_nan=False,
            sort_keys=True,
            indent=2 if args.pretty else None,
            separators=None if args.pretty else (",", ":"),
        )
    )
    if report["overall_status"] != "VALID":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
