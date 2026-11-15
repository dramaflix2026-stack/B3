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
