"""Local CSV merging with explicit rules and a traceable exceptions file."""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import io
import json
from collections import defaultdict
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


def write_csv(path: Path, headers, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(headers)
        writer.writerows(rows)


def write_preview(path: Path, report: dict, headers: list[str], rows: list[Record]):
    escape = lambda value: html.escape(str(value))
    head = "".join(f"<th>{escape(column)}</th>" for column in headers)
    body = "".join("<tr>" + "".join(f"<td>{escape(cell)}</td>" for cell in row.values) + "</tr>" for row in rows[:20])
    counts = report["counts"]
    path.write_text(
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>CSV 交付检查 / Delivery check</title>'
        '<style>body{font:16px/1.7 system-ui,sans-serif;max-width:1050px;margin:40px auto;padding:0 24px;color:#173042;background:#f8fafb}'
        'h1{line-height:1.3}table{border-collapse:collapse;width:100%;background:white}th,td{border:1px solid #dbe3e9;padding:8px;text-align:left}'
        'th{background:#e8f0f4}code{background:#e8f0f4;padding:3px 6px}.note{border-left:4px solid #d29c36;padding:12px 18px;background:#fff8e8}'
        '.scroll{overflow:auto}.counts{font-size:19px;font-weight:600}</style>'
        '<h1>CSV 交付检查<br><small>Local delivery check</small></h1>'
        '<p>处理在本机完成，没有上传文件，也没有调用 AI 或其他网络服务。</p>'
        f'<p class="counts">输入 {counts["input_records"]} 条 · 可交付 {counts["clean_records"]} 条 · '
        f'完全重复 {counts["exact_duplicates_removed"]} 条 · 待确认 {counts["exception_records"]} 条</p>'
        '<p class="note">相同编号对应不同内容时，所有冲突记录均放入 <code>exceptions.csv</code>，等待确认。'
        '不会猜测应该保留哪一条。原文件保持不变。</p>'
        '<p>字段保持文本，包括编号的前导零与金额原有的小数位。用 Excel 查看 CSV 时，请通过“数据 → 从文本/CSV”导入，'
        '将编号列设为文本；直接双击 CSV 可能触发 Excel 自动转换。</p>'
        '<h2>交付数据预览（最多 20 条）</h2>'
        f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'
        '<h2>交付文件</h2><ul><li><a href="clean.csv">clean.csv</a>：整理后的数据</li>'
        '<li><a href="exceptions.csv">exceptions.csv</a>：需确认的记录及来源</li>'
        '<li><a href="duplicates.csv">duplicates.csv</a>：被移除的完全重复记录</li>'
        '<li><a href="report.json">report.json</a>：规则、计数及原文件校验值</li></ul>'
        '<p>此报告只证明所列规则执行及计数结果。它不证明业务数据本身正确，也不证明任何付款到账。</p></html>',
        encoding="utf-8",
    )


def deliver(inputs, output, *, key=None, trim=False, dedupe_exact=False, encoding="utf-8-sig", max_rows=100000):
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
        "limitations": ["No business-value inference or correction", "No payment verification",
                        "CSV viewers may infer numbers; import identifier columns as text"],
    }
    if len(all_records) != len(clean) + len(duplicates) + len(exceptions):
        raise DeliveryError("Internal record reconciliation failed; no output written.")
    destination.mkdir(parents=True, exist_ok=False)
    write_csv(destination / "clean.csv", headers, (record.values for record in clean))
    audit_headers = ["source_file", "source_line", "reason", *["data." + column for column in headers]]
    write_csv(destination / "exceptions.csv", audit_headers,
              ((record.source, record.line, reason, *record.values) for record, reason in exceptions))
    write_csv(destination / "duplicates.csv", audit_headers,
              ((record.source, record.line, "exact_duplicate", *record.values) for record in duplicates))
    (destination / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_preview(destination / "preview.html", report, headers, clean)
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
