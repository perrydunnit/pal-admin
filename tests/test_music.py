"""Tests for the audio upload rules.

Plain unittest so `python -m unittest discover -s tests` works without adding a
dependency; scripts/verify.sh runs it.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import musiclib  # noqa: E402

MB = 1024 * 1024


class NameTests(unittest.TestCase):
    def test_extension_is_lowercased_and_stripped(self):
        self.assertEqual(musiclib.extension("Clair de Lune.MP3"), ".mp3")
        self.assertEqual(musiclib.extension("C:\\music\\song.flac"), ".flac")
        self.assertEqual(musiclib.extension("noext"), "")

    def test_display_name_drops_extension_and_junk(self):
        self.assertEqual(musiclib.display_name("Clair de Lune.mp3"), "Clair de Lune")
        self.assertEqual(musiclib.display_name("  spaced__  .mid"), "spaced")
        self.assertEqual(musiclib.display_name("../../etc/passwd.mp3"), "passwd")
        self.assertEqual(musiclib.display_name(""), "track")

    def test_display_name_is_bounded(self):
        self.assertLessEqual(len(musiclib.display_name("x" * 500 + ".mp3")), musiclib.MAX_NAME)

    def test_kind_matches_the_mod(self):
        self.assertEqual(musiclib.kind(".mid"), "midi")
        self.assertEqual(musiclib.kind(".midi"), "midi")
        self.assertEqual(musiclib.kind(".mp3"), "audio")
        self.assertEqual(musiclib.kind(".flac"), "audio")
        self.assertIsNone(musiclib.kind(".exe"))

    def test_id_is_stable_and_short(self):
        digest = musiclib.sha256(b"hello")
        self.assertEqual(musiclib.identifier(digest), digest[:32])
        self.assertEqual(len(musiclib.identifier(digest)), 32)


class SniffTests(unittest.TestCase):
    def test_accepts_real_headers(self):
        self.assertTrue(musiclib.sniff(".mid", b"MThd\x00\x00\x00\x06"))
        self.assertTrue(musiclib.sniff(".wav", b"RIFF\x24\x00\x00\x00WAVEfmt "))
        self.assertTrue(musiclib.sniff(".mp3", b"ID3\x04\x00"))
        self.assertTrue(musiclib.sniff(".mp3", b"\xff\xfb\x90\x00"))
        self.assertTrue(musiclib.sniff(".flac", b"fLaC\x00\x00\x00\x22"))
        self.assertTrue(musiclib.sniff(".ogg", b"OggS\x00\x02"))
        self.assertTrue(musiclib.sniff(".opus", b"OggS\x00\x02"))
        self.assertTrue(musiclib.sniff(".m4a", b"\x00\x00\x00\x20ftypM4A "))
        self.assertTrue(musiclib.sniff(".wma", bytes.fromhex("3026b2758e66cf11a6d900aa0062ce6c")))

    def test_rejects_renamed_failures(self):
        self.assertFalse(musiclib.sniff(".mp3", b"<?xml version"))
        self.assertFalse(musiclib.sniff(".wav", b"RIFF\x00\x00\x00\x00AVI "))
        self.assertFalse(musiclib.sniff(".flac", b"ID3\x04"))
        self.assertFalse(musiclib.sniff(".mid", b"MTh"))
        self.assertFalse(musiclib.sniff(".mp3", b""))


class ValidateTests(unittest.TestCase):
    def test_returns_the_stored_shape(self):
        out = musiclib.validate("Ode to Joy.mid", b"MThd\x00\x00\x00\x06junk", 10 * MB)
        self.assertEqual(out["name"], "Ode to Joy")
        self.assertEqual(out["ext"], ".mid")
        self.assertEqual(out["kind"], "midi")
        self.assertEqual(out["bytes"], 12)
        self.assertEqual(out["id"], out["sha256"][:32])

    def test_rejects_bad_input(self):
        cases = [
            ("", b"MThd", "file name"),
            ("song.exe", b"MZ", "unsupported type"),
            ("song.mp3", b"", "empty"),
            ("song.mp3", b"not audio", "does not look like"),
        ]
        for name, data, message in cases:
            with self.subTest(name=name, message=message):
                with self.assertRaises(ValueError) as ctx:
                    musiclib.validate(name, data, 10 * MB)
                self.assertIn(message, str(ctx.exception))

    def test_accepts_a_minimal_mp3_frame(self):
        musiclib.validate("song.mp3", b"\xff\xfb" + b"\x00" * 100, 10 * MB)

    def test_rejects_oversized_files(self):
        with self.assertRaises(ValueError) as ctx:
            musiclib.validate("big.mp3", b"\xff\xfb" + b"\x00" * 2048, 1024)
        self.assertIn("larger than", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
