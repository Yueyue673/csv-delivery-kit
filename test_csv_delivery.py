import csv
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from csv_delivery import DeliveryError, deliver


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.out = self.root / "delivery"

    def source(self, name, text, encoding="utf-8-sig"):
        path = self.root / name
        path.write_text(text, encoding=encoding, newline="")
        return path

    def read(self, name):
        with (self.out / name).open(encoding="utf-8-sig", newline="") as stream:
            return list(csv.DictReader(stream))

    def test_merges_reordered_headers_and_preserves_text(self):
        one = self.source("one.csv", "id,name,amount\n0001,张三,19.90\n")
        two = self.source("two.csv", "amount;name;id\n10.00;Alice;0002\n")
        before = [hashlib.sha256(path.read_bytes()).hexdigest() for path in (one, two)]
        deliver([one, two], self.out)
        self.assertEqual(self.read("clean.csv"), [
            {"id": "0001", "name": "张三", "amount": "19.90"},
            {"id": "0002", "name": "Alice", "amount": "10.00"},
        ])
        self.assertEqual(before, [hashlib.sha256(path.read_bytes()).hexdigest() for path in (one, two)])

    def test_duplicate_and_conflict_accounting_does_not_pick_a_winner(self):
        path = self.source("input.csv", "id,value\n001,A\n001,A\n002,B\n002,C\n,missing\n003,D\n")
        report = deliver([path], self.out, key="id", dedupe_exact=True)
        self.assertEqual(report["counts"], {"input_records": 6, "clean_records": 2,
            "exact_duplicates_removed": 1, "exception_records": 3, "conflicting_keys": 1, "missing_key_records": 1})
        self.assertEqual([row["id"] for row in self.read("clean.csv")], ["001", "003"])
        self.assertEqual([row["data.value"] for row in self.read("exceptions.csv")], ["B", "C", "missing"])
        self.assertEqual(self.read("duplicates.csv")[0]["source_line"], "3")

    def test_trimming_and_deduplication_are_opt_in(self):
        path = self.source("input.csv", "id,name\n001, Alice \n001, Alice \n")
        report = deliver([path], self.out)
        self.assertEqual(report["counts"]["clean_records"], 2)
        self.assertEqual(self.read("clean.csv")[0]["name"], " Alice ")

    def test_explicit_trim_handles_headers_before_key_resolution(self):
        path = self.source("input.csv", " id , name \n 001 , Alice \n001,Alice\n")
        report = deliver([path], self.out, key="id", trim=True, dedupe_exact=True)
        self.assertEqual(report["counts"]["clean_records"], 1)
        self.assertEqual(self.read("clean.csv")[0], {"id": "001", "name": "Alice"})

    def test_delimiter_detection_does_not_silently_trim_spaces(self):
        path = self.source("input.csv", "id, name\n001, Alice\n002, Bob\n")
        deliver([path], self.out)
        self.assertEqual(self.read("clean.csv")[0], {"id": "001", " name": " Alice"})

    def test_malformed_semicolon_rows_are_not_treated_as_a_single_column(self):
        path = self.source("input.csv", "id;name\n001;Alice;extra\n002;Bob\n")
        with self.assertRaises(DeliveryError):
            deliver([path], self.out)
        self.assertFalse(self.out.exists())

    def test_same_filename_in_different_directories_cannot_ambiguate_audit(self):
        one = self.source("input.csv", "id,value\n1,A\n")
        other = self.root / "other"
        other.mkdir()
        two = other / "input.csv"
        two.write_bytes(one.read_bytes())
        with self.assertRaises(DeliveryError):
            deliver([one, two], self.out)
        self.assertFalse(self.out.exists())

    def test_quoted_commas_and_multiline_cells_survive(self):
        path = self.source("input.csv", 'id,note\n01,"a,b"\n02,"two\nlines"\n')
        deliver([path], self.out)
        self.assertEqual([row["note"] for row in self.read("clean.csv")], ["a,b", "two\nlines"])

    def test_input_errors_do_not_create_output(self):
        cases = ["id,id\n1,2\n", "id,value\n1,2,3\n", 'id,value\n1,"unclosed\n', ""]
        for index, content in enumerate(cases):
            with self.subTest(index=index):
                path = self.source(f"bad{index}.csv", content)
                with self.assertRaises(DeliveryError):
                    deliver([path], self.out)
                self.assertFalse(self.out.exists())

    def test_mismatched_columns_and_unknown_key_fail_without_output(self):
        one = self.source("one.csv", "id,value\n1,A\n")
        two = self.source("two.csv", "id,other\n2,B\n")
        with self.assertRaises(DeliveryError):
            deliver([one, two], self.out)
        with self.assertRaises(DeliveryError):
            deliver([one], self.out, key="unknown")
        self.assertFalse(self.out.exists())

    def test_existing_output_and_repeated_input_are_rejected(self):
        path = self.source("input.csv", "id,value\n1,A\n")
        with self.assertRaises(DeliveryError):
            deliver([path, path], self.out)
        self.out.mkdir()
        marker = self.out / "keep.txt"
        marker.write_text("keep", encoding="utf-8")
        with self.assertRaises(DeliveryError):
            deliver([path], self.out)
        self.assertEqual(marker.read_text(), "keep")

    def test_configured_encoding_and_row_limit(self):
        path = self.source("gb.csv", "id,name\n001,中文\n002,测试\n", encoding="gb18030")
        with self.assertRaises(DeliveryError):
            deliver([path], self.out)
        with self.assertRaises(DeliveryError):
            deliver([path], self.out, encoding="gb18030", max_rows=1)
        self.assertFalse(self.out.exists())
        deliver([path], self.out, encoding="gb18030")
        self.assertEqual(self.read("clean.csv")[0]["name"], "中文")

    def test_preview_displays_cells_as_text(self):
        path = self.source("input.csv", 'id,note\n01,"<b>A&B</b>"\n')
        deliver([path], self.out)
        preview = (self.out / "preview.html").read_text(encoding="utf-8")
        self.assertIn("&lt;b&gt;A&amp;B&lt;/b&gt;", preview)
        self.assertNotIn("<b>A&B</b>", preview)

    def test_report_failure_removes_partial_outputs_and_allows_retry(self):
        path = self.source("input.csv", "id,value\n001,A\n002,B\n")
        before = path.read_bytes()
        with mock.patch("csv_delivery.write_preview", side_effect=OSError("interrupted report write")):
            with self.assertRaises(OSError):
                deliver([path], self.out)
        self.assertFalse(self.out.exists())
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(deliver([path], self.out)["counts"]["clean_records"], 2)

    def test_interrupted_csv_write_rolls_back_the_partial_file(self):
        path = self.source("input.csv", "id,value\n001,A\n002,B\n")
        original_writer = csv.writer

        def interrupted_writer(*args, **kwargs):
            writer = original_writer(*args, **kwargs)

            class InterruptedWriter:
                def writerow(self, row):
                    return writer.writerow(row)

                def writerows(self, rows):
                    writer.writerow(next(iter(rows)))
                    raise OSError("interrupted after writing a real record")

            return InterruptedWriter()

        with mock.patch("csv_delivery.csv.writer", side_effect=interrupted_writer):
            with self.assertRaises(OSError):
                deliver([path], self.out)
        self.assertFalse(self.out.exists())
        self.assertEqual(deliver([path], self.out)["counts"]["clean_records"], 2)

    def test_rollback_preserves_unrelated_files(self):
        path = self.source("input.csv", "id,value\n001,A\n")

        def interrupted_report(*args, **kwargs):
            (self.out / "external-note.txt").write_text("keep", encoding="utf-8")
            raise OSError("report interrupted")

        with mock.patch("csv_delivery.write_preview", side_effect=interrupted_report):
            with self.assertRaisesRegex(DeliveryError, "cleanup was incomplete"):
                deliver([path], self.out)
        self.assertEqual(list(self.out.iterdir()), [self.out / "external-note.txt"])
        self.assertEqual((self.out / "external-note.txt").read_text(), "keep")

    def test_real_cli_reports_counts_and_error_exit(self):
        path = self.source("input.csv", "id,value\n0001,A\n0001,A\n")
        script = Path(__file__).with_name("csv_delivery.py")
        args = [sys.executable, str(script), str(path), "--output", str(self.out), "--dedupe-exact"]
        success = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(success.returncode, 0, success.stderr)
        self.assertEqual(json.loads(success.stdout)["clean_records"], 1)
        repeated = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(repeated.returncode, 2)
        self.assertIn("already exists", repeated.stderr)


if __name__ == "__main__":
    unittest.main()
