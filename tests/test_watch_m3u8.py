"""Testes das funções puras de watch_m3u8.py (sem rede e sem ffmpeg)."""
import contextlib
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import watch_m3u8 as w  # noqa: E402

MASTER = """#EXTM3U
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="Português",LANGUAGE="pt-BR",URI="audio/pt.m3u8"
#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="sub",NAME="English",LANGUAGE="en",FORCED=NO,URI="subs/en.m3u8"
#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="sub",NAME="Forçada",LANGUAGE="pt-BR",FORCED=YES,URI="subs/pt-forced.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360,AUDIO="aud",SUBTITLES="sub"
360/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=5000000,RESOLUTION=1920x1080,AUDIO="aud",SUBTITLES="sub"
1080/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2500000,RESOLUTION=1280x720,AUDIO="aud",SUBTITLES="sub"
720/index.m3u8
"""

MEDIA_VOD = """#EXTM3U
#EXT-X-TARGETDURATION:6
#EXTINF:6.0,
seg0.ts
#EXTINF:6.0,
seg1.ts
#EXT-X-ENDLIST
"""

BASE = "https://cdn.example.com/video/master.m3u8"


class ParseAttrs(unittest.TestCase):
    def test_quoted_value_with_comma(self):
        a = w.parse_attrs('#EXT-X-MEDIA:TYPE=AUDIO,NAME="a,b",LANGUAGE="pt"')
        self.assertEqual(a, {"TYPE": "AUDIO", "NAME": "a,b", "LANGUAGE": "pt"})

    def test_no_colon(self):
        self.assertEqual(w.parse_attrs("#EXTM3U"), {})


class ParsePlaylist(unittest.TestCase):
    def test_invalid(self):
        self.assertEqual(w.parse_playlist("<html>nope</html>", BASE)[0], "invalid")
        self.assertEqual(w.parse_playlist("", BASE)[0], "invalid")

    def test_master_variants_resolved_to_absolute_urls(self):
        kind, variants = w.parse_playlist(MASTER, BASE)
        self.assertEqual(kind, "master")
        self.assertEqual(len(variants), 3)
        self.assertEqual(variants[0]["url"], "https://cdn.example.com/video/360/index.m3u8")
        self.assertEqual(variants[0]["bw"], 800000)
        self.assertEqual(variants[0]["res"], "640x360")
        self.assertEqual(variants[0]["media"][0]["URI"], "https://cdn.example.com/video/audio/pt.m3u8")

    def test_media_playlist_vod(self):
        kind, info = w.parse_playlist(MEDIA_VOD, "https://cdn.example.com/a/index.m3u8")
        self.assertEqual(kind, "media")
        self.assertEqual(info["segments"], 2)
        self.assertFalse(info["live"])
        self.assertFalse(info["encrypted"])
        self.assertEqual(info["first"], "https://cdn.example.com/a/seg0.ts")

    def test_media_playlist_live_and_encrypted(self):
        text = '#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="k.key"\n#EXTINF:6,\nseg.ts\n'
        _, info = w.parse_playlist(text, BASE)
        self.assertTrue(info["live"])
        self.assertTrue(info["encrypted"])


class PickVariant(unittest.TestCase):
    def setUp(self):
        _, v = w.parse_playlist(MASTER, BASE)
        self.variants = sorted(v, key=lambda x: x["bw"], reverse=True)  # como o probe() ordena

    def test_best_worst(self):
        self.assertEqual(w.variant_height(w.pick_variant(self.variants, "best")), 1080)
        self.assertEqual(w.variant_height(w.pick_variant(self.variants, "worst")), 360)

    def test_auto_and_empty_return_none(self):
        self.assertIsNone(w.pick_variant(self.variants, "auto"))
        self.assertIsNone(w.pick_variant(self.variants, None))
        self.assertIsNone(w.pick_variant([], "720"))

    def test_height_picks_highest_that_fits(self):
        self.assertEqual(w.variant_height(w.pick_variant(self.variants, "720")), 720)
        self.assertEqual(w.variant_height(w.pick_variant(self.variants, "900p")), 720)

    def test_height_below_minimum_falls_back_to_lowest(self):
        self.assertEqual(w.variant_height(w.pick_variant(self.variants, "240")), 360)

    def test_invalid_spec_exits(self):
        with self.assertRaises(SystemExit):
            w.pick_variant(self.variants, "abc")


