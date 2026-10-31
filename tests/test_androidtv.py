"""Driver Android TV contra a TV falsa (tests/fake_androidtv.py)."""
import os
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["TVTUNER_CONFIG"] = os.path.join(tempfile.mkdtemp(), "config.json")

from tvtuner import config, drivers, panel              # noqa: E402
from tvtuner.drivers import androidtv as A             # noqa: E402
from tvtuner.drivers.base import HOME_APP              # noqa: E402
from tvtuner.remote import PairingError                # noqa: E402
from tests import fake_androidtv                       # noqa: E402

IP = "127.0.0.1"
TV = SOCKS = OLD = None


def setUpModule():
    global TV, SOCKS, OLD
    OLD = (A.API_PORT, A.PAIR_PORT, A.CERT_DIR)
    A.API_PORT, A.PAIR_PORT = 16466, 16467
    A.CERT_DIR = tempfile.mkdtemp()
    TV, SOCKS = fake_androidtv.start(A.client_cert()[0])


def tearDownModule():
    A.API_PORT, A.PAIR_PORT, A.CERT_DIR = OLD
    for s in SOCKS:
        s.close()


class ProtoTest(unittest.TestCase):
    def test_protobuf_roundtrip(self):
        m = A.parse(A.pb((1, 300), (2, "olá"), (3, A.pb((1, 1)))))
        self.assertEqual((m[1][0], m[2][0].decode(), A.parse(m[3][0])[1][0]), (300, "olá", 1))

    def test_cert_name_and_mac(self):
        name, mac = A.name_and_mac(TV.server_der)
        self.assertEqual((name, mac), ("Chromecast", "AA:BB:CC:DD:EE:01"))


class AndroidTVTest(unittest.TestCase):
    def setUp(self):
        config.forget(IP)
        TV.paired.clear()
        TV.keys.clear(); TV.links.clear(); TV.text.clear()
        TV.on = True
        self.drv = drivers.DRIVERS["androidtv"](IP)

    def tearDown(self):
        self.drv.close()

    def pair(self):
        config.remember(IP, brand="androidtv")
        self.drv.pair_start()
        self.drv.pair_finish(TV.code)

    def test_probe_reads_name_from_certificate(self):
        d = A.AndroidTV.probe(IP)
        self.assertEqual((d["brand"], d["name"], d["mac"]), ("androidtv", "Chromecast", "AA:BB:CC:DD:EE:01"))

    def test_wrong_code_is_refused_locally(self):
        config.remember(IP, brand="androidtv")
        self.drv.pair_start()
        bad = ("00" if TV.code[:2] != "00" else "01") + TV.code[2:]
        with self.assertRaisesRegex(PairingError, "Código errado"):
            self.drv.pair_finish(bad)
        self.assertFalse(self.drv.paired())

    def test_unpaired_asks_for_pairing(self):
        with self.assertRaisesRegex(RuntimeError, "não pareada"):
            self.drv.key("KEY_UP")

    def test_pair_then_control(self):
        self.pair()
        self.assertTrue(self.drv.paired())
        d = self.drv.info()
        self.assertEqual((d["power"], d["model"]), ("on", "Chromecast"))
        for k in ("KEY_UP", "KEY_ENTER", "KEY_RETURN"):
            self.drv.key(k)
        self.drv.launch("com.netflix.ninja")
        self.drv.text("stranger")
        self.drv.launch(HOME_APP)
        time.sleep(0.3)
        self.assertEqual(TV.keys, [19, 23, 4, 3])
        self.assertEqual(TV.links, ["market://launch?id=com.netflix.ninja"])
        self.assertEqual(TV.text, ["stranger"])
        self.assertEqual(self.drv.current_app(self.drv.apps), {"appId": "com.netflix.ninja", "name": "Netflix"})

    def test_volume_mute_power(self):
        self.pair()
        self.drv.info()
        time.sleep(0.2)
        self.assertEqual((self.drv.volume(), self.drv.muted()), (TV.volume, False))
        self.drv.set_mute(True)
        time.sleep(0.2)
        self.assertTrue(self.drv.muted())
        self.drv.set_mute(True)                      # já está: não alterna de novo
        time.sleep(0.2)
        self.assertTrue(TV.muted)
        self.drv.power_off()
        time.sleep(0.2)
        self.assertEqual(self.drv.info()["power"], "standby")
        self.assertEqual(self.drv.power_on(), "key")

    def test_panel_two_step_pairing_and_live_volume(self):
        hub = panel.Hub(IP)
        config.remember(IP, brand="androidtv")
        self.assertEqual(hub.pair(IP), {"code": True})
        hub.pair(IP, TV.code)
        got = []
        tv = panel.TV(IP, emit=lambda k, d: got.append((k, d)))
        st = tv.snapshot()
        self.assertEqual((st["brand"], st["paired"], st["power"]), ("androidtv", True, "on"))
        TV.volume = 15
        TV.push_volume()
        time.sleep(0.3)
        self.assertEqual(tv.live["volume"], 15)
        tv.drv.close()


if __name__ == "__main__":
    unittest.main()
