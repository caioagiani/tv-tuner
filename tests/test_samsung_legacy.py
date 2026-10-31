"""Samsung 2010–2013 (porta 55000) contra a TV falsa."""
import os
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("TVTUNER_CONFIG", os.path.join(tempfile.mkdtemp(), "config.json"))

from tvtuner import config, drivers                    # noqa: E402
from tvtuner.drivers import samsung_legacy as L        # noqa: E402
from tvtuner.drivers.base import Unsupported           # noqa: E402
from tvtuner.remote import PairingError                # noqa: E402
from tests import fake_samsung_legacy                  # noqa: E402

IP = "127.0.0.1"
TV = SRV = OLD = None


def setUpModule():
    global TV, SRV, OLD
    OLD, L.PORT = L.PORT, 15500
    TV, SRV = fake_samsung_legacy.start(IP, L.PORT)


def tearDownModule():
    L.PORT = OLD
    SRV.close()


class LegacyTest(unittest.TestCase):
    def setUp(self):
        config.forget(IP)
        TV.allow, TV.keys = True, []
        self.drv = drivers.DRIVERS["samsung_legacy"](IP)

    def tearDown(self):
        self.drv.close()

    def test_probe(self):
        self.assertEqual(L.SamsungLegacy.probe(IP)["brand"], "samsung_legacy")

    def test_refusal_and_unpaired(self):
        TV.allow = False
        with self.assertRaises(PairingError):
            self.drv.pair(5)
        with self.assertRaisesRegex(RuntimeError, "não pareada"):
            self.drv.key("KEY_UP")

    def test_pair_and_keys(self):
        config.remember(IP, brand="samsung_legacy")
        self.drv.pair(5)
        self.drv.key("KEY_UP"); self.drv.key("enter"); self.drv.set_mute(True); self.drv.power_off()
        time.sleep(0.1)
        self.assertEqual(TV.keys, ["KEY_UP", "KEY_ENTER", "KEY_MUTE", "KEY_POWEROFF"])
        with self.assertRaises(Unsupported):
            self.drv.launch("netflix")


if __name__ == "__main__":
    unittest.main()