class ParseTime(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(w.parse_time("90"), 90)
        self.assertEqual(w.parse_time("1:30"), 90)
        self.assertEqual(w.parse_time("1:02:03"), 3723)

    def test_invalid_exits(self):
        with self.assertRaises(SystemExit):
            w.parse_time("abc")


class BuildHeaders(unittest.TestCase):
    def test_referer_adds_origin(self):
        h = w.build_headers("https://site.example/path/page", "UA", None)
        self.assertEqual(h["Referer"], "https://site.example/path/page")
        self.assertEqual(h["Origin"], "https://site.example")
        self.assertEqual(h["User-Agent"], "UA")

    def test_extra_headers(self):
        h = w.build_headers(None, "UA", ["X-Test: 1", "Cookie: a=b:c"])
        self.assertEqual(h["X-Test"], "1")
        self.assertEqual(h["Cookie"], "a=b:c")
        self.assertNotIn("Referer", h)

    def test_invalid_extra_header_exits(self):
        with self.assertRaises(SystemExit):
            w.build_headers(None, "UA", ["sem-dois-pontos"])


class RewritePlaylist(unittest.TestCase):
    PREFIX = "/tok/p?u="

    def test_segments_and_uri_attributes_go_through_proxy(self):
        text = '#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="k.key"\n#EXTINF:6,\nseg0.ts\n'
        out = w.rewrite_playlist(text, "https://cdn.example.com/a/index.m3u8", self.PREFIX)
        self.assertIn('URI="/tok/p?u=https%3A%2F%2Fcdn.example.com%2Fa%2Fk.key"', out)
        self.assertIn("/tok/p?u=https%3A%2F%2Fcdn.example.com%2Fa%2Fseg0.ts", out)
        self.assertTrue(out.endswith("\n"))

    def test_data_uri_untouched(self):
        text = '#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="data:text/plain;base64,AAAA"\n'
        out = w.rewrite_playlist(text, BASE, self.PREFIX)
        self.assertIn('URI="data:text/plain;base64,AAAA"', out)


class Languages(unittest.TestCase):
    def test_lang3(self):
        self.assertEqual(w.lang3("pt-BR"), "por")
        self.assertEqual(w.lang3("en"), "eng")
        self.assertEqual(w.lang3("fra"), "fra")
        self.assertEqual(w.lang3("xx"), "und")
        self.assertEqual(w.lang3(None), "und")

    def test_fmt_dur(self):
        self.assertEqual(w.fmt_dur(3723), "01:02:03")
        self.assertEqual(w.fmt_dur(-5), "00:00:00")


class Selection(unittest.TestCase):
    def setUp(self):
        _, v = w.parse_playlist(MASTER, BASE)
        self.subs = w.sub_items(v[0])

    def pick(self, spec):
        with contextlib.redirect_stdout(io.StringIO()):  # select_items imprime a lista
            return w.select_items(self.subs, spec, "t", interactive=False)

    def test_sub_items_group_and_forced(self):
        self.assertEqual([s["lang"] for s in self.subs], ["en", "pt-BR"])
        self.assertEqual([s["forced"] for s in self.subs], [False, True])

    def test_select_all_none_and_numbers(self):
        self.assertEqual(len(self.pick("all")), 2)
        self.assertEqual(self.pick("none"), [])
        self.assertEqual(self.pick("2"), [self.subs[1]])

    def test_select_by_language_and_forced_flag(self):
        self.assertEqual(self.pick("en"), [self.subs[0]])
        self.assertEqual(self.pick("pt-BR:forced"), [self.subs[1]])

    def test_select_unknown_exits(self):
        with self.assertRaises(SystemExit):
            self.pick("zz")


class Vtt(unittest.TestCase):
    def test_shift_forward_and_clamp_at_zero(self):
        vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:02.500\nOi\n"
        self.assertIn("00:00:03.000 --> 00:00:04.500", w.shift_vtt(vtt, 2))
        self.assertIn("00:00:00.000 --> 00:00:01.500", w.shift_vtt(vtt, -1))

    def test_zero_offset_is_noop(self):
        vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nOi\n"
        self.assertEqual(w.shift_vtt(vtt, 0), vtt)


class OutName(unittest.TestCase):
    def test_default_out_name(self):
        self.assertEqual(w.default_out_name("https://x.example/a/filme.m3u8?token=1"), "filme.mp4")
        self.assertEqual(w.default_out_name("https://x.example/"), "video.mp4")


if __name__ == "__main__":
    unittest.main()
