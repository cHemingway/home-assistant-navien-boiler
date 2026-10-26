"""Development CLI for probing the Navien Smart Plus cloud with your own account.

Credentials come from NAVIEN_EMAIL / NAVIEN_PASSWORD (environment or a gitignored .env in the repo root).
Raw replies are saved to research/captures/ with tokens removed.

    .venv/bin/python scripts/navien_cli.py probe
    .venv/bin/python scripts/navien_cli.py usage [--year 2026 --month 10]
    .venv/bin/python scripts/navien_cli.py listen --seconds 120
    .venv/bin/python scripts/navien_cli.py set-temp --zone 1 --temp 20.5 --yes
    .venv/bin/python scripts/navien_cli.py set-mode --zone 1 --mode auto --yes
    .venv/bin/python scripts/navien_cli.py set-dhw --mode on --yes
"""

import argparse
import asyncio
from dataclasses import asdict
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import sys
import time

import aiohttp

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "custom_components" / "navien_smartplus"))

from api import HeatingMode, HotWaterMode, NavienSmartPlusClient  # noqa: E402

CAPTURES = ROOT / "research" / "captures"
SECRET_KEYS = {
    "accessToken",
    "refreshToken",
    "idToken",
    "accessKeyId",
    "secretKey",
    "sessionToken",
    "password",
}


def load_env() -> tuple[str, str]:
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip().strip("'\""))
    try:
        return os.environ["NAVIEN_EMAIL"], os.environ["NAVIEN_PASSWORD"]
    except KeyError:
        sys.exit("Set NAVIEN_EMAIL and NAVIEN_PASSWORD (environment or .env)")


