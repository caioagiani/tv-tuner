"""Driver LG contra a LG falsa (tests/fake_lg.py)."""
import os
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["TVTUNER_CONFIG"] = os.path.join(tempfile.mkdtemp(), "config.json")

from tests import fake_lg                       # noqa: E402
from tvtuner import config, drivers, panel      # noqa: E402
from tvtuner.drivers import lg as lg_driver     # noqa: E402
from tvtuner.drivers.base import HOME_APP       # noqa: E402
from tvtuner.remote import PairingError         # noqa: E402

IP = "127.0.0.1"
PORT = 13000                    # a 3000 local costuma ser de servidor de desenvolvimento
LG = SRV = None


def setUpModule():
    global LG, SRV, OLD
    OLD = lg_driver.PORTS
    lg_driver.PORTS = ((PORT, "ws"),)
    LG, SRV = fake_lg.start(IP, PORT)


def tearDownModule():
    lg_driver.PORTS = OLD
    SRV.close()


class LGTest(unittest.TestCase):
    def setUp(self):
        config.forget(IP)
        LG.accept, LG.power, LG.app = True, "Active", None
        LG.buttons.clear(); LG.text.clear()
        self.drv = drivers.DRIVERS["lg"](IP)

    def tearDown(self):
        self.drv.close()

    def pair(self):
        config.remember(IP, brand="lg")
        self.drv.pair(timeout=5)

    def test_probe_without_pairing(self):
        d = drivers.probe(IP)
        self.assertEqual((d["brand"], d["model"], d["power"]), ("lg", "OLED55C1PSA", "on"))
        self.assertFalse(self.drv.paired())

    def test_pairing_saves_key_and_refusal_explains(self):
        LG.accept = False
        config.remember(IP, brand="lg")
        with self.assertRaisesRegex(PairingError, "recusou"):
            self.drv.pair(timeout=5)
        LG.accept = True
        self.drv.pair(timeout=5)
        self.assertEqual(config.tv(IP)["token"], fake_lg.KEY)

    def test_unpaired_control_asks_for_pairing(self):
        with self.assertRaisesRegex(RuntimeError, "não pareada"):
            self.drv.key("KEY_UP")

    def test_keys_text_apps(self):
        self.pair()
        self.drv.key("KEY_UP"); self.drv.key("KEY_ENTER"); self.drv.key("KEY_RETURN")
        time.sleep(0.2)
        self.assertEqual(LG.buttons, ["type:button\nname:UP\n\n", "type:button\nname:ENTER\n\n",
                                      "type:button\nname:BACK\n\n"])
        self.drv.text("stranger")
        self.assertEqual(LG.text, ["stranger", "\n"])
        apps = self.drv.apps()
        self.assertIn({"appId": "netflix", "name": "Netflix", "icon": "/appicon/netflix"}, apps)
        self.assertIsNone(self.drv.current_app(self.drv.apps))
        self.drv.launch("netflix")
        self.assertEqual(self.drv.current_app(self.drv.apps), {"appId": "netflix", "name": "Netflix"})
        body, ctype = self.drv.icon("netflix")
        self.assertEqual(body[:4], b"\x89PNG")
        self.drv.launch(HOME_APP)
        time.sleep(0.2)
        self.assertEqual(LG.buttons[-1], "type:button\nname:HOME\n\n")

    def test_volume_mute_power_and_mac(self):
        self.pair()
        self.assertEqual(self.drv.set_volume(20), 20)
        self.assertEqual(self.drv.volume(), 20)
        self.drv.set_mute(True)
        self.assertTrue(self.drv.muted())
        d = self.drv.info()
        self.assertEqual((d["power"], d["model"]), ("on", "OLED55C1PSA"))
        self.assertEqual(config.tv(IP)["mac"], "11:22:33:44:55:66")
        self.drv.power_off()
        self.assertEqual(self.drv.info()["power"], "standby")
        self.assertEqual(self.drv.power_on(), "key")
        self.assertEqual(self.drv.power_on(), "already")

    def test_panel_gets_ime_and_pushed_volume(self):
        self.pair()
        got = []
        tv = panel.TV(IP, emit=lambda k, d: got.append((k, d)))
        st = tv.snapshot()
        self.assertEqual((st["brand"], st["paired"], st["volume"]), ("lg", True, LG.volume))
        LG.ime(True)
        LG.volume = 33; LG._push_vol()
        time.sleep(0.3)
        self.assertIn(("ime", {"event": "lg.ime", "open": True, "data": "text"}), got)
        self.assertEqual(tv.live["volume"], 33)
        self.assertEqual(tv.snapshot()["volume"], 33)
        tv.drv.close()


if __name__ == "__main__":
    unittest.main()
