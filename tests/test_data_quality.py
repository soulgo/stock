import contextlib
import io
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import brief
import etf_flow
import mx_snapshot
import report_gen
import evening_gen


class SnapshotTests(unittest.TestCase):
    def test_nonfinite_signal_price_is_not_backfilled(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "signals.md"
            rows = ["| 2026-01-02 | K04 | 1 | A | inf | |"]
            rows.extend(f"| {(date(2026, 1, 5) + timedelta(days=offset)).isoformat()} | K04 | 1 | A | 110 | |"
                        for offset in range(20))
            path.write_text("# Signals\n\n## 台账\n\n" + brief._SIG_COLS + "\n".join(rows), encoding="utf-8")
            with patch.object(brief, "SIGNALS_MD", str(path)), contextlib.redirect_stdout(io.StringIO()):
                brief.signals_backfill()
            self.assertIn("| 2026-01-02 | K04 | 1 | A | inf |  |", path.read_text(encoding="utf-8"))

    def test_rewriting_old_source_does_not_make_quote_current(self):
        payload = {"date": date.today().isoformat(), "written": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                   "quotes": {"512170": {"time": "2020-01-02", "price": 1}}}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "mx.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with patch.object(brief, "_mx_path", return_value=str(path)):
                brief.load_mx()
            self.assertIsNone(brief.quote_mx("512170"))

    def test_future_written_timestamp_is_not_accepted(self):
        payload = {"written": (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "mx.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with patch.object(brief, "_mx_path", return_value=str(path)):
                brief.load_mx()
            self.assertIsNone(brief.MX["data"])

    def test_missing_flow_stays_unknown_and_preserves_source_date(self):
        with tempfile.TemporaryDirectory() as folder:
            quote = Path(folder) / "quote.json"
            flow = Path(folder) / "flow.json"
            out = Path(folder) / "mx.json"
            quote.write_text('{"data": {}}', encoding="utf-8")
            flow.write_text(json.dumps({"data": {"sh512170": {"data": [
                {"EndDate": "2026-11-18", "MainNetFlow": "0"}]}}}), encoding="utf-8")
            with patch("sys.argv", ["mx_snapshot.py", "--quotes", str(quote),
                                    "--flows", str(flow), "--out", str(out)]), contextlib.redirect_stdout(io.StringIO()):
                mx_snapshot.main()
            record = json.loads(out.read_text(encoding="utf-8"))["flows"]["512170"]
            self.assertIsNone(record["huge"])
            self.assertEqual(record["main"], 0)
            self.assertEqual(record["date"], "2026-11-18")
            self.assertNotEqual(record["time"], datetime.now().strftime("%H:%M"))

    def test_fwd20_uses_twentieth_future_observation_across_gaps(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "signals.md"
            rows = ["| 2026-01-02 | K04 | 1 | A | 100 | |"]
            future = [date(2026, 1, 5) + timedelta(days=offset) for offset in range(22)]
            rows.extend(f"| {day.isoformat()} | K04 | 1 | A | 110 | |" for day in future)
            path.write_text("# Signals\n\n## 台账\n\n" + brief._SIG_COLS + "\n".join(rows), encoding="utf-8")
            with patch.object(brief, "SIGNALS_MD", str(path)):
                brief.signals_backfill()
            updated = path.read_text(encoding="utf-8")
            self.assertIn("| 2026-01-02 | K04 | 1 | A | 100 | +10.00% |", updated)
            self.assertIn("| 2026-01-05 | K04 | 1 | A | 110 | +0.00% |", updated)
            self.assertIn(f"| {future[-1].isoformat()} | K04 | 1 | A | 110 |  |", updated)

    def test_flow_without_source_date_does_not_get_local_time(self):
        with tempfile.TemporaryDirectory() as folder:
            quote, flow, out = [Path(folder) / name for name in ("quote.json", "flow.json", "out.json")]
            quote.write_text('{"data": {}}', encoding="utf-8")
            flow.write_text('{"data": {"sh563800": {"data": [{"MainNetFlow": 0}]}}}', encoding="utf-8")
            with patch("sys.argv", ["mx_snapshot.py", "--quotes", str(quote), "--flows", str(flow),
                                    "--out", str(out)]), contextlib.redirect_stdout(io.StringIO()):
                mx_snapshot.main()
            self.assertIsNone(json.loads(out.read_text(encoding="utf-8"))["flows"]["563800"]["time"])


class DecisionTests(unittest.TestCase):
    def test_null_action_field_is_rejected(self):
        decision = {"actions": [{"name": "A500", "code": "563800", "side": "buy", "direction": "正T",
                                 "trigger": "1.0", "shares": "100", "target": "1.1", "invalid": None, "why": "测试"}]}
        with self.assertRaisesRegex(ValueError, "关键字段"):
            report_gen.validate_decision("morning", decision, {"563800": {}})

    def test_morning_list_levels_accept_matching_holding_and_reject_missing_data(self):
        holdings = {"563800": {"code": "563800", "name": "A500龙头"}}
        decision = {"actions": [{"name": "A500龙头", "code": "563800", "side": "buy",
                    "direction": "正T", "trigger": "1.000", "shares": "100 份",
                    "target": "1.010", "invalid": "0.990", "why": "测试"}]}
        panel = {"levels": [{"name": "A500龙头", "state": "震荡区间", "sup": "0.990", "res": "1.010"}]}
        report_gen.validate_decision("morning", decision, holdings, panel)
        with self.assertRaisesRegex(ValueError, "资金流"):
            report_gen.validate_decision("noon", decision, holdings,
                                         {"levels": {"A500龙头": panel["levels"][0]}, "flows": []})

    def test_tstats_reports_only_confirmed_t_profit(self):
        ledger = {"items": [{"code": "563800", "kind": "exit", "confirmed": True,
                             "realized": 34, "rounds": 0}]}
        with patch.object(brief, "tledger", return_value=ledger), contextlib.redirect_stdout(io.StringIO()) as output:
            brief.tstats_block("一", {"holdings": []})
        self.assertIn("已实现收益      +0.00", output.getvalue())
        self.assertIn("退出 +34.00", output.getvalue())

    def test_decisions_validate_market_actions_and_evening_has_no_actions(self):
        portfolio = json.loads(Path("portfolio.json").read_text(encoding="utf-8"))
        holdings = {item["code"]: item for item in portfolio["holdings"]}
        valid = {"verdict": "观察", "actions": [{"name": "A500", "code": "563800",
                 "side": "buy", "direction": "正T", "trigger": "1.000", "shares": "100",
                 "target": "1.010", "invalid": "0.990", "why": "测试"}]}
        with self.assertRaisesRegex(ValueError, "持仓标的"):
            report_gen.validate_decision("noon", {**valid, "actions": [{**valid["actions"][0], "code": "000001"}]}, holdings)
        with self.assertRaisesRegex(ValueError, "side"):
            report_gen.validate_decision("morning", {**valid, "actions": [{**valid["actions"][0], "side": "hold"}]}, holdings)
        with self.assertRaisesRegex(ValueError, "做T动作"):
            evening_gen.validate_decision({**valid, "actions": valid["actions"]})
        report_gen.validate_decision("noon", valid, holdings)

    def test_realized_trade_totals_distinguish_t_and_exit(self):
        ledger = {"items": [
            {"kind": "t", "confirmed": True, "rounds": 1, "realized": 12},
            {"kind": "rebalance", "confirmed": True, "realized": 21},
            {"kind": "exit", "confirmed": True, "realized": 34},
            {"kind": "exit", "confirmed": False, "realized": 99},
            {"kind": "t", "confirmed": True, "rounds": 0, "realized": 50},
            {"realized": 206.7, "rounds": 0}]}
        totals = brief.realized_trade_totals(ledger)
        self.assertEqual(totals["t"], 12)
        self.assertEqual(totals["rebalance"], 21)
        self.assertEqual(totals["exit"], 34)
        self.assertEqual(totals["unconfirmed"], 3)

    def test_action_requires_valid_panel_state_and_complete_inputs(self):
        holdings = {"563800": {"code": "563800"}}
        decision = {"verdict": "观察", "actions": [{"name": "A500", "code": "563800",
                    "side": "buy", "direction": "正T", "trigger": "1.000", "shares": "100",
                    "target": "1.010", "invalid": "0.990", "why": "测试"}]}
        with self.assertRaisesRegex(ValueError, "状态"):
            report_gen.validate_decision("noon", decision, holdings, {"levels": []})
        with self.assertRaisesRegex(ValueError, "关键字段"):
            report_gen.validate_decision("noon", {**decision, "actions": [
                {**decision["actions"][0], "invalid": ""}]}, holdings)


class AggregationTests(unittest.TestCase):
    def test_missing_market_source_dates_cannot_form_total(self):
        flows = {code: {key: 1 for key in ("main", "huge", "big", "mid", "small")}
                 for code in ("sh000001", "sz399001")}
        with patch.dict(brief.MX, {"data": {"flows_full": flows}}):
            result = brief.market_data(mode="morning")
        self.assertIsNone(result["idx"]["main"])

    def test_html_preserves_unknown_flow_and_coverage(self):
        data = {"idx": {key: None for key in
                        ("main", "huge", "big", "mid", "small", "main5", "main10", "main20")},
                "market": None, "cut": None, "nat": None,
                "k01": {"date": "2026-11-18", "pool": 20, "total_shares": 100000000,
                        "index": 100, "observed": 1, "carried": 19}, "ok": True, "mode": "noon"}
        data["idx"].update({"amount": 0, "flow_date": "2026-11-18", "coverage": 1})
        parsed = report_gen.parse_market("\n".join(brief.market_render(data, "一")))
        rendered = report_gen.render_market(parsed)
        self.assertIn("指数覆盖 1/2", rendered)
        self.assertIn("实测 1/20", rendered)
        self.assertIn("前值顺延 19", rendered)
        self.assertIn("资金流不完整", rendered)

    def test_unknown_market_total_does_not_parse_date_as_money(self):
        data = {"idx": {"main": None, "huge": None, "big": None, "mid": None,
                       "small": None, "main5": None, "main10": None, "main20": None,
                       "amount": 0, "flow_date": "2026-11-18", "coverage": 1},
                "market": None, "cut": None, "nat": None, "k01": None,
                "ok": True, "mode": "noon"}
        parsed = report_gen.parse_market("\n".join(brief.market_render(data, "一")))
        self.assertIsNone(parsed["main"])

    def test_missing_national_flow_does_not_crash_or_become_zero(self):
        code, family = brief.NATIONAL[0]
        with patch.dict(brief.MX, {"data": {"flows_full": {code: {"main": None}},
                                         "etf": {code: {"size": 100}}}}):
            result = brief.market_data(mode="morning")
        self.assertIsNone(result["nat"]["m1"])

    def test_old_flow_is_not_usable_for_intraday_decisions(self):
        flow = {key: 0 for key in ("main", "huge", "big", "mid", "small")}
        flow["date"] = "2020-01-02"
        with patch.dict(brief.MX, {"data": {"flows": {"563800": flow}}}):
            self.assertIsNone(brief.fflow("563800"))

    def test_partial_market_coverage_is_unknown_instead_of_two_market_total(self):
        flow = {"main": 10, "huge": None, "big": 2, "mid": 3, "small": 4,
                "date": date.today().isoformat()}
        with patch.dict(brief.MX, {"data": {"flows_full": {"sh000001": flow}}}):
            result = brief.market_data(mode="morning")
        self.assertIsNone(result["idx"]["main"])
        self.assertEqual(result["idx"]["coverage"], 1)

    def test_incomplete_flow_is_not_usable_for_intraday_decisions(self):
        with patch.dict(brief.MX, {"data": {"flows": {"563800": {
                "main": 0, "huge": None, "date": date.today().isoformat()}}}}), patch.object(brief, "jget", return_value={}):
            self.assertIsNone(brief.fflow("563800"))


class K01Tests(unittest.TestCase):
    def test_summary_cannot_use_cross_date_partial_baseline(self):
        rows = [{"date": "2026-11-16" if offset else "2026-11-17",
                 "code": code, "shares": "100"} for offset, code in enumerate(etf_flow.CORE)]
        with patch.object(etf_flow, "load", return_value=rows), contextlib.redirect_stdout(io.StringIO()) as output:
            etf_flow.summary()
        self.assertNotIn("100.00", output.getvalue())

    def test_valid_complete_snapshot_writes_csv_and_index(self):
        with tempfile.TemporaryDirectory() as folder:
            snapshot, csv_path, index_path = [Path(folder) / name for name in ("mx.json", "etf.csv", "index.json")]
            snapshot.write_text(json.dumps({"date": date.today().isoformat(), "quotes": {
                code: {"time": date.today().isoformat(), "total_shares": 100, "price": 2}
                for code in etf_flow.CORE}}), encoding="utf-8")
            with patch.object(etf_flow, "CSV_PATH", str(csv_path)), patch.object(etf_flow, "INDEX_PATH", str(index_path)), patch.object(etf_flow, "fetch") as fetch, contextlib.redirect_stdout(io.StringIO()):
                etf_flow.collect(str(snapshot))
                self.assertEqual(len(etf_flow.load()), len(etf_flow.CORE))
                fetch.assert_not_called()
            index = json.loads(index_path.read_text(encoding="utf-8"))
            self.assertEqual(index["observed"], 20)
            self.assertEqual(index["carried"], 0)
            self.assertEqual(index["index"], 100)

    def test_explicit_broken_snapshot_preserves_existing_files_without_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            csv_path = Path(folder) / "etf.csv"
            index_path = Path(folder) / "index.json"
            csv_path.write_bytes(b"existing csv")
            index_path.write_bytes(b"existing index")
            with patch.object(etf_flow, "CSV_PATH", str(csv_path)), patch.object(etf_flow, "INDEX_PATH", str(index_path)), patch.object(etf_flow, "fetch") as fetch:
                with self.assertRaises((ValueError, FileNotFoundError)):
                    etf_flow.collect(str(Path(folder) / "absent.json"))
                fetch.assert_not_called()
            self.assertEqual(csv_path.read_bytes(), b"existing csv")
            self.assertEqual(index_path.read_bytes(), b"existing index")

    def test_stale_and_nonfinite_shares_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "mx.json"
            for stamp, shares in [("2020-01-02", 100), (date.today().isoformat(), "NaN")]:
                path.write_text(json.dumps({"date": date.today().isoformat(), "quotes": {
                    "510300": {"time": stamp, "total_shares": shares, "price": 3}}}), encoding="utf-8")
                with self.assertRaises(ValueError):
                    etf_flow.from_mx(str(path))

    def test_carried_shares_are_labeled_with_observed_coverage(self):
        rows = [{"date": "2026-11-16", "code": code, "shares": "100"} for code in etf_flow.CORE]
        rows.append({"date": "2026-11-17", "code": etf_flow.CORE[0], "shares": "120"})
        index = etf_flow.build_index(rows)
        self.assertEqual(index["observed"], 1)
        self.assertEqual(index["carried"], len(etf_flow.CORE) - 1)

    def test_partial_dates_cannot_together_form_a_complete_baseline(self):
        rows = [{"date": "2026-11-16" if offset else "2026-11-17",
                 "code": code, "shares": "100"} for offset, code in enumerate(etf_flow.CORE)]
        self.assertIsNone(etf_flow.build_index(rows))

    def test_collection_without_explicit_snapshot_does_not_use_bad_f84_source(self):
        with patch.object(etf_flow, "load", return_value=[]), patch.object(etf_flow, "fetch") as fetch:
            with self.assertRaisesRegex(ValueError, "--mx"):
                etf_flow.collect()
            fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