def redact(value):
    if isinstance(value, dict):
        return {
            k: ("<redacted>" if k in SECRET_KEYS else redact(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def save(name: str, data) -> Path:
    CAPTURES.mkdir(parents=True, exist_ok=True)
    path = CAPTURES / f"{time.strftime('%Y%m%d-%H%M%S')}-{name}.json"
    path.write_text(json.dumps(redact(data), indent=2, default=str))
    return path


def confirm(args, what: str) -> None:
    if not args.yes:
        sys.exit(f"Would {what}. Re-run with --yes to send it.")


async def run(args) -> None:
    email, password = load_env()
    async with aiohttp.ClientSession() as session:
        client = NavienSmartPlusClient(session, email, password)
        messages: list[dict] = []
        client.add_raw_listener(
            lambda topic, data: messages.append(
                {"t": time.time(), "topic": topic, "data": data}
            )
        )

        account = await client.login()
        print(f"Signed in: userSeq={account.user_seq} familySeq={account.family_seq}")
        devices = await client.async_get_devices()
        for d in devices:
            print(
                f"Device seq={d.device_seq} id={d.device_id} type={d.device_type} model={d.model_code} "
                f"connected={d.is_connected} zones={[(z.zone_id, z.name) for z in d.zones]}"
            )
        save("devices", [asdict(d) for d in devices])
        if not devices:
            return
        device = devices[0]

        await client.async_connect()
        print("MQTT connected")
        try:
            if args.cmd == "probe":
                features = await client.async_get_features(device)
                print("Features saved to", save("features", features.raw))
                schedules = await client.async_get_heating_schedules(device)
                print(
                    "Heating schedules saved to",
                    save("heat-schedule", {k: asdict(v) for k, v in schedules.items()}),
                )
                zone_ids = [z.zone_id for z in device.zones] or [1]
                for zone_id in zone_ids:
                    status = await client.async_get_status(device, zone_id)
                    print(
                        f"Status (zone {zone_id}) saved to",
                        save(f"status-zone{zone_id}", status.raw),
                    )
                    print(
                        f"  burner={status.burner_active} outside={status.outside_temperature} "
                        f"dhw_mode={status.dhw_mode} error={status.error_code}"
                    )
                    for z in status.zones.values():
                        print(
                            f"  zone {z.zone_id}: mode={z.mode!r} current={z.current_temperature} "
                            f"target={z.target_temperature(schedules.get(z.zone_id), datetime.now())} "
                            f"(setting field {z.setting_temperature}, program {z.program_number}) "
                            f"heating={z.heating_active}"
                        )
            elif args.cmd == "usage":
                today = datetime.now()
                year, month = args.year or today.year, args.month or today.month
                daily = await client.async_get_daily_usage(device, year, month)
                monthly = await client.async_get_monthly_usage(device, year)
                save(
                    f"usage-{year}-{month:02d}",
                    {
                        "daily": [asdict(r) for r in daily],
                        "monthly": [asdict(r) for r in monthly],
                    },
                )
                print(
                    f"Daily {year}-{month:02d} (m³ heating / hot water, hours heating / hot water):"
                )
                for r in daily:
                    if r.total_usage or r.heating_hours or r.hot_water_hours:
                        print(
                            f"  {r.date}  {r.heating_usage:3d} / {r.hot_water_usage:3d} m³   "
                            f"{r.heating_hours:3d} / {r.hot_water_hours:3d} h"
                        )
                print(
                    f"  month so far: {sum(r.heating_usage for r in daily)} / "
                    f"{sum(r.hot_water_usage for r in daily)} m³"
                )
                print(f"Monthly {year}:")
                for r in monthly:
                    print(
                        f"  {r.year}-{r.month:02d}  {r.heating_usage:3d} / {r.hot_water_usage:3d} m³   "
                        f"{r.heating_hours:3d} / {r.hot_water_hours:3d} h"
                    )
                print(
                    f"  year so far: {sum(r.heating_usage for r in monthly)} / "
                    f"{sum(r.hot_water_usage for r in monthly)} m³, "
                    f"total {sum(r.total_usage for r in monthly)} m³"
                )
            elif args.cmd == "listen":
                print(
                    f"Listening for {args.seconds}s; change something in the app or on the thermostat now"
                )
                await asyncio.sleep(args.seconds)
            elif args.cmd == "set-temp":
                status = await client.async_get_status(device, args.zone)
                zone = status.zones.get(args.zone)
                if zone is None:
                    sys.exit(f"Zone {args.zone} not in status")
                current = zone.target_temperature(
                    (await client.async_get_heating_schedules(device)).get(args.zone),
                    datetime.now(),
                )
                confirm(
                    args,
                    f"set zone {args.zone} from {current} to {args.temp} (mode {zone.mode!r})",
                )
                result = await client.async_set_zone_temperature(
                    device, args.zone, args.temp, zone.mode
                )
                print(
                    "Reply status saved to",
                    save("set-temp", result.raw if result else {}),
                )
            elif args.cmd == "set-mode":
                mode = HeatingMode[args.mode.upper()]
                status = await client.async_get_status(device, args.zone)
                zone = status.zones.get(args.zone)
                temp = args.temp or (zone.setting_temperature if zone else 20.0)
                confirm(args, f"set zone {args.zone} mode {mode!r} at {temp}")
                result = await client.async_set_zone_mode(device, args.zone, mode, temp)
                print(
                    "Reply status saved to",
                    save("set-mode", result.raw if result else {}),
                )
            elif args.cmd == "set-dhw":
                mode = HotWaterMode[args.mode.upper()]
                confirm(args, f"set hot water mode {mode!r}")
                result = await client.async_set_hot_water_mode(device, mode)
                print(
                    "Reply status saved to",
                    save("set-dhw", result.raw if result else {}),
                )
        finally:
            await asyncio.sleep(2)  # collect trailing pushes
            print(
                f"{len(messages)} MQTT messages saved to",
                save(f"messages-{args.cmd}", messages),
            )
            await client.async_close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("probe")
    p = sub.add_parser("usage", help="gas usage history (read-only)")
    p.add_argument("--year", type=int)
    p.add_argument("--month", type=int)
    p = sub.add_parser("listen")
    p.add_argument("--seconds", type=int, default=60)
    p = sub.add_parser("set-temp")
    p.add_argument("--zone", type=int, default=1)
    p.add_argument("--temp", type=float, required=True)
    p.add_argument("--yes", action="store_true")
    p = sub.add_parser("set-mode")
    p.add_argument("--zone", type=int, default=1)
    p.add_argument("--mode", choices=["auto", "on", "off", "boost"], required=True)
    p.add_argument("--temp", type=float)
    p.add_argument("--yes", action="store_true")
    p = sub.add_parser("set-dhw")
    p.add_argument("--mode", choices=["auto", "on", "off", "once"], required=True)
    p.add_argument("--yes", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(name)s %(message)s",
    )
    if not args.debug:
        logging.getLogger("aiomqtt").setLevel(logging.WARNING)
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
