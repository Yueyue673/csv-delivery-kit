"""Local CSV merging with explicit rules and a traceable exceptions file."""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import io
import json
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Record:
    source: str
    line: int
    values: tuple[str, ...]


class DeliveryError(ValueError):
    pass


def read_source(path: Path, encoding: str, trim: bool, expected: list[str] | None):
    raw = path.read_bytes()
    try:
        text = raw.decode(encoding)
    except (UnicodeError, LookupError) as exc:
        raise DeliveryError(f"Cannot decode {path.name} with {encoding}; specify --encoding.") from exc
    if not text.strip():
        raise DeliveryError(f"Empty input: {path.name}")
    try:
        delimiter = csv.Sniffer().sniff(text[:65536], delimiters=",;\t|").delimiter
    except csv.Error:
        candidates = []
        for separator in (",", ";", "\t", "|"):
            try:
                header = next(csv.reader(io.StringIO(text, newline=""), delimiter=separator, strict=True))
            except (csv.Error, StopIteration):
                continue
            if len(header) > 1:
                candidates.append(separator)
        if len(candidates) > 1:
            raise DeliveryError(f"Cannot determine a unique separator for {path.name}; confirm its format.")
        delimiter = candidates[0] if candidates else ","
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True, skipinitialspace=False)
    try:
        headers = next(reader)
        if trim:
            headers = [cell.strip() for cell in headers]
        if not headers or any(not cell for cell in headers) or len(set(headers)) != len(headers):
            raise DeliveryError(f"Input needs unique, nonempty column names: {path.name}")
        if expected is not None and set(headers) != set(expected):
            raise DeliveryError(f"Column sets differ in {path.name}; agree a column mapping first.")
        ordered_headers = expected or headers
        positions = [headers.index(column) for column in ordered_headers]
        records, blank_rows, changed_rows = [], 0, 0
        previous_line = reader.line_num
        for row in reader:
            line = previous_line + 1
            previous_line = reader.line_num
            if not row or all(not value.strip() for value in row):
                blank_rows += 1
                continue
            if len(row) != len(headers):
                raise DeliveryError(f"Column count mismatch: {path.name}, line {line}.")
            if trim:
                cleaned = [cell.strip() for cell in row]
                changed_rows += cleaned != row
                row = cleaned
            records.append(Record(path.name, line, tuple(row[position] for position in positions)))
    except csv.Error as exc:
        raise DeliveryError(f"Invalid CSV: {path.name}, near line {reader.line_num}.") from exc
    return ordered_headers, records, {
        "file": path.name,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "delimiter": delimiter,
        "records": len(records),
        "blank_records_skipped": blank_rows,
        "whitespace_changed_records": changed_rows,
    }


@contextmanager
def new_output(path: Path, created_paths=None, *, encoding="utf-8", newline=None):
    # Track only files actually created by this run. Never remove an unrelated file on rollback.
    with path.open("x", encoding=encoding, newline=newline) as stream:
        if created_paths is not None:
            created_paths.append(path)
        yield stream


