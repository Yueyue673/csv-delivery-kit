# CSV Delivery

**Two exports. One organised file. A clear list of decisions.**

Merge CSV exports without losing track of duplicates or conflicting identifiers. Get the output you can use, the rows that need your decision, and a script you can run again.

## See a real processing result

The included **synthetic demo** starts with two files whose columns and separators differ:

| In | Out |
| --- | --- |
| 9 input records across 2 files | 5 clean records + 3 records for review + 1 exact duplicate |
| Order `00003` appears with `8.00` and `9.00` | Both versions stay in the review file, with the source file and line |
| IDs such as `00001`, amounts such as `19.90` | Text preserved in the CSV; import identifier columns as Text in Excel |

Download the project, then open [the interactive report](demo/delivery/preview.html) locally. Switch between clean output, review records and duplicates; choose English or Chinese. All four delivery files are linked from the report. No external scripts, fonts or network access are needed.

**For a small paid adaptation:** the proposed US$25 starter covers up to 3 CSVs, 5,000 records and 20 columns under one agreed rule set. It includes the result files, rerunnable script, operating notes and one correction against the agreed scope. A redacted sample is enough to confirm whether the job fits and agree timing. This is a service proposal; the tool itself is MIT licensed.

## What the tool does

A Python CLI for merging CSV exports while keeping conflicting records visible for review. Values stay as text, including leading-zero identifiers and decimal formatting.

- Aligns matching columns even when their order differs; detects comma, semicolon, tab and pipe delimiters.
- Trimming and exact-row deduplication are opt-in.
- With `--key`, every record sharing a conflicting identifier goes to the exceptions file. A missing key is also an exception.
- Produces clean data, duplicate and exception audits, input hashes, record counts and an HTML preview.
- Processes files locally using Python's standard library.

## Run

Python 3.10 or later; no third-party packages required.

```sh
python csv_delivery.py demo/inputs/batch-a.csv demo/inputs/batch-b.csv --output delivery --key order_id --trim --dedupe-exact
```

The output directory must be new. Existing directories are rejected, and input files are left unchanged. Use `--encoding gb18030` for GB18030 input. The default limit is 100,000 nonblank records; `--max-rows` changes it.

## Synthetic example

The included example has **9 input records = 5 clean records + 1 removed exact duplicate + 3 exceptions**. Both conflicting versions of an order are retained in the exceptions audit.

See [the count report](demo/delivery/report.json), [exception rows](demo/delivery/exceptions.csv), and [HTML preview source](demo/delivery/preview.html). All example names, identifiers and amounts are synthetic. Keep `preview_template.html` beside `csv_delivery.py` when running the tool; generated reports are self-contained.

```sh
python -m unittest -v
python build_demo.py
```

The 14 regression tests cover parsing, reordered columns, text preservation, duplicate and conflict rules, malformed input, row limits and output protection. Local validation used Windows and Python 3.12.14; see [VALIDATION.txt](VALIDATION.txt).

## Boundaries

The tool does not infer the correct business value or choose a winner among conflicting records. Different column sets are rejected; column mapping requires an explicit decision first.

CSV files do not carry column types. When importing into Excel, set identifier columns to **Text** to preserve leading zeroes; Excel may otherwise convert them automatically. Import untrusted fields as text as well. This tool does not evaluate spreadsheet formulas.

MIT licensed. [中文说明](README.txt).
