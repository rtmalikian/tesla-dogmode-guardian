# Tesla Dog Mode Guardian — Backup Safety Alerts If Dog Mode Fails

**Free & open source Tesla Fleet API app that sends a backup safety alert to a second person when your Model 3 / Model Y / Model S / Model X / Cybertruck's cabin temperature drifts while Dog Mode is on, or the battery drops too low. Because one phone notification isn't enough.**

> ⚠️ **BACKUP NOTIFIER ONLY — NOT LIFE SUPPORT.** This script does not replace checking on your pet. The Tesla Fleet API can be down, your car can be unreachable (asleep, no signal), your phone can be off, and software can fail. **NEVER leave a pet unattended longer than you would without this script.** If you get an alert, go to the car immediately.

## Why?

- Tesla's Dog Mode is great — until the climate glitches, a door doesn't fully close, or the battery runs low. Your Tesla only notifies *you*, on *one* phone.
- Dog Mode deaths make the news every summer. A second set of eyes — your partner, a friend, the pet sitter — doubles the chance someone acts in time.
- Your second contact doesn't need a Tesla account or any app login: they just subscribe to a free ntfy.sh topic on their phone.

## How it works

Every 5 minutes the app reads your car's cabin temperature, Dog Mode state, and battery level (**without ever waking a sleeping car** — wakes are the priciest Fleet API call). While Dog Mode is on:

- If the cabin drifts more than `TCC_DOG_TEMP_DRIFT_C` (default 3°C) from the baseline — the temperature you set (`TCC_DOG_SETPOINT_C`), or the cabin temperature when Dog Mode first became active — it sends an **urgent push alert to your second contact** via free ntfy.sh.
- If the battery drops below `TCC_DOG_SOC_MIN` (default 20%) while Dog Mode is on — Dog Mode dies with the battery — it alerts too.
- Each condition alerts **exactly once per excursion**: no re-spam while it persists, and it re-arms after the condition clears so a second excursion alerts again.
- The app issues **zero commands** to the car. It only reads and alerts.

```
12:00  Parked, Dog Mode on, cabin 21.5°C     → nothing (baseline set)
12:40  Cabin drifts to 24.8°C (drift 3.3°C)  → ✅ URGENT alert to second contact
13:00  Still drifting                        → nothing (one alert per excursion)
13:20  Cabin recovers to 21.8°C              → nothing (alert re-armed)
13:40  Cabin drifts again to 25.0°C          → ✅ URGENT alert again
13:50  Battery drops to 18%                  → ✅ URGENT alert (Dog Mode dies with the battery)
14:10  You return, Dog Mode off              → nothing (tracking reset)
```

**Expected API cost:** ~$17.28/month per vehicle at the default 5-minute schedule IF left running 24/7 (288 no-wake data reads/day ≈ $0.58/day, zero commands, zero wakes; community-reported rates ~$1/500 data calls — re-verify against developer.tesla.com). **Run the daemon only while your pet is actually in the car** — a 1-hour errand costs about $0.02. The car is **never woken just to check Dog Mode**.

## Quickstart

```bash
git clone https://github.com/rtmalikian/tesla-dogmode-guardian.git
cd tesla-dogmode-guardian
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# 1. Configure (secrets stay local — .env is git-ignored)
cp .env.example .env
# edit .env: credentials, TCC_VEHICLES, TCC_NTFY_TOPIC

# 2. Authorize with your Tesla account (one-time)
tcc-dog auth

# 3. Install the virtual key on each car (one-time)
tcc-dog pairing

# 4. Watch it work on a fake Dog Mode afternoon — no API calls, no real cars
tcc-dog simulate

# 5. Single safe pass against your real cars
tcc-dog run --once

# 6. Run the guardian while your pet is in the car
tcc-dog run
```

## Tesla developer setup (one-time, ~15 minutes)

