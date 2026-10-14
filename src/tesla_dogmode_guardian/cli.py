"""CLI: tcc-dog auth | status | run | once | simulate | pairing

⚠️  DISCLAIMER (also shown by `run` and `simulate`): tcc-dog is a BACKUP
notifier only — not life support. It does not replace checking on your
pet. NEVER leave a pet unattended longer than you would without it.
"""
from __future__ import annotations

import argparse
import logging
import sys
import webbrowser
from datetime import datetime

from dotenv import load_dotenv

from . import __version__
from .config import ConfigError, Settings, VehicleConfig, load_settings
from .dog import DogDecider
from .scheduler import DISCLAIMER, build_states, load_decider, run_loop, run_once
from .simulator import ClimateEvent, FakeVehicle, run_simulation
from .tesla_client import TeslaApiError, TeslaClient

log = logging.getLogger("tcc-dog")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")


def _client(settings: Settings) -> TeslaClient:
    return TeslaClient(client_id=settings.client_id, client_secret=settings.client_secret,
                       region=settings.region, redirect_uri=settings.redirect_uri,
                       token_file=settings.token_file)


def cmd_auth(args, settings: Settings) -> int:
    client = _client(settings)
    url = client.authorize_url()
    print("1. Open this URL and sign in with your Tesla account:\n\n   %s\n" % url)
    try:
        webbrowser.open(url)
    except Exception:
        pass
    print("2. After sign-in you'll land on a localhost URL that fails to load.")
    print("   Paste that FULL redirected URL here (it contains ?code=...).")
    redirected = input("Redirected URL: ").strip()
    if "code=" not in redirected:
        print("ERROR: no ?code= found.", file=sys.stderr)
        return 1
    try:
        client.exchange_code(redirected.split("code=")[1].split("&")[0])
    except TeslaApiError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"\nAuthorized! Tokens saved to {settings.token_file} (mode 600).")
    print("Next: run `tcc-dog pairing` for the one-time virtual-key setup.")
    return 0


def cmd_pairing(_args, _settings: Settings) -> int:
    print(
        "One-time virtual-key setup (required before commands work):\n\n"
        "  1. Host your app's public key at\n"
        "     https://<your-domain>/.well-known/appspecific/com.tesla.3p.public-key.pem\n"
        "  2. Register it: POST /api/1/partner_accounts {\"domain\": \"<your-domain>\"}\n"
        "  3. In EACH Tesla, open https://www.tesla.com/_ak/<your-domain>\n"
        "     and approve the key on the touchscreen.\n"
    )
    return 0


def cmd_status(_args, settings: Settings) -> int:
    client = _client(settings)
    states = build_states(client, settings)
    decider = load_decider(settings)
    print(f"{'Vehicle':<12}{'Dog':<6}{'Temp':<9}{'SoC':<7}{'Baseline':<10}")
    print("-" * 46)
    for s in states:
        t = decider._tracked(s.vin)
        base = f"{t.baseline_c:.1f}°C" if t.baseline_c is not None else "—"
        temp = f"{s.inside_temp_c:.1f}°C" if s.inside_temp_c is not None else "?"
        soc = f"{s.soc:.0f}%" if s.soc is not None else "?"
        print(f"{s.name:<12}{'ON' if s.dog_on else 'off':<6}{temp:<9}{soc:<7}{base:<10}")
    print(f"\nAlert thresholds: drift > {settings.dog_temp_drift_c}°C, "
          f"SoC < {settings.dog_soc_min:g}%")
    print(f"ntfy: {'set' if settings.ntfy_topic else 'NOT SET — alerts are logged only'}")
    return 0


def cmd_run(args, settings: Settings) -> int:
    print(f"\n⚠️  {DISCLAIMER}\n")
    if args.once:
        run_once(_client(settings), load_decider(settings), settings)
        return 0
    run_loop(settings)
    return 0


