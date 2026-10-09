"""Testes das funções puras de boost_audio.py (sem ffmpeg)."""
import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
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


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "precisa de ffmpeg")
class EndToEnd(unittest.TestCase):
    """Roda o script de verdade num MKV pequeno com 2 áudios e legenda SRT (o caso que já quebrou)."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        srt = os.path.join(self.dir, "s.srt")
        with open(srt, "w", encoding="utf-8") as f:
            f.write(chr(10).join(["1", "00:00:01,000 --> 00:00:02,000", "Oi", ""]))
        self.src = os.path.join(self.dir, "in.mkv")
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=d=20:s=160x120:r=10",
             "-f", "lavfi", "-i", "sine=f=440:d=20", "-f", "lavfi", "-i", "sine=f=660:d=20", "-i", srt,
             "-map", "0", "-map", "1", "-map", "2", "-map", "3", "-c:v", "libx264", "-c:a", "aac", "-c:s", "srt", self.src],
            check=True)

    def run_main(self, *extra):
        out = os.path.join(self.dir, "out" + (".mp4" if "--mp4" in extra else ".mkv"))
        argv = ["boost_audio.py", self.src, "-o", out, "-y", *extra]
        old = sys.argv
        sys.argv = argv
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                rc = b.main()
        finally:
            sys.argv = old
        return rc, out

    def streams(self, path):
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name", "-of", "csv=p=0", path],
                           capture_output=True, text=True)
        return r.stdout.split()

    def test_mkv_keeps_all_streams_and_audio_is_complete(self):
        rc, out = self.run_main("--db", "6")
        self.assertEqual(rc, 0)
        self.assertEqual(self.streams(out), ["h264,video", "aac,audio", "aac,audio", "subrip,subtitle"])
        self.assertEqual(b.check_audio(out, [0, 1], 20.0), [])

    def test_tv_to_mp4_converts_subs_and_single_track(self):
        rc, out = self.run_main("--tv", "--mp4", "--track", "1")
        self.assertEqual(rc, 0)
        self.assertEqual(self.streams(out), ["h264,video", "aac,audio", "aac,audio", "mov_text,subtitle"])


if __name__ == "__main__":
    unittest.main()
