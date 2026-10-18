"""Reproducible acceptance check using 5,000 original synthetic CSV rows.

This is a local test fixture, not customer data or a paid customer case.
No network calls or third-party packages are used. By default, all generated
files live inside a TemporaryDirectory owned by this invocation. --output
retains the fixture, two CLI deliveries and result.json in a new directory.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import csv
import hashlib
import io
import json
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time


FIELDS = ("order_id", "customer", "amount", "currency", "note")
AUDIT_FIELDS = ("source_file", "source_line", "reason", *(
    "data." + field for field in FIELDS))
EXPECTED_COUNTS = {
    "input_records": 5000,
    "clean_records": 3850,
    "exact_duplicates_removed": 600,
    "exception_records": 550,
    "conflicting_keys": 150,
    "missing_key_records": 250,
}
OUTPUT_FILES = ("clean.csv", "duplicates.csv", "exceptions.csv", "report.json", "preview.html")
SOURCE_LAYOUTS = (
    ("batch-a.csv", ",", FIELDS),
    ("batch-b.csv", ";", ("currency", "note", "amount", "customer", "order_id")),
    ("batch-c.csv", "\t", ("note", "order_id", "currency", "customer", "amount")),
)


class VerificationError(ValueError):
    """An independently expected acceptance condition was not met."""


def require(condition, message):
    if not condition:
        raise VerificationError(message)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def base_values(number):
    # Amounts are strings, never rounded or converted through floating point.
    amount = ("0.1000" if number % 17 == 0
              else f"{100 + number // 100}.{number % 100:02d}")
    notes = (
        f"合成订单 {number}，中文与逗号, 原文保留",
        f'合成订单 {number}；含双引号 "原样"',
        f"合成订单 {number}\n第二行备注，保持换行",
        f'合成订单 {number}, 含 "引号"\n以及第二行',
    )
    return (f"{number:06d}", f"合成客户{number:04d}", amount,
            "USD", notes[number % len(notes)])


def encoded_row(values, delimiter):
    """Serialize one row so physical start lines are counted before parsing."""
    stream = io.StringIO(newline="")
    csv.writer(stream, delimiter=delimiter, lineterminator="\n").writerow(values)
    return stream.getvalue()


def build_fixture(root):
    inputs = root / "inputs"
    inputs.mkdir(exist_ok=False)
    buckets = [[], [], []]
    # Categories are assigned by fixture design, not discovered by the tool's
    # conflict/deduplication implementation or inferred from its output.
    for number in range(1, 4001):
        values = base_values(number)
        reason = "conflicting_key" if number <= 150 else None
        buckets[(number - 1) % 3].append((values, values, "exception" if reason else "clean", reason))

    for number in range(151, 751):
        values = base_values(number)
        raw = tuple(" " + value + " " for value in values)
        # Its unpadded base row is earlier in the SAME source file, so the
        # expected duplicate's provenance is fixed independently of read order.
        buckets[(number - 1) % 3].append((raw, values, "duplicate", "exact_duplicate"))

    for number in range(1, 151):
        original = base_values(number)
        values = (*original[:2], f"{number + 9000}.500", *original[3:])
        buckets[number % 3].append((values, values, "exception", "conflicting_key"))

    for number in range(1, 251):
        values = ("", f"缺编号合成客户{number:03d}", "13.000", "USD",
                  f'待确认 {number}, "没有编号"\n第二行说明')
        raw = ("   ", *values[1:]) if number % 2 else values
        buckets[(number - 1) % 3].append((raw, values, "exception", "missing_key"))

    oracle = {"clean": Counter(), "duplicate": Counter(), "exception": Counter()}
    source_expectations = []
    paths = []
    for (name, delimiter, order), entries in zip(SOURCE_LAYOUTS, buckets):
        content = [encoded_row(order, delimiter)]
        physical_line = content[0].count("\n") + 1
        changed = 0
        for raw_values, expected_values, category, reason in entries:
            raw = dict(zip(FIELDS, raw_values))
            fragment = encoded_row(tuple(raw[field] for field in order), delimiter)
            if category == "clean":
                oracle[category][expected_values] += 1
            else:
                oracle[category][(name, str(physical_line), reason, *expected_values)] += 1
            changed += raw_values != expected_values
            content.append(fragment)
            physical_line += fragment.count("\n")
        path = inputs / name
        path.write_text("".join(content), encoding="utf-8-sig", newline="")
        paths.append(path)
        source_expectations.append({
            "file": name,
            "sha256": sha256(path),
            "delimiter": delimiter,
            "records": len(entries),
            "blank_records_skipped": 0,
            "whitespace_changed_records": changed,
        })
    require(sum(len(bucket) for bucket in buckets) == 5000, "Fixture must contain exactly 5,000 rows.")
    require(sum(oracle["clean"].values()) == 3850, "Fixture clean oracle must contain 3,850 rows.")
    require(sum(oracle["duplicate"].values()) == 600, "Fixture duplicate oracle must contain 600 rows.")
    require(sum(oracle["exception"].values()) == 550, "Fixture exception oracle must contain 550 rows.")
    return paths, oracle, source_expectations


def read_rows(path, expected_headers):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream, strict=True)
        headers = next(reader, None)
        require(headers == list(expected_headers), f"Unexpected output columns in {path.name}.")
        rows = list(reader)
    require(all(len(row) == len(expected_headers) for row in rows), f"Invalid output width in {path.name}.")
    return Counter(tuple(row) for row in rows)


def compare_multiset(actual, expected, label):
    if actual != expected:
        missing = sum((expected - actual).values())
        extra = sum((actual - expected).values())
        raise VerificationError(f"{label}: {missing} expected rows missing; {extra} unexpected rows present.")


def assert_inputs_unchanged(paths, source_expectations):
    for path, expected in zip(paths, source_expectations):
        require(sha256(path) == expected["sha256"], f"Input was modified: {path.name}.")


def run_and_verify(root, name, paths, oracle, source_expectations):
    output = root / name
    script = Path(__file__).resolve().with_name("csv_delivery.py")
    command = [sys.executable, "-X", "utf8", str(script), *(str(path) for path in paths),
               "--output", str(output), "--key", "order_id", "--trim", "--dedupe-exact",
               "--max-rows", "5000"]
    began = time.perf_counter()
    run = subprocess.run(command, cwd=script.parent, capture_output=True, text=True,
                         encoding="utf-8", errors="strict", timeout=120, check=False)
    require(run.returncode == 0, f"{name}: CLI exit {run.returncode}: {run.stderr.strip()}")
    require(json.loads(run.stdout) == EXPECTED_COUNTS, f"{name}: CLI count output differs from fixed oracle.")
    require(all((output / filename).is_file() for filename in OUTPUT_FILES), f"{name}: Delivery bundle is incomplete.")
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    require(report["counts"] == EXPECTED_COUNTS, f"{name}: Report counts differ from fixed oracle.")
    require(report["sources"] == source_expectations, f"{name}: Source counts, format, hashes or trim counts differ.")
    require(report["rules"] == {
        "key": "order_id", "trim_outer_whitespace": True, "dedupe_exact_rows": True,
        "encoding": "utf-8-sig", "values_kept_as_text": True, "conflict_policy": "quarantine_all",
    }, f"{name}: Recorded processing rules differ from the invoked rules.")
    compare_multiset(read_rows(output / "clean.csv", FIELDS), oracle["clean"], name + " clean fields")
    compare_multiset(read_rows(output / "duplicates.csv", AUDIT_FIELDS), oracle["duplicate"],
                     name + " duplicate fields and source lines")
    compare_multiset(read_rows(output / "exceptions.csv", AUDIT_FIELDS), oracle["exception"],
                     name + " exception fields, reasons and source lines")
    assert_inputs_unchanged(paths, source_expectations)
    return {
        "name": name,
        "cli_exit_code": run.returncode,
        "elapsed_seconds": round(time.perf_counter() - began, 6),
        "counts": report["counts"],
        "checks_passed": [
            "real_cli_success_and_exact_fixed_counts",
            "complete_five_file_delivery_bundle",
            "exact_source_hashes_delimiters_counts_and_trim_counts",
            "exact_processing_rules",
            "complete_clean_field_multiset_against_independent_oracle",
            "complete_duplicate_field_multiset_with_original_source_lines",
            "complete_exception_field_multiset_with_reasons_and_original_source_lines",
            "original_input_sha256_unchanged",
        ],
        "output_sha256": {filename: sha256(output / filename) for filename in OUTPUT_FILES},
    }


def verify_in(root):
    project = Path(__file__).resolve().parent
    implementation_paths = [project / "csv_delivery.py", project / "preview_template.html", project / "verify_batch.py"]
    implementation_hashes = {path.name: sha256(path) for path in implementation_paths}
    paths, oracle, sources = build_fixture(root)
    first = run_and_verify(root, "run-1", paths, oracle, sources)
    second = run_and_verify(root, "run-2", paths, oracle, sources)
    require(first["output_sha256"] == second["output_sha256"],
            "Repeated CLI runs did not produce byte-identical data, report and HTML.")
    require(implementation_hashes == {path.name: sha256(path) for path in implementation_paths},
            "Implementation changed during this verification; rerun a stable version.")
    return {
        "fixture": {
            "synthetic": True,
            "customer_data": False,
            "customer_case_study": False,
            "description": "Original deterministic synthetic acceptance fixture, not a client project or benchmark claim.",
            "input_records": 5000,
            "base_unique_ids": 4000,
            "exact_duplicate_rows_after_explicit_trim": 600,
            "additional_conflicting_amount_versions": 150,
            "missing_id_rows": 250,
            "features": ["three_column_orders", "comma_semicolon_tab_sources", "six_digit_leading_zero_ids",
                         "chinese_text", "quoted_commas", "embedded_newlines", "double_quotes",
                         "two_three_and_four_decimal_places_preserved"],
            "sources": sources,
        },
        "implementation_sha256": implementation_hashes,
        "expected_counts": EXPECTED_COUNTS,
        "runs": [first, second],
        "checks_passed": ["two_real_cli_runs_against_independent_fixture_oracle",
                          "byte_identical_csv_json_and_html_outputs_on_rerun",
                          "stable_implementation_during_both_runs"],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", help="Retain evidence in a NEW directory; an existing path is never overwritten.")
    args = parser.parse_args(argv)
    result = {
        "format_version": 1,
        "check": "csv_delivery_5000_synthetic_acceptance",
        "started_at_utc": utc_now(),
        "success": False,
        "environment": {"python": platform.python_version(), "implementation": platform.python_implementation(),
                        "platform": platform.system()},
        "evidence_retained": bool(args.output),
    }
    evidence_root = None
    try:
        if args.output:
            evidence_root = Path(args.output).expanduser().resolve()
            require(not evidence_root.exists(), "Evidence output already exists; choose a new directory.")
            evidence_root.mkdir(parents=True, exist_ok=False)
            result["evidence_directory"] = evidence_root.name
            result.update(verify_in(evidence_root))
        else:
            with tempfile.TemporaryDirectory(prefix="csv-batch-check-") as temporary:
                owned_root = Path(temporary).resolve(strict=True)
                require(owned_root.is_dir() and owned_root.name.startswith("csv-batch-check-"),
                        "Temporary evidence directory could not be verified.")
                result.update(verify_in(owned_root))
        result["success"] = True
    except (VerificationError, OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
        result["error"] = {"type": type(exc).__name__, "message": str(exc)}
    result["finished_at_utc"] = utc_now()
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    # Only our newly created evidence directory may receive this summary.
    if evidence_root is not None and "evidence_directory" in result:
        try:
            (evidence_root / "result.json").write_text(rendered, encoding="utf-8")
        except OSError as exc:
            result["success"] = False
            result["summary_write_error"] = str(exc)
            rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    print(rendered, end="")
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