def cmd_simulate(_args, _settings: Settings) -> int:
    """Demo afternoon: Dog Mode on with a 21.5°C baseline — warmup, excursion,
    sustained drift, recovery, second excursion, battery drop, owner returns."""
    print(f"\n⚠️  {DISCLAIMER}\n")
    start = datetime.now().astimezone().replace(hour=12, minute=0, second=0, microsecond=0)
    car = FakeVehicle(
        vin="SIM1", name="Model Y", parked=True,
        keeper_mode="Dog", inside_temp=21.5, soc=65.0,
        script=[
            ClimateEvent(at_minute=25, keeper="Dog", temp=22.8, soc=64.0),   # drift 1.3 — ok
            ClimateEvent(at_minute=40, keeper="Dog", temp=24.8, soc=63.0),   # drift 3.3 — alert #1
            ClimateEvent(at_minute=60, keeper="Dog", temp=25.5, soc=62.0),   # still drifting — no dup
            ClimateEvent(at_minute=80, keeper="Dog", temp=21.8, soc=61.0),   # recovered — re-arm
            ClimateEvent(at_minute=100, keeper="Dog", temp=25.0, soc=60.0),  # drift 3.5 — alert #2
            ClimateEvent(at_minute=110, keeper="Dog", temp=25.0, soc=18.0),  # battery — alert #3
            ClimateEvent(at_minute=130, keeper=None, temp=24.0, soc=17.0),   # owner returns, Dog off
        ],
    )
    settings = Settings(
        vehicles=[VehicleConfig(name="Model Y", vin="SIM1")],
        poll_interval_seconds=300,
        ntfy_topic="",  # simulate logs the alert instead of pushing
    )
    decider = DogDecider(settings)
    report = run_simulation([car], decider, settings, start, hours=4.0, tick_minutes=5.0)

    print("Simulating a Dog Mode afternoon — Model Y parked at 12:00, baseline 21.5°C\n")
    print(f"{'Time':<8}{'Dog':<5}{'Temp':<8}{'SoC':<7}Note")
    print("-" * 80)
    last = None
    for tick in report["timeline"]:
        key = (tick["dog"]["Model Y"], tick["temp"]["Model Y"],
               tick["soc"]["Model Y"], tick["note"])
        if key != last:
            print(f"{tick['time']:<8}{tick['dog']['Model Y']:<5}"
                  f"{tick['temp']['Model Y'] if tick['temp']['Model Y'] is not None else '?':<8}"
                  f"{tick['soc']['Model Y'] if tick['soc']['Model Y'] is not None else '?':<7}"
                  f"{tick['note']}")
            last = key
    alerts = report["alerts"]
    kinds = [a[2] for a in alerts]
    times = [a[0] for a in alerts]
    ok = (kinds == ["temp", "temp", "soc"] and len(alerts) == 3
          and report["wakes"] == 0)
    print(f"\nExactly 3 alerts (temp excursion → alert, no duplicate while drifting, "
          f"re-arm after recovery → alert again, battery drop → alert): "
          f"{'yes' if ok else 'NO'} ({'PASS' if ok else 'FAIL — bug!'})")
    print(f"Alert times: {', '.join(times)}")
    print(f"No-wake reads in sim: {report['reads']}, wakes: {report['wakes']} "
          f"(car is never woken just to check Dog Mode)")

    cost = report["cost"]
    print(f"\nEstimated API cost (community-reported rates ~$1/500 data calls; "
          f"re-verify against developer.tesla.com): "
          f"~{cost['daily_usd']:.3f}/day "
          f"(~${cost['monthly_usd']:.2f}/month) per vehicle at the default "
          f"{settings.poll_interval_seconds // 60}-minute schedule, IF left running 24/7 — "
          f"{cost['reads_per_day']:.0f} no-wake reads/day, 0 commands, 0 wakes. "
          f"Run the daemon only while your pet is actually in the car "
          f"(a 1-hour errand ≈ $0.02).")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tcc-dog",
                                description="Backup Dog Mode safety alerts to a "
                                            "second contact. BACKUP NOTIFIER ONLY — "
                                            "not life support.")
    p.add_argument("--version", action="version", version=f"tcc-dog {__version__}")
    p.add_argument("--env-file", default=None)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("auth", help="Authorize with your Tesla account (one-time)")
    sub.add_parser("pairing", help="Virtual-key pairing instructions")
    sub.add_parser("status", help="Show Dog Mode state + tracking per vehicle")
    run_p = sub.add_parser("run", help="Run the guardian loop (daemon)")
    run_p.add_argument("--once", action="store_true", help="Single pass, then exit")
    sub.add_parser("simulate", help="Demo against a fake fleet (no API calls)")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    load_dotenv(args.env_file)
    try:
        settings = load_settings(require_auth=args.command in ("auth", "status", "run"))
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2
    return {
        "auth": cmd_auth, "pairing": cmd_pairing, "status": cmd_status,
        "run": cmd_run, "simulate": cmd_simulate,
    }[args.command](args, settings)


if __name__ == "__main__":
    raise SystemExit(main())
