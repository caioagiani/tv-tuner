"""Drivers e painel contra a Roku falsa (tests/fake_roku.py). Sem TV de verdade:
python3 -m unittest discover tests"""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["TVTUNER_CONFIG"] = os.path.join(tempfile.mkdtemp(), "config.json")

from tests import fake_roku                         # noqa: E402
from tvtuner import config, drivers, panel          # noqa: E402
from tvtuner.drivers.base import HOME_APP, Unsupported  # noqa: E402

IP = "127.0.0.1"
ROKU = SRV = None


def setUpModule():
    global ROKU, SRV
    ROKU, SRV = fake_roku.start(IP)


def tearDownModule():
    SRV.shutdown()
    SRV.server_close()


class RokuTest(unittest.TestCase):
    def setUp(self):
        self.tv = ROKU
        self.tv.pressed.clear()
        self.tv.power, self.tv.active, self.tv.limited = "PowerOn", None, False
        self.drv = drivers.DRIVERS["roku"](IP)

    def test_probe_finds_roku(self):
        d = drivers.probe(IP)
        self.assertEqual((d["brand"], d["name"], d["model"], d["power"]), ("roku", "Sala Roku", "TCL Roku TV", "on"))
        self.assertEqual(drivers.brand_of(IP), "roku")

    def test_keys_translate_from_samsung_names(self):
        for k in ("KEY_UP", "KEY_ENTER", "KEY_RETURN", "KEY_VOLUP", "KEY_MUTE", "Select", "HOME"):
            self.drv.key(k)
        self.assertEqual(self.tv.pressed, ["Up", "Select", "Back", "VolumeUp", "VolumeMute", "Select", "Home"])
        with self.assertRaises(Unsupported):
            self.drv.key("KEY_NADA")

    def test_text_goes_letter_by_letter(self):
        self.drv.text("ab c")
        self.assertEqual(self.tv.pressed, ["Lit_a", "Lit_b", "Lit_ ", "Lit_c"])

    def test_apps_launch_and_current(self):
        apps = self.drv.apps()
        self.assertIn({"appId": "12", "name": "Netflix", "icon": "/appicon/12"}, apps)
        self.assertIsNone(self.drv.current_app(self.drv.apps))
        self.drv.launch("12")
        self.assertEqual(self.drv.current_app(self.drv.apps), {"appId": "12", "name": "Netflix"})
        self.drv.launch(HOME_APP)                     # botão Início do painel = Home
        self.assertEqual(self.tv.pressed, ["Home"])

    def test_power_and_media(self):
        self.drv.power_off()
        self.assertEqual(self.drv.info()["power"], "standby")
        self.assertEqual(self.drv.power_on(), "key")
        self.assertEqual(self.drv.power_on(), "already")
        self.drv.media_cmd("play")
        self.assertEqual(self.drv.media(), "PLAYING")

    def test_no_absolute_volume(self):
        self.assertIsNone(self.drv.volume())
        with self.assertRaises(Unsupported):
            self.drv.set_volume(20)

    def test_limited_mode_explains(self):
        self.tv.limited = True
        with self.assertRaisesRegex(RuntimeError, "Controle por apps móveis"):
            self.drv.key("KEY_UP")


class PanelOnRokuTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tv = ROKU
        config.remember(IP, brand="roku", name="Sala Roku")
        config.select(IP)
        hub = panel.Hub(IP)
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), panel.make_handler(hub, None, 0))
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def call(self, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_state_declares_caps(self):
        code, body = self.call("/api/state")
        st = json.loads(body)
        self.assertEqual(code, 200)
        self.assertEqual((st["brand"], st["power"], st["paired"]), ("roku", "on", True))
        self.assertNotIn("volume", st["caps"])
        self.assertIn("icons", st["caps"])

    def test_key_launch_icon_and_unsupported(self):
        self.assertEqual(self.call("/api/key", {"key": "KEY_DOWN"})[0], 200)
        self.assertEqual(self.call("/api/launch", {"appId": "837"})[0], 200)
        self.assertIn("Down", self.tv.pressed)
        self.assertEqual(self.tv.active, "837")
        code, body = self.call("/appicon/837")
        self.assertEqual((code, body[:4]), (200, b"\x89PNG"))
        code, body = self.call("/api/volume", {"level": 10})
        self.assertEqual(code, 502)
        self.assertIn("volume", json.loads(body)["error"])


if __name__ == "__main__":
    unittest.main()
