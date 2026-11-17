"""Species keys and portrait lookup.

The icon for a pal is chosen by species, so a boss blueprint and a tamed pal of
the same species must resolve to the same key — otherwise one `Anubis.png` would
only cover half the Anubis on the server.

Plain unittest so `python -m unittest discover -s tests` needs no dependency;
scripts/verify.sh runs it.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.app_env import import_app  # noqa: E402

paladmin, APP_IMPORT_ERROR = import_app()

needs_app = unittest.skipUnless(
    paladmin is not None, f"app.py is not importable here: {APP_IMPORT_ERROR}")


@needs_app
class SpeciesTests(unittest.TestCase):
    def test_boss_and_plain_blueprints_share_a_species(self):
        for blueprint, expected in [
            ("BP_Anubis_MiddleBoss_C", "Anubis"),
            ("BP_Anubis_C", "Anubis"),
            ("BP_ElecPanda_BOSS_C", "ElecPanda"),
            ("BP_FlowerDinosaur_Electric_C", "FlowerDinosaur_Electric"),
            ("BP_SheepBall_C", "SheepBall"),
            ("/Game/Pal/Blueprint/Character/BP_PinkCat_C", "PinkCat"),
            ("SK_PinkCat_C", "PinkCat"),
        ]:
            with self.subTest(blueprint=blueprint):
                self.assertEqual(paladmin.pal_species(blueprint), expected)

    def test_falls_back_to_the_display_name(self):
        self.assertEqual(paladmin.pal_species("", "Dinossom Lux"), "DinossomLux")
        self.assertEqual(paladmin.pal_species(None, "Anubis"), "Anubis")
        self.assertEqual(paladmin.pal_species("", ""), "")


@needs_app
class IconNameTests(unittest.TestCase):
    """The game's own portrait files, which is what an export of the pak gives."""

    def test_reads_the_games_icon_names(self):
        for stem, expected in [
            ("T_Anubis_icon_normal", "Anubis"),
            ("T_Anubis_Icon_Normal", "Anubis"),
            ("T_Anubis_Skin001_icon_normal", "Anubis"),
            ("T_FlowerDinosaur_Electric_icon_normal", "FlowerDinosaur_Electric"),
            ("T_PinkCat_Skin004_icon_normal", "PinkCat"),
            ("Anubis", "Anubis"),
            ("Grizzbolt", "Grizzbolt"),
        ]:
            with self.subTest(stem=stem):
                self.assertEqual(paladmin.icon_species(stem), expected)


@needs_app
class IconTests(unittest.TestCase):
    def setUp(self):
        self.directory = os.environ["PAL_ICON_DIR"]
        os.makedirs(self.directory, exist_ok=True)
        paladmin._icon_cache["at"] = 0.0

    def tearDown(self):
        for name in os.listdir(self.directory):
            try:
                os.remove(os.path.join(self.directory, name))
            except PermissionError:
                pass  # a served file can stay open on Windows until the client closes
        paladmin._icon_cache["at"] = 0.0

    def write(self, name, payload=b"\x89PNG\r\n\x1a\n"):
        with open(os.path.join(self.directory, name), "wb") as handle:
            handle.write(payload)

    def test_lists_only_real_images_and_lowercases_the_key(self):
        self.write("Anubis.png")
        self.write("SheepBall.PNG")
        self.write("notes.txt")
        icons = paladmin.pal_icons()
        self.assertEqual(sorted(icons), ["anubis", "sheepball"])
        self.assertEqual(icons["anubis"], "Anubis.png")

    def test_an_exported_pak_folder_works_without_renaming(self):
        self.write("T_Anubis_icon_normal.png")
        self.write("T_Anubis_Skin001_icon_normal.png")
        self.write("T_PinkCat_icon_normal.png")
        self.write("T_PinkCat_Skin003_icon_normal.png")
        self.write("T_Anubis_icon_normal.uasset")   # not an image
        icons = paladmin.pal_icons()
        self.assertEqual(sorted(icons), ["anubis", "pinkcat"])
        self.assertEqual(icons["anubis"], "T_Anubis_icon_normal.png")
        self.assertEqual(icons["pinkcat"], "T_PinkCat_icon_normal.png")

    def test_case_insensitive_lookup_is_what_the_route_uses(self):
        self.write("Anubis.png")
        paladmin._icon_cache["at"] = 0.0
        client = paladmin.app.test_client()
        response = client.get("/api/pal-icon/anubis")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Content-Type"], "image/png")
        self.assertIn("max-age", response.headers.get("Cache-Control", ""))
        response.close()
        self.assertEqual(client.get("/api/pal-icon/grizzbolt").status_code, 404)

    def test_no_icon_directory_is_not_an_error(self):
        os.rmdir(self.directory)
        paladmin._icon_cache["at"] = 0.0
        self.assertEqual(paladmin.pal_icons(), {})
        os.makedirs(self.directory, exist_ok=True)


if __name__ == "__main__":
    unittest.main()