1. Register an app at [developer.tesla.com](https://developer.tesla.com) and get a client ID + secret.
2. Add scopes: `openid offline_access vehicle_device_data vehicle_location vehicle_cmds`.
3. `tcc-dog auth` — sign in with your Tesla account; tokens are stored at `~/.tcc/tokens.json` (mode 600).
4. `tcc-dog pairing` — host your public key, register the domain, approve it on each car's touchscreen at `https://www.tesla.com/_ak/<your-domain>`.
5. On the **second contact's phone**: install the [ntfy app](https://ntfy.sh) and subscribe to your `TCC_NTFY_TOPIC`. Pick an unguessable topic — it's the only secret.

## Configuration (`.env` reference)

| Variable | Default | Meaning |
|---|---|---|
| `TESLA_CLIENT_ID` / `TESLA_CLIENT_SECRET` | — | Your Tesla developer app credentials |
| `TESLA_REGION` | `na` | `na`, `eu`, or `cn` |
| `TESLA_REDIRECT_URI` | `http://localhost:8080/callback` | OAuth callback |
| `TESLA_TOKEN_FILE` | `~/.tcc/tokens.json` | Local token storage (mode 600) |
| `TCC_VEHICLES` | — | `name=VIN,...` — one entry per car |
| `TCC_DOG_TEMP_DRIFT_C` | `3.0` | Alert when cabin drifts more than this (°C) from baseline |
| `TCC_DOG_SOC_MIN` | `20` | Alert when battery drops below this (%) while Dog Mode is on |
| `TCC_DOG_SETPOINT_C` | — | Optional: the cabin target you set; baseline = first observed temp when empty |
| `TCC_NTFY_TOPIC` | — | ntfy.sh topic for urgent alerts to the second contact (required for pushes) |
| `TCC_STATE_FILE` | `~/.tcc/dogmode_guardian_state.json` | Local tracking state |
| `TCC_POLL_INTERVAL_SECONDS` | `300` | Check cadence; lower catches drift faster, costs more |
| `TCC_DRY_RUN` | `false` | Log what WOULD happen without sending alerts |

## Try before you connect: `tcc-dog simulate`

Runs a virtual Dog Mode afternoon against a fake car — warmup (no alert), a temp excursion (alert), sustained drift (no duplicate), recovery (re-arm), a second excursion (alert), a battery drop (alert), owner returns (reset). Prints the timeline, a PASS/FAIL verdict, and the estimated API cost. Zero credentials, zero network.

## FAQ

**Is this a substitute for checking on my pet?**
No — and it says so everywhere on purpose. This is a *backup* notifier: it adds a second phone to the loop in case Tesla's own notification misses you. It cannot guarantee delivery (API outages, dead zones, a sleeping car all make a pass get skipped). Check on your pet yourself, every time.

**Why does it alert a *second* contact instead of me?**
Tesla already notifies you. The failure mode this fixes is *you* missing it — phone on silent, dead battery, in a meeting. Your partner, friend, or pet sitter subscribes to the same free ntfy topic and gets the urgent alert too.

**How does it know the right cabin temperature?**
Set `TCC_DOG_SETPOINT_C` to what you dial in on the touchscreen, and drift is measured against that. Leave it empty and the baseline becomes the cabin temperature the first time Dog Mode is observed — fine for most use, since the cabin is usually already at the target when you walk away.

**Will it spam my second contact while the problem persists?**
No. Each condition alerts exactly once per excursion. If the cabin stays hot, you get one alert; if it recovers and drifts again, you get a second one. Failed push attempts are retried on the next pass, not swallowed.

**What if my car is asleep when Dog Mode is on?**
The pass is skipped and retried on the next poll — the app never wakes the car just to check. (Note: with Dog Mode active the car normally stays awake anyway.)

**Does it work with multiple cars?**
Yes — every vehicle in `TCC_VEHICLES` is tracked independently each pass.

## Development

```bash
pip install -e .          # install
PYTHONPATH=src .venv/bin/python -m pytest tests/ -q
PYTHONPATH=src .venv/bin/python -m tesla_dogmode_guardian.cli simulate
```

Decision logic lives in `src/tesla_dogmode_guardian/dog.py` — pure functions, `now` injected, no network. The Fleet API wrapper is `tesla_client.py` (read-only use of the official `vehicle_data` endpoint; the app sends no commands).

## License

MIT — Raphael Malikian.
