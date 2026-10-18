"""Build a synthetic, reproducible service demonstration in a new demo directory."""

import csv
import hashlib
import json
from pathlib import Path

from csv_delivery import deliver


def main():
    root = Path(__file__).resolve().parent / "demo"
    if root.exists():
        raise SystemExit("Demo already exists; it will not be overwritten.")
    inputs = root / "inputs"
    inputs.mkdir(parents=True)
    headers = ["order_id", "customer", "amount", "currency", "note"]
    first_rows = [
        ["00001", "Alice", "19.90", "USD", "first order"],
        ["00002", "Bob", "10.00", "USD", ""],
        ["00003", "陈同学", "8.00", "USD", "needs confirmation"],
        ["00005", "Eli", "5.00", "USD", ""],
    ]
    second_headers = ["currency", "amount", "customer", "order_id", "note"]
    second_rows = [
        ["USD", "19.90", " Alice ", "00001", "first order"],
        [" USD ", "12.50", "Dana", "00004", "quoted, comma"],
        ["USD", "9.00", "陈同学", "00003", "needs confirmation"],
        ["USD", "13.00", "Missing identifier", "", "needs an ID"],
        ["USD", "0.00", "示例用户", "00006", "synthetic data"],
    ]
    sources = [inputs / "batch-a.csv", inputs / "batch-b.csv"]
    for path, columns, rows, delimiter in [
        (sources[0], headers, first_rows, ","),
        (sources[1], second_headers, second_rows, ";"),
    ]:
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream, delimiter=delimiter)
            writer.writerow(columns)
            writer.writerows(rows)
    before = [hashlib.sha256(path.read_bytes()).hexdigest() for path in sources]
    report = deliver(sources, root / "delivery", key="order_id", trim=True, dedupe_exact=True, synthetic_demo=True)
    assert report["counts"] == {"input_records": 9, "clean_records": 5, "exact_duplicates_removed": 1,
                                "exception_records": 3, "conflicting_keys": 1, "missing_key_records": 1}
    assert before == [hashlib.sha256(path.read_bytes()).hexdigest() for path in sources]
    (root / "SYNTHETIC-DATA.txt").write_text(
        "All demonstration records are synthetic. No client work or revenue is claimed.\n"
        "所有演示数据均为合成数据。不是历史客户项目，没有成交或收入。\n", encoding="utf-8")
    print(json.dumps(report["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
