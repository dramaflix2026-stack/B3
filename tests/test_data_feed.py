import unittest
from data_feed import parse_candles, HistoricalFeed, LiveFeed, LiveFeedUnavailable

HEADER = "timestamp,open,high,low,close,volume\n"
ROWS = "2026-11-13T12:00:00-03:00,130000,130010,129995,130005,20\n2026-11-13T12:01:00-03:00,130005,130015,130000,130010,12\n"

class FeedTests(unittest.TestCase):
    def test_parse_valid(self):
        data = parse_candles(HEADER + ROWS, "WIN")
        self.assertEqual(len(data), 2)
        self.assertEqual(data[0].close, 130005)
    def test_no_future_leak(self):
        feed = HistoricalFeed(parse_candles(HEADER + ROWS, "WIN"), "WIN")
        self.assertEqual(feed.next().close, 130005)
        self.assertEqual(feed.next().close, 130010)
        self.assertIsNone(feed.next())
    def test_reject_bad_tick(self):
        with self.assertRaises(ValueError):
            parse_candles(HEADER + ROWS.replace("130005", "130006"), "WIN")
    def test_reject_duplicate_time(self):
        with self.assertRaises(ValueError):
            parse_candles(HEADER + ROWS + ROWS.splitlines()[0] + "\n", "WIN")
    def test_reject_missing_timezone(self):
        with self.assertRaises(ValueError):
            parse_candles(HEADER + ROWS.replace("-03:00", ""), "WIN")
    def test_reject_invalid_ohlc(self):
        with self.assertRaises(ValueError):
            parse_candles(HEADER + ROWS.replace("129995", "130020"), "WIN")
    def test_live_fail_closed(self):
        feed = LiveFeed("WDO")
        self.assertFalse(feed.status.connected)
        with self.assertRaises(LiveFeedUnavailable):
            feed.next()

if __name__ == "__main__":
    unittest.main()
