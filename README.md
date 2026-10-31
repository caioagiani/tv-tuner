# TV Tuner

<img src="docs/screens/remote.png" width="230" align="right" alt="Phone remote">

Phone remote, voice control and an ad-free home screen for Samsung Tizen, LG webOS, Roku and Android TV / Google TV.
Runs on your home network. No cloud, no store app, no ads.

- **Remote:** D-pad with joystick drag, hold-to-repeat volume, app logos, live state.
- **Voice:** hold the mic and speak ("open netflix", "volume 20", "search stranger things"). Local transcription with whisper.
- **Keyboard:** when the TV opens a text field, your phone opens one too.
- **Multi-TV:** discover, pair, switch. Apps and volume work even without pairing.
- **Doctor:** checks network, pairing, developer mode, app certificate and ad blocking.
- **Home screen:** Tizen app with clock, favorites and HDMI inputs.

<br clear="right">

## Set up with AI

Paste this into Claude Code (or any agent with a terminal) inside this folder:

```text
Set up TV Tuner on this computer for my Samsung TV.

1. Install dependencies: pip3 install -r requirements.txt; whisper.cpp and ffmpeg (brew).
2. Run ./tv discover and ./tv pair. I'll accept "Allow" on the TV.
3. Run ./tv doctor, explain each warning and fix what can be fixed automatically.
4. Run the panel as a service: ./tv service install --pin <pick a 4-digit PIN>.
   Download the whisper large-v3-turbo q5_0 model into ~/.config/tv-tuner/whisper/.
5. Give me the panel's https URL to open on my phone, on the same network.
6. Optional: install the home screen (tizen/README.md) and walk me through the TV's Developer Mode.

Rules: never open router ports; token, PIN and the TV's label never go into git.
```

## Manual

```sh
pip3 install -r requirements.txt
./tv discover && ./tv pair
./tv doctor
./tv panel --pin 1234        # http://<ip>:8780 · https://<ip>:8743 (mic needs https)
./tv service install --pin 1234
```

`./tv --help` lists the rest: keys, macros, apps, text, browser, power, checklist.

## Supported TVs

| Brand | Status | Protocol | Notes |
|---|---|---|---|
| Samsung Tizen (2016+) | tested (UN50AU7700) | websocket 8002, REST 8001, UPnP 9197, SDB 26101 | pairing once; home screen app needs Developer Mode (2023+ needs a Samsung certificate) |
| Samsung 2010–2013 (C/D/E/F) | experimental | legacy TCP 55000 | keys only (no apps, no volume readout, no power on) |
| LG webOS (2014+) | experimental | SSAP websocket 3000/3001 | pairing once; power on needs *Mobile TV On* / Wake-on-LAN |
| Android TV / Google TV (Sony, TCL, Philips, Chromecast, Shield...) | experimental | Android TV Remote v2, TLS 6466/6467 | pairing with a 6-character code shown on the TV; apps open by package; volume reported, not set |
| Roku TV / Roku players | experimental | ECP, HTTP 8060 | no pairing; set *Control by mobile apps* to Default; no absolute volume |

Each brand is a driver in `tvtuner/drivers/` declaring what it can do (`caps`); the phone hides the rest.
New brand = one file. `python3 -m unittest discover -s tests -t .` runs against fake TVs (`tests/fake_*.py`).

## DNS

`dns/blocklist.txt` (safe) and `dns/blocklist-agressivo.txt` (may break the store). Use with NextDNS, Pi-hole or AdGuard Home.

## Credits

SDB installer based on [free-my-tv](https://github.com/simbasjungles/free-my-tv) (MIT).
Tizen notes from [cleanyourtv](https://github.com/Gathin23/cleanyourtv).
Aggressive list from [Perflyst/PiHoleBlocklist](https://github.com/Perflyst/PiHoleBlocklist).
Legacy Samsung protocol from [samsungctl](https://github.com/Ape/samsungctl) (MIT).
Android TV protocol checked against [androidtvremote2](https://github.com/tronikos/androidtvremote2) (Apache-2.0).
LG protocol checked against [aiowebostv](https://github.com/home-assistant-libs/aiowebostv) (Apache-2.0).
Icons from [Simple Icons](https://simpleicons.org).

## License

MIT
