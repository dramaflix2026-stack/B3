import unittest
from datetime import datetime
from simulator import Simulator, Risk

T = datetime(2026, 11, 14, 10, 0)
class SimulatorTests(unittest.TestCase):
    def test_win_pnl(self):
        s=Simulator("WIN",Risk(slippage_ticks=0))
        s.open("buy",1,130000,T)
        self.assertEqual(s.close(130050,T)["net"],10)
    def test_wdo_pnl(self):
        s=Simulator("WDO",Risk(slippage_ticks=0))
        s.open("sell",1,5000,T)
        self.assertEqual(s.close(4999,T)["net"],10)
    def test_daily_lock(self):
        s=Simulator("WIN",Risk(max_daily_loss=10,slippage_ticks=0))
        s.open("buy",1,130000,T)
        s.close(129950,T)
        with self.assertRaises(RuntimeError): s.open("buy",1,130000,T)
    def test_stop(self):
        s=Simulator("WIN",Risk(stop_brl=10,slippage_ticks=0))
        s.open("buy",1,130000,T)
        self.assertEqual(s.mark(129950,T)["reason"],"stop")
    def test_trade_limit(self):
        s=Simulator("WIN",Risk(max_trades=1,slippage_ticks=0))
        s.open("buy",1,130000,T);s.close(130005,T)
        with self.assertRaises(RuntimeError): s.open("buy",1,130000,T)
    def test_emergency(self):
        s=Simulator("WIN",Risk())
        s.emergency_stop(130000,T)
        with self.assertRaises(RuntimeError): s.open("buy",1,130000,T)
    def test_unrealized_includes_exit_slippage_and_fees(self):
        s=Simulator("WIN",Risk(slippage_ticks=1,fee_per_side_per_contract=0.5))
        s.open("buy",1,130000,T)
        self.assertEqual(s.unrealized(130000),-3)
    def test_daily_limit_triggers_intratrade(self):
        s=Simulator("WIN",Risk(max_daily_loss=5,stop_brl=100,slippage_ticks=0))
        s.open("buy",1,130000,T)
        self.assertEqual(s.mark(129975,T)["reason"],"stop")
        self.assertTrue(s.locked)
    def test_invalid_price_rejected(self):
        s=Simulator("WIN",Risk())
        for price in (float("nan"),float("inf"),130001):
            with self.assertRaises(ValueError):
                s.open("buy",1,price,T)
    def test_invalid_risk_rejected(self):
        for risk in (Risk(slippage_ticks=-1),Risk(max_daily_loss=0),Risk(fee_per_side_per_contract=-1)):
            with self.assertRaises(ValueError):
                Simulator("WIN",risk)
if __name__ == "__main__": unittest.main()
