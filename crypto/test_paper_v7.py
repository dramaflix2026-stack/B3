"""Offline safety tests for paper V7; zero external calls."""
import pathlib
import tempfile
import unittest

import paper_v7 as paper
from paper_v7_analytics import diagnostics as audit

MINUTE = 60000


def candle(ms, o=100., h=100.2, l=99.8, c=100.):
    return {"ms": ms, "o": o, "h": h, "l": l, "c": c}


def frozen():
    return {"source_report": "test-only.json", "sha256": "a"*64, "frozen_at_utc": "2026-11-15T00:00:00Z",
            "control_decision": "cash", "control_reason": "V7 P5 stayed cash",
            "shadow_atr": {"direction": "low", "threshold_pct": .1679}}


class PaperV7Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_data = (paper.PAPER, paper.STATE_FILE)
        paper.PAPER = pathlib.Path(self.tmp.name) / "paper"
        paper.STATE_FILE = paper.PAPER / "state.json"
        start = (1_730_000_000_000 // MINUTE) * MINUTE
        self.ready = [candle(start + i*MINUTE) for i in range(721)]
        self.state = paper.new_state(self.ready, frozen())

    def tearDown(self):
        paper.PAPER, paper.STATE_FILE = self.old_data
        self.tmp.cleanup()

    def test_warmup_is_not_backtest(self):
        self.assertEqual(self.state["processed"], 0)
        self.assertEqual(self.state["trades"], [])
        self.assertEqual(self.state["cash"], 1000.)
        self.assertEqual(self.state["frozen"]["control_decision"], "cash")
        self.assertEqual(self.state["last_ms"], self.ready[-1]["ms"])
        self.assertEqual(len(self.state["closes"]), 720)

    def test_new_completed_candle_only_and_duplicate_ignored(self):
        one = candle(self.state["last_ms"] + MINUTE)
        paper.process_candle(self.state, one)
        self.assertEqual(self.state["processed"], 1)
        self.assertEqual(self.state["trades"], [])
        self.assertEqual(self.state["cash"], 1000.)
        paper.process_candle(self.state, one)
        self.assertEqual(self.state["processed"], 1)

    def test_next_open_simulated_fill_and_same_candle_stop_with_fees(self):
        self.state["pending"] = {"atr": .2, "atr_pct": .2, "signal_ms": self.state["last_ms"]}
        nxt = candle(self.state["last_ms"] + MINUTE, o=100, h=100.2, l=98.0, c=99.0)
        paper.process_candle(self.state, nxt)
        self.assertEqual(len(self.state["trades"]), 1)
        trade = self.state["trades"][0]
        self.assertEqual(trade["duration_min"], 0)
        self.assertEqual(trade["reason"], "stop_loss")
        self.assertLess(trade["net_usdt"], 0)
        self.assertAlmostEqual(trade["gross_before_costs_usdt"]-
                               trade["commissions_usdt"]-trade["slippage_usdt"],
                               trade["net_usdt"], places=4)
        self.assertEqual(self.state["btc"], 0.)
        self.assertEqual(self.state["frozen"]["control_decision"], "cash")

    def test_gap_pauses_without_fabricated_trade(self):
        paper.process_candle(self.state, candle(self.state["last_ms"] + 2*MINUTE))
        self.assertEqual(self.state["phase"], "paused_data_gap")
        self.assertEqual(self.state["processed"], 0)
        self.assertEqual(self.state["trades"], [])

    def test_feed_staleness_and_sample_warning(self):
        result = audit(self.state, {"phase": "running"}, self.state["last_ms"]+7*MINUTE)
        self.assertEqual(result["feed"]["status"], "stale")
        self.assertFalse(result["paper"]["sufficient_sample"])
        self.assertEqual(result["paper"]["total_trades_recorded"], 0)
        self.assertIsNone(result["paper"]["profit_factor_recent"])
        self.assertTrue(result["rules"]["no_real_orders"])

    def test_closed_trade_metrics_and_legacy_counter(self):
        self.state.pop("total_trades")  # existing state created before counter release
        self.state["pending"] = {"atr": .2, "atr_pct": .2, "signal_ms": self.state["last_ms"]}
        nxt = candle(self.state["last_ms"]+MINUTE, o=100, h=100.2, l=98.0, c=99.0)
        paper.process_candle(self.state, nxt)
        result = audit(self.state, {"phase": "running"}, self.state["last_ms"]+MINUTE)
        self.assertEqual(result["feed"]["status"], "healthy")
        self.assertEqual(result["paper"]["total_trades_recorded"], 1)
        self.assertLess(result["paper"]["realized_net_usdt"], 0)
        self.assertGreater(result["paper"]["fees_usdt_all"], 0)
        self.assertGreater(result["paper"]["slippage_usdt_all"], 0)
        self.assertEqual(result["paper"]["win_rate_pct_recent"], 0)
        self.assertEqual(result["paper"]["daily_utc_recent"][0]["trades"], 1)
        self.assertEqual(self.state["frozen"]["control_decision"], "cash")

    def test_gap_is_an_alert_and_not_a_trade(self):
        paper.process_candle(self.state, candle(self.state["last_ms"]+2*MINUTE))
        result = audit(self.state, {"phase": "running"}, self.state["last_ms"]+3*MINUTE)
        self.assertEqual(result["feed"]["status"], "paused_data_gap")
        self.assertTrue(result["feed"]["alerts"])
        self.assertEqual(result["paper"]["total_trades_recorded"], 0)

    def test_mark_to_market_drawdown_in_open_position(self):
        self.state["pending"] = {"atr": .2, "atr_pct": .2, "signal_ms": self.state["last_ms"]}
        first = candle(self.state["last_ms"]+MINUTE, o=100, h=100.2, l=99.5, c=99.6)
        paper.process_candle(self.state, first)
        self.assertGreater(self.state["btc"], 0)
        self.assertGreater(self.state["max_close_drawdown_usdt"], 0)
        self.assertEqual(self.state["equity_peak_usdt"], 1000.)
        up = candle(self.state["last_ms"]+MINUTE, o=99.6, h=103, l=99.6, c=102)
        paper.process_candle(self.state, up)
        self.assertGreater(self.state["equity_peak_usdt"], 1000)
        down = candle(self.state["last_ms"]+MINUTE, o=102, h=102, l=100, c=100)
        paper.process_candle(self.state, down)
        self.assertGreater(self.state["max_close_drawdown_usdt"], 0)
        self.assertEqual(len(self.state["trades"]), 0)
        result = audit(self.state, {"phase": "running"}, self.state["last_ms"]+MINUTE)
        self.assertGreater(result["paper"]["max_closed_candle_drawdown_usdt"], 0)
        self.assertGreater(result["paper"]["max_closed_candle_drawdown_pct"], 0)
        self.assertEqual(result["paper"]["total_trades_recorded"], 0)

    def test_equity_marks_are_idempotent_and_survive_restart(self):
        t = self.state["last_ms"]+MINUTE
        paper.process_candle(self.state, candle(t))
        self.assertEqual(len(self.state["daily_close_equity_utc"]), 1)
        self.assertEqual(self.state["processed"], 1)
        paper.atomic_save(self.state)
        restored = paper._load()
        snapshot = list(restored["daily_close_equity_utc"])
        paper.process_candle(restored, candle(t))
        self.assertEqual(restored["processed"], 1)
        self.assertEqual(restored["daily_close_equity_utc"], snapshot)
        paper.process_candle(restored, candle(t+MINUTE))
        self.assertEqual(restored["processed"], 2)
        self.assertEqual(len(restored["daily_close_equity_utc"]), 1)
        self.assertEqual(restored["frozen"]["control_decision"], "cash")

    def test_legacy_state_does_not_invent_prior_drawdown(self):
        for k in ("equity_peak_usdt","equity_monitoring_started_ms",
                  "max_close_drawdown_usdt","max_close_drawdown_pct",
                  "last_close_equity_usdt","daily_close_equity_utc"):
            self.state.pop(k)
        self.state["cash"] = 900.
        old_timestamp = self.state["last_ms"]
        paper.process_candle(self.state, candle(old_timestamp+MINUTE))
        self.assertEqual(self.state["equity_monitoring_started_ms"], old_timestamp)
        self.assertEqual(self.state["equity_peak_usdt"], 900.)
        self.assertEqual(self.state["max_close_drawdown_usdt"], 0.)
        self.assertEqual(self.state["last_close_equity_usdt"], 900.)
        self.assertEqual(len(self.state["daily_close_equity_utc"]), 1)

    def test_immutable_archive_and_page_are_replay_safe(self):
        from paper_v7_journal import archive_status, page, sync_archive
        self.state["pending"] = {"atr": .2, "atr_pct": .2, "signal_ms": self.state["last_ms"]}
        nxt = candle(self.state["last_ms"]+MINUTE, o=100., h=100.2, l=98, c=99)
        paper.process_candle(self.state, nxt)
        paper.atomic_save(self.state)
        archived = archive_status(self.state, paper.PAPER)
        self.assertEqual(archived["status"], "complete")
        self.assertEqual(archived["archived"], 1)
        self.assertEqual(page(self.state, paper.PAPER)["records"][0]["sequence"], 1)
        # Replaying a save and restart must not create another ledger item.
        restored = paper._load()
        sync_archive(restored, paper.PAPER)
        paper.atomic_save(restored)
        self.assertEqual(archive_status(restored, paper.PAPER)["archived"], 1)
        self.assertEqual(paper.journal_page()["ledger"]["total"], 1)
        self.assertEqual(paper.journal_page()["control_decision"], "cash")

    def test_archived_trade_conflict_blocks_new_processing(self):
        from paper_v7_journal import sync_archive
        self.state["pending"] = {"atr": .2, "atr_pct": .2, "signal_ms": self.state["last_ms"]}
        paper.process_candle(self.state, candle(self.state["last_ms"]+MINUTE, o=100, h=100.2, l=98.0, c=99.0))
        paper.atomic_save(self.state)
        self.state["trades"][0]["net_usdt"] = 999999
        with self.assertRaises(ValueError):
            sync_archive(self.state, paper.PAPER)

    def test_archive_paging_does_not_truncate_trade_history(self):
        from paper_v7_journal import archive_status, page, sync_archive
        example = {"entry_utc":"2026-11-15T00:00:00Z", "exit_utc":"2026-11-15T00:01:00Z",
                   "net_usdt":1., "commissions_usdt":.1, "slippage_usdt":.01}
        self.state["trades"] = [dict(example, sequence_metadata=i) for i in range(25)]
        self.state["total_trades"] = 25
        sync_archive(self.state, paper.PAPER)
        self.assertEqual(archive_status(self.state,paper.PAPER)["archived"],25)
        first = page(self.state, paper.PAPER, page_num=1, page_size=20)
        second = page(self.state, paper.PAPER, page_num=2, page_size=20)
        self.assertEqual([r["sequence"] for r in first["records"]],list(range(25,5,-1)))
        self.assertEqual([r["sequence"] for r in second["records"]],list(range(5,0,-1)))
        self.assertEqual(first["status"],"complete")
        self.assertEqual(second["status"],"complete")

    def test_partial_archive_is_explicitly_not_complete(self):
        from paper_v7_journal import archive_status, page
        self.state["trades"] = [{"net_usdt": -1}]
        self.state["total_trades"] = 2
        self.assertEqual(archive_status(self.state,paper.PAPER)["missing"],2)
        result=page(self.state,paper.PAPER)
        self.assertEqual(result["status"],"partial")
        self.assertEqual(result["missing_sequences"],[2,1])

    def test_daily_stats_count_only_new_closed_candles(self):
        from paper_v7_daily import status as daily_status
        original = self.state["last_ms"]
        self.assertNotIn("daily_activity_utc", self.state)
        paper.process_candle(self.state, candle(original+MINUTE))
        paper.process_candle(self.state, candle(original+MINUTE))  # duplicate
        report = daily_status(self.state, self.state["last_ms"]+MINUTE)
        self.assertEqual(report["observed_days_in_retained_window"], 1)
        self.assertEqual(report["last_30_days_utc"][0]["candles"], 1)
        self.assertEqual(report["closed_trades_since_activation"], 0)
        self.assertEqual(report["approval"], "not_approved")
        self.assertFalse(report["minimum_research_sample_reached"])
        self.assertEqual(report["days_remaining_in_observed_window"], 29)
        paper.atomic_save(self.state)
        restored = paper._load()
        paper.process_candle(restored, candle(restored["last_ms"]+MINUTE))
        self.assertEqual(restored["daily_activity_utc"][0]["candles"], 2)

    def test_daily_same_candle_entry_exit_reconciles_costs_and_net(self):
        self.state["pending"] = {"atr": .2, "atr_pct": .2, "signal_ms": self.state["last_ms"]}
        nxt = candle(self.state["last_ms"]+MINUTE,o=100,h=100.2,l=98,c=99)
        paper.process_candle(self.state, nxt)
        day = self.state["daily_activity_utc"][-1]
        self.assertEqual(day["candles"], 1)
        self.assertEqual(day["entries"], 1)
        self.assertEqual(day["closed_trades"], 1)
        self.assertAlmostEqual(day["fees_paid_usdt"], self.state["fees"], places=4)
        self.assertAlmostEqual(day["slippage_paid_usdt"], self.state["slippage"], places=4)
        self.assertAlmostEqual(day["realized_net_on_exit_usdt"], self.state["realized"], places=4)
        self.assertEqual(self.state["frozen"]["control_decision"], "cash")

    def test_operational_alerts_stale_gap_archive_and_no_real_orders(self):
        from paper_v7_daily import alerts
        stale = alerts(self.state, {"phase":"running"}, self.state["last_ms"]+7*MINUTE,
                       {"status":"complete"})
        codes = [x["code"] for x in stale["active"]]
        self.assertIn("feed_stale", codes)
        self.assertIn("sample_small", codes)
        self.assertEqual(stale["notification_mode"], "in_dashboard_only")
        self.assertEqual(stale["critical"], 0)
        self.state["phase"] = "paused_data_gap"
        self.state["max_close_drawdown_pct"] = 7.5
        with_gap = alerts(self.state, {"phase":"running"}, self.state["last_ms"]+MINUTE,
                          {"status":"partial","missing":2})
        codes = [x["code"] for x in with_gap["active"]]
        self.assertIn("feed_gap", codes)
        self.assertIn("journal_incomplete", codes)
        self.assertIn("drawdown_high", codes)
        self.assertEqual(with_gap["critical"], 2)
        self.assertEqual(self.state["frozen"]["control_decision"], "cash")

    def test_daily_metrics_preserve_utc_date_boundary_without_backfill(self):
        from paper_v7_daily import record_candle, status as daily_status
        import datetime as dt
        start = int(dt.datetime(2026,11,15,23,59,tzinfo=dt.timezone.utc).timestamp()*1000)
        record_candle(self.state,start,signals=1,blocked=0,entries=0,exits=0,
                      fee_delta=0,slippage_delta=0,realized_delta=0)
        record_candle(self.state,start+MINUTE,signals=0,blocked=1,entries=0,exits=0,
                      fee_delta=0,slippage_delta=0,realized_delta=0)
        rows = daily_status(self.state,start+2*MINUTE)["last_30_days_utc"]
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0]["date_utc"],"2026-11-16")
        self.assertEqual(rows[1]["date_utc"],"2026-11-15")
        self.assertEqual(rows[1]["signals"],1)
        self.assertEqual(rows[0]["atr_blocked"],1)

    def _auditable_source(self):
        import hashlib
        data_dir = pathlib.Path(self.tmp.name)/"data"
        report = data_dir/"v7"/"test-only.json"
        report.parent.mkdir(parents=True,exist_ok=True)
        report.write_bytes(b'{"version":"test-frozen-file"}')
        self.state["frozen"]["sha256"] = hashlib.sha256(report.read_bytes()).hexdigest()
        return data_dir

    def test_independent_audit_when_no_trades(self):
        from paper_v7_audit import audit
        data_dir = self._auditable_source()
        paper.atomic_save(self.state)
        result = audit(self.state, paper.PAPER, data_dir)
        self.assertEqual(result["status"], "passed", result["checks"])
        self.assertEqual(result["trades_checked"], 0)
        self.assertEqual(result["checks_passed"], result["checks_total"])
        self.assertEqual(result["accounting"]["marked_equity_usdt"], 1000)
        self.assertEqual(result["accounting"]["commissions_usdt"], 0)
        self.assertEqual(self.state["frozen"]["control_decision"], "cash")

    def test_independent_audit_after_buy_and_stop(self):
        from paper_v7_audit import audit
        data_dir = self._auditable_source()
        self.state["pending"]={"atr":.2,"atr_pct":.2,"signal_ms":self.state["last_ms"]}
        candle_next=candle(self.state["last_ms"]+MINUTE,o=100,h=100.2,l=98,c=99)
        paper.process_candle(self.state,candle_next)
        paper.atomic_save(self.state)
        result=audit(self.state,paper.PAPER,data_dir)
        self.assertEqual(result["status"],"passed",result["checks"])
        self.assertEqual(result["trades_checked"],1)
        self.assertEqual(result["accounting"]["realized_usdt"],round(self.state["realized"],4))

    def test_independent_audit_detects_cash_tamper(self):
        from paper_v7_audit import audit
        data_dir=self._auditable_source()
        self.state["cash"]+=50
        result=audit(self.state,paper.PAPER,data_dir)
        self.assertEqual(result["status"],"failed")
        self.assertIn("cash_reconciliation",result["failures"])

    def test_independent_audit_detects_frozen_source_mismatch(self):
        from paper_v7_audit import audit
        data_dir=self._auditable_source()
        (data_dir/"v7"/"test-only.json").write_bytes(b'changed')
        result=audit(self.state,paper.PAPER,data_dir)
        self.assertEqual(result["status"],"failed")
        self.assertIn("source_checksum",result["failures"])

    def test_independent_audit_detects_corrupt_trade(self):
        from paper_v7_audit import audit
        import json
        data_dir=self._auditable_source()
        self.state["pending"]={"atr":.2,"atr_pct":.2,"signal_ms":self.state["last_ms"]}
        paper.process_candle(self.state,candle(self.state["last_ms"]+MINUTE,o=100,h=100.2,l=98,c=99))
        paper.atomic_save(self.state)
        journal=paper.PAPER/"archive"/"0000000001.json"
        record=json.loads(journal.read_text())
        record["trade"]["net_usdt"]+=100
        journal.write_text(json.dumps(record))
        result=audit(self.state,paper.PAPER,data_dir)
        self.assertEqual(result["status"],"failed")
        self.assertIn("recent_ledger_match",result["failures"])
        self.assertIn("trade_cost_identity",result["failures"])

    def test_v6_robot9_breakout_rule_and_frozen_atr_parity(self):
        # Independent conditions from V6: trend 60>240>720,
        # range 240 >=.009, price > slow*1.006,
        # price > previous 240-high*1.006, extension under .016.
        state=self.state
        prices=[100+i*.02 for i in range(720)]
        state["closes"]=list(prices)
        state["previous_close"]=prices[-1]
        state["last_price"]=prices[-1]
        state["tranges"]=[.01]*14
        new_price=115.12
        row=candle(state["last_ms"]+MINUTE,o=prices[-1],h=115.14,l=prices[-1]-.01,c=new_price)
        paper.process_candle(state,row)
        self.assertEqual(state["signals"],1)
        self.assertIsNotNone(state["pending"])
        self.assertEqual(state["blocked"],0)
        fast=sum(state["closes"][-60:])/60
        mid=sum(state["closes"][-240:])/240
        slow=sum(state["closes"])/720
        volatility=(max(state["closes"][-240:])-min(state["closes"][-240:]))/new_price
        prior=max(state["closes"][-241:-1])
        reference=fast>mid>slow and volatility>=.009 and new_price>slow*1.006 and new_price>prior*1.006 and new_price/fast-1<.016
        self.assertTrue(reference)
        self.assertAlmostEqual(state["pending"]["atr_pct"],sum(state["tranges"])/14/new_price*100)
        following=candle(state["last_ms"]+MINUTE,o=115.13,h=115.18,l=115.10,c=115.16)
        paper.process_candle(state,following)
        self.assertGreater(state["btc"],0)
        self.assertIsNone(state["pending"])
        self.assertAlmostEqual(state["entry"]["price"],115.13*(1+paper.SLIP))
        self.assertEqual(state["frozen"]["control_decision"],"cash")

    def test_v6_robot9_preentry_atr_gate_can_reject_signal(self):
        state=self.state
        state["frozen"]["shadow_atr"]["threshold_pct"]=.0001
        prices=[100+i*.02 for i in range(720)]
        state["closes"]=list(prices)
        state["previous_close"]=prices[-1]
        state["tranges"]=[.01]*14
        row=candle(state["last_ms"]+MINUTE,o=prices[-1],h=115.14,l=prices[-1]-.01,c=115.12)
        paper.process_candle(state,row)
        self.assertEqual(state["signals"],1)
        self.assertEqual(state["blocked"],1)
        self.assertIsNone(state["pending"])
        self.assertEqual(state["cash"],1000)
        self.assertEqual(state["frozen"]["control_decision"],"cash")

    def test_readonly_http_endpoints_with_real_handler(self):
        import http.server
        import json
        import threading
        import urllib.error
        import urllib.request
        import collector_service
        data_dir=self._auditable_source()
        original_data=paper.DATA
        paper.DATA=data_dir
        try:
            paper.atomic_save(self.state)
            srv=http.server.ThreadingHTTPServer(("127.0.0.1",0),collector_service.Handler)
            thread=threading.Thread(target=srv.serve_forever,daemon=True)
            thread.start()
            try:
                base="http://127.0.0.1:"+str(srv.server_address[1])
                for route in ("/health","/paper-v7","/paper-v7-journal?page=1&size=20","/paper-v7-audit"):
                    with urllib.request.urlopen(base+route,timeout=4) as response:
                        self.assertEqual(response.status,200,route)
                        obj=json.load(response)
                        self.assertIsInstance(obj,dict,route)
                        if route.endswith("paper-v7-audit"):
                            self.assertEqual(obj["status"],"passed",obj)
                            self.assertEqual(obj["trades_expected"],0)
                        if route.startswith("/paper-v7-journal"):
                            self.assertEqual(obj["ledger"]["total"],0)
                            self.assertEqual(obj["control_decision"],"cash")
                        if route=="/paper-v7":
                            self.assertEqual(obj["control"]["decision"],"cash")
                            self.assertEqual(obj["shadow"]["mode"],"exploratory_paper_only")
                with self.assertRaises(urllib.error.HTTPError) as err:
                    urllib.request.urlopen(base+"/paper-v7-journal?page=0",timeout=4)
                self.assertEqual(err.exception.code,400)
                with self.assertRaises(urllib.error.HTTPError) as err:
                    urllib.request.urlopen(urllib.request.Request(base+"/run-real-order",method="POST"),timeout=4)
                self.assertEqual(err.exception.code,404)
            finally:
                srv.shutdown()
                srv.server_close()
                thread.join(3)
        finally:
            paper.DATA=original_data

    def test_guard_approves_accounting_on_startup(self):
        from paper_v7_guard import check
        data_dir = self._auditable_source()
        report = check(self.state, paper.PAPER, data_dir)
        self.assertEqual(report["status"], "passed", report)
        self.assertEqual(report["checks_passed"], report["checks_total"])
        self.assertGreater(report["checks_total"], 10)
        paper.atomic_save(self.state)
        self.assertEqual(paper._load()["audit_guard"]["status"], "passed")
        self.assertEqual(self.state["frozen"]["control_decision"], "cash")

    def test_guard_skips_until_15_new_closed_minutes(self):
        from paper_v7_guard import check
        data_dir = self._auditable_source()
        first = check(self.state, paper.PAPER, data_dir)
        start_ms = first["last_candle_ms"]
        for i in range(1, 15):
            paper.process_candle(self.state, candle(start_ms + i*MINUTE))
        self.assertIsNone(check(self.state, paper.PAPER, data_dir))
        paper.process_candle(self.state, candle(start_ms + 15*MINUTE))
        due = check(self.state, paper.PAPER, data_dir)
        self.assertIsNotNone(due)
        self.assertEqual(due["status"], "passed", due)
        self.assertEqual(due["last_candle_ms"], start_ms+15*MINUTE)
        self.assertEqual(self.state["frozen"]["control_decision"], "cash")

    def test_guard_fail_closed_on_account_tampering(self):
        from paper_v7_guard import check
        data_dir=self._auditable_source()
        self.state["cash"] += 50
        guard=check(self.state, paper.PAPER, data_dir)
        self.assertEqual(guard["status"],"failed")
        self.assertIn("cash_reconciliation",guard["failures"])
        self.assertEqual(self.state["phase"],"paused_accounting_audit")
        paper.atomic_save(self.state)
        from paper_v7_daily import alerts
        signal=alerts(self.state,{"phase":"running"},self.state["last_ms"]+MINUTE,
                      {"status":"complete"})
        self.assertIn("accounting_audit_failed",[x["code"] for x in signal["active"]])
        self.assertGreater(signal["critical"],0)

    def test_guard_fail_closed_on_frozen_report_change(self):
        from paper_v7_guard import check
        data_dir=self._auditable_source()
        (data_dir/"v7"/"test-only.json").write_bytes(b'adulterated')
        report=check(self.state,paper.PAPER,data_dir)
        self.assertEqual(report["status"],"failed")
        self.assertIn("source_checksum",report["failures"])
        self.assertEqual(self.state["phase"],"paused_accounting_audit")

    def test_guard_persists_failure_and_does_not_resume_automatically(self):
        from paper_v7_guard import check
        data_dir=self._auditable_source()
        self.state["cash"]+=20
        failure=check(self.state,paper.PAPER,data_dir)
        paper.atomic_save(self.state)
        stored=paper._load()
        self.assertEqual(stored["audit_guard"],failure)
        self.assertEqual(stored["phase"],"paused_accounting_audit")
        self.assertIsNone(check(stored,paper.PAPER,data_dir))
        self.assertEqual(stored["phase"],"paused_accounting_audit")

    def test_minimal_version_test_dashboard_and_historical_preservation(self):
        from html.parser import HTMLParser
        main = pathlib.Path(__file__).with_name("dashboard.html").read_text(encoding="utf-8")
        legacy = pathlib.Path(__file__).with_name("dashboard_legacy.html").read_text(encoding="utf-8")
        class Page(HTMLParser):
            def __init__(self):
                super().__init__()
                self.ids=[]
                self.buttons=[]
            def handle_starttag(self, tag, attrs):
                attrs=dict(attrs)
                if attrs.get("id"):
                    self.ids.append(attrs["id"])
                if tag=="button":
                    self.buttons.append(attrs)
        page=Page();page.feed(main)
        self.assertEqual(len(page.ids),len(set(page.ids)))
        self.assertEqual(len(page.buttons),1,"Main panel has exactly one test button")
        self.assertEqual(page.buttons[0].get("id"),"testV7")
        self.assertIn("▶ Testar V7",main)
        self.assertIn('id="testResult" hidden',main)
        self.assertIn("fetch('/paper-v7'",main)
        self.assertIn("fetch('/paper-v7-audit'",main)
        self.assertIn("V8",main)
        self.assertIn("Indisponível",main)
        self.assertNotIn("Testar V8",main)
        self.assertNotIn('id="runbtn"',main)
        self.assertNotIn('id="labrobot"',main)
        self.assertIn('href="/laboratorio"',main)
        self.assertIn('id="runbtn"',legacy)
        self.assertIn('id="labrobot"',legacy)
        self.assertIn("V6 · Pesquisa",legacy)
        self.assertIn("V7 · Observação",legacy)

    def test_html_routes_expose_simple_test_and_legacy_archive(self):
        import http.server
        import threading
        import urllib.request
        import collector_service
        server=http.server.ThreadingHTTPServer(("127.0.0.1",0),collector_service.Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            base="http://127.0.0.1:"+str(server.server_address[1])
            with urllib.request.urlopen(base+"/",timeout=4) as response:
                self.assertEqual(response.status,200)
                html=response.read().decode()
                self.assertIn('id="testV7"',html)
                self.assertNotIn('id="runbtn"',html)
            with urllib.request.urlopen(base+"/laboratorio",timeout=4) as response:
                self.assertEqual(response.status,200)
                html=response.read().decode()
                self.assertIn('id="runbtn"',html)
                self.assertIn("V7 · Observação",html)
        finally:
            server.shutdown();server.server_close();thread.join(3)

    def test_atomic_persistence_and_readonly_snapshot(self):
        paper.atomic_save(self.state)
        self.assertEqual(paper._load()["activation_ms"], self.state["activation_ms"])
        snap = paper.snapshot()
        self.assertEqual(snap["control"]["decision"], "cash")
        self.assertEqual(snap["control"]["trades"], 0)
        self.assertEqual(snap["shadow"]["trades"], 0)
        self.assertEqual(snap["shadow"]["mode"], "exploratory_paper_only")


if __name__ == "__main__":
    unittest.main()
