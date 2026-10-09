"""Testes das funções puras de boost_audio.py (sem ffmpeg)."""
import os
import sys
import unittest
from argparse import Namespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import boost_audio as b  # noqa: E402


class BuildFilter(unittest.TestCase):
    def test_plain_boost(self):
        f = b.build_filter(Namespace(dialogue=False, normalize=False, db=6.0))
        self.assertEqual(f, "volume=6.0dB,alimiter=limit=0.97:level=disabled")

    def test_normalize_without_extra_gain(self):
        f = b.build_filter(Namespace(dialogue=False, normalize=True, db=0.0))
        self.assertTrue(f.startswith("loudnorm="))
        self.assertNotIn("volume=", f)

    def test_normalize_resamples_back_to_48k(self):
        f = b.build_filter(Namespace(dialogue=False, normalize=True, db=0.0))
        self.assertLess(f.index("loudnorm"), f.index("aresample=48000"))

    def test_normalize_with_extra_gain_comes_after_loudnorm(self):
        f = b.build_filter(Namespace(dialogue=False, normalize=True, db=3.0))
        self.assertLess(f.index("loudnorm"), f.index("volume=3.0dB"))

    def test_dialogue_compresses_first_and_limiter_is_last(self):
        f = b.build_filter(Namespace(dialogue=True, normalize=False, db=4.0))
        self.assertTrue(f.startswith("acompressor="))
        self.assertTrue(f.endswith("alimiter=limit=0.97:level=disabled"))


class TvPreset(unittest.TestCase):
    def test_tv_compresses_then_normalizes_with_low_lra(self):
        f = b.build_filter(Namespace(dialogue=False, normalize=True, db=0.0, tv=True))
        self.assertLess(f.index("acompressor"), f.index("loudnorm"))
        self.assertIn("LRA=7", f)
        self.assertLess(f.index("loudnorm"), f.index("aresample=48000"))
        self.assertTrue(f.endswith("alimiter=limit=0.97:level=disabled"))


class Formatting(unittest.TestCase):
    def test_fmt(self):
        self.assertEqual(b.fmt(3723), "01:02:03")
        self.assertEqual(b.fmt(-1), "00:00:00")

    def test_label(self):
        s = {"codec_name": "aac", "channels": 6, "tags": {"language": "por", "title": "Dublado"}}
        self.assertEqual(b.label(0, s), "#1  por   aac 6ch  Dublado")


if __name__ == "__main__":
    unittest.main()
