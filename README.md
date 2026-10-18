# CSV Merge and Conflict Review

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

See [the count report](demo/delivery/report.json), [exception rows](demo/delivery/exceptions.csv), and [HTML preview source](demo/delivery/preview.html). Download the project and open the HTML locally to view it. All example names, identifiers and amounts are synthetic.

```sh
python -m unittest -v
python build_demo.py
```

The 14 regression tests cover parsing, reordered columns, text preservation, duplicate and conflict rules, malformed input, row limits and output protection. Local validation used Windows and Python 3.12.14; see [VALIDATION.txt](VALIDATION.txt).

## Boundaries

The tool does not infer the correct business value or choose a winner among conflicting records. Different column sets are rejected; column mapping requires an explicit decision first.

CSV files do not carry column types. When importing into Excel, set identifier columns to **Text** to preserve leading zeroes; Excel may otherwise convert them automatically. Import untrusted fields as text as well. This tool does not evaluate spreadsheet formulas.

MIT licensed. [中文说明](README.txt).