def write_csv(path: Path, headers, rows, *, created_paths=None):
    with new_output(path, created_paths, encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(headers)
        writer.writerows(rows)


def write_preview(path: Path, report: dict, headers: list[str], rows: list[Record],
                  exceptions=(), duplicates=(), *, created_paths=None):
    def escape(value):
        return html.escape(str(value))

    def bilingual(english, chinese):
        return f'<span class="en">{escape(english)}</span><span class="zh">{escape(chinese)}</span>'

    def table(records, *, audit=False):
        columns = ([bilingual("Reason", "原因")] if audit else [])
        columns += [escape(column) for column in headers]
        columns += [bilingual("Source · line", "来源 · 行号")]
        head = "".join(f'<th scope="col">{column}</th>' for column in columns)
        body = []
        labels = {"conflicting_key": ("Conflicting ID", "编号冲突"),
                  "missing_key": ("Missing ID", "编号缺失"),
                  "exact_duplicate": ("Exact duplicate", "完全重复")}
        for entry in records[:20]:
            record, reason = entry if audit else (entry, None)
            cells = []
            if reason:
                label = bilingual(*labels.get(reason, (reason, reason)))
                style = "reason duplicate" if reason == "exact_duplicate" else "reason"
                cells.append(f'<td><span class="{style}">{label}</span></td>')
            cells.extend(f'<td class="cell-value">{escape(cell)}</td>' for cell in record.values)
            cells.append(f'<td class="source-ref">{escape(record.source)} · {record.line}</td>')
            body.append("<tr>" + "".join(cells) + "</tr>")
        if not body:
            body.append(f'<tr><td class="empty" colspan="{len(columns)}">'
                        + bilingual("No records in this view.", "此分类暂无记录。") + "</td></tr>")
        return f'<div class="scroll" tabindex="0"><table><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'

    counts, rules = report["counts"], report["rules"]
    on_off = lambda enabled: bilingual("Enabled", "启用") if enabled else bilingual("Off", "关闭")
    rule_items = [
        (bilingual("KEY COLUMN", "编号列"), escape(rules["key"]) if rules["key"] is not None else bilingual("Not selected", "未指定")),
        (bilingual("OUTER WHITESPACE TRIM", "首尾空格整理"), on_off(rules["trim_outer_whitespace"])),
        (bilingual("EXACT ROW DEDUPLICATION", "整行完全去重"), on_off(rules["dedupe_exact_rows"])),
        (bilingual("CONFLICT POLICY", "冲突处理"), bilingual("Keep all conflicting versions for review", "全部冲突版本单独保留，等待确认")),
    ]
    sources = "".join(
        '<div class="hash-source"><strong>' + escape(source["file"]) + '</strong> · '
        + str(source["records"]) + ' ' + bilingual("records", "条记录")
        + '<code>SHA-256 ' + escape(source["sha256"]) + '</code></div>' for source in report["sources"])
    demo_label = ('<span class="demo-label">' + bilingual("Synthetic demo", "合成数据演示") + '</span> · '
                  if report.get("synthetic_demo") else "")
    replacements = {
        "INPUT": str(counts["input_records"]), "CLEAN": str(counts["clean_records"]),
        "EXCEPTIONS": str(counts["exception_records"]), "DUPLICATES": str(counts["exact_duplicates_removed"]),
        "SOURCE_COUNT": str(len(report["sources"])), "DEMO_LABEL": demo_label,
        "CLEAN_TABLE": table(rows), "REVIEW_TABLE": table(exceptions, audit=True),
        "DUPLICATE_TABLE": table([(record, "exact_duplicate") for record in duplicates], audit=True),
        "RULES": "".join(f'<div><strong>{label}</strong>{value}</div>' for label, value in rule_items),
        "SOURCES": sources,
        "TRACK": "".join(f'<span style="flex:{count};background:{colour}"></span>' for count, colour in
                         [(counts["clean_records"], "#85c9b1"), (counts["exception_records"], "#e9bf70"),
                          (counts["exact_duplicates_removed"], "#9cb3c9")] if count),
    }
    # Substitute only template tokens; file contents must never become markup or template instructions.
    import re
    template = Path(__file__).with_name("preview_template.html").read_text(encoding="utf-8")
    rendered = re.sub(r"@@([A-Z_]+)@@", lambda match: replacements[match.group(1)], template)
    with new_output(path, created_paths, newline="\n") as stream:
        stream.write(rendered)


def deliver(inputs, output, *, key=None, trim=False, dedupe_exact=False, encoding="utf-8-sig", max_rows=100000,
            synthetic_demo=False):
    paths = [Path(item).resolve() for item in inputs]
    destination = Path(output).resolve()
    if not paths or max_rows < 1:
        raise DeliveryError("Provide inputs and a positive row limit.")
    if len(set(paths)) != len(paths):
        raise DeliveryError("The same input file was supplied more than once.")
    if len({path.name.casefold() for path in paths}) != len(paths):
        raise DeliveryError("Input filenames must be unique so the audit identifies each source.")
    if destination.exists():
        raise DeliveryError("Output already exists; choose a new directory. Existing files are not overwritten.")
    headers, all_records, sources = None, [], []
    for path in paths:
        headers, records, source = read_source(path, encoding, trim, headers)
        all_records.extend(records)
        sources.append(source)
        if len(all_records) > max_rows:
            raise DeliveryError("Row limit exceeded; no output has been written.")
    if key is not None and key not in headers:
        raise DeliveryError("The requested key column does not exist.")

    records, duplicates, seen = [], [], set()
    for record in all_records:
        if dedupe_exact and record.values in seen:
            duplicates.append(record)
        else:
            records.append(record)
            seen.add(record.values)

    exceptions, clean, conflicting_keys = [], [], set()
    if key is not None:
        key_index = headers.index(key)
        payloads = defaultdict(set)
        for record in records:
            if record.values[key_index].strip():
                payloads[record.values[key_index]].add(record.values)
        conflicting_keys = {value for value, payload in payloads.items() if len(payload) > 1}
        for record in records:
            value = record.values[key_index]
            if not value.strip():
                exceptions.append((record, "missing_key"))
            elif value in conflicting_keys:
                exceptions.append((record, "conflicting_key"))
            else:
                clean.append(record)
    else:
        clean = records

    report = {
        "format_version": 1,
        "rules": {"key": key, "trim_outer_whitespace": trim, "dedupe_exact_rows": dedupe_exact,
                  "encoding": encoding, "values_kept_as_text": True, "conflict_policy": "quarantine_all"},
        "counts": {"input_records": len(all_records), "clean_records": len(clean),
                   "exact_duplicates_removed": len(duplicates), "exception_records": len(exceptions),
                   "conflicting_keys": len(conflicting_keys),
                   "missing_key_records": sum(reason == "missing_key" for _, reason in exceptions)},
        "sources": sources,
        "reconciliation": "input_records = clean_records + exact_duplicates_removed + exception_records",
        "limitations": ["No business-value inference or correction",
                        "CSV viewers may infer numbers; import identifier columns as text"],
    }
    if synthetic_demo:
        report["synthetic_demo"] = True
    if len(all_records) != len(clean) + len(duplicates) + len(exceptions):
        raise DeliveryError("Internal record reconciliation failed; no output written.")
    destination.mkdir(parents=True, exist_ok=False)
    created_paths = []
    try:
        write_csv(destination / "clean.csv", headers, (record.values for record in clean), created_paths=created_paths)
        audit_headers = ["source_file", "source_line", "reason", *["data." + column for column in headers]]
        write_csv(destination / "exceptions.csv", audit_headers,
                  ((record.source, record.line, reason, *record.values) for record, reason in exceptions),
                  created_paths=created_paths)
        write_csv(destination / "duplicates.csv", audit_headers,
                  ((record.source, record.line, "exact_duplicate", *record.values) for record in duplicates),
                  created_paths=created_paths)
        with new_output(destination / "report.json", created_paths) as stream:
            stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        write_preview(destination / "preview.html", report, headers, clean, exceptions, duplicates,
                      created_paths=created_paths)
    except BaseException as error:
        cleanup_failures = []
        for path in reversed(created_paths):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                cleanup_failures.append(path.name)
        try:
            destination.rmdir()
        except OSError:
            cleanup_failures.append("output directory")
        if cleanup_failures:
            raise DeliveryError(
                f"Delivery failed and cleanup was incomplete at {destination}; "
                "do not treat the remaining directory as a completed delivery. "
                "Unrelated files were not removed."
            ) from error
        raise
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+")
    parser.add_argument("--output", required=True)
    parser.add_argument("--key")
    parser.add_argument("--trim", action="store_true", help="Explicitly trim outer whitespace in headers and values.")
    parser.add_argument("--dedupe-exact", action="store_true", help="Remove only identical complete rows, after optional trimming.")
    parser.add_argument("--encoding", default="utf-8-sig")
    parser.add_argument("--max-rows", type=int, default=100000)
    args = parser.parse_args(argv)
    try:
        report = deliver(args.inputs, args.output, key=args.key, trim=args.trim,
                         dedupe_exact=args.dedupe_exact, encoding=args.encoding, max_rows=args.max_rows)
    except (DeliveryError, OSError) as exc:
        parser.exit(2, f"Cannot prepare delivery: {exc}\n")
    print(json.dumps(report["counts"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
