"""Command line entry point and API I/O."""

import argparse
import base64
import fcntl
import os
import re
import sys
from datetime import date
from pathlib import Path
from urllib.parse import quote

from .sync import RUN_SPORTS, discover
from .workouts import UnsupportedStep, workout_text


def required_env(name):
    value = os.getenv(name)
    if not value or not value.strip():
        raise ValueError(f"set {name} in the environment or the selected .env file")
    return value


def prompt_mfa():
    if not sys.stdin.isatty():
        raise ValueError("Garmin MFA requires a terminal. Run the CLI interactively to refresh login.")
    return input("Garmin MFA code: ").strip()


def upload(session, athlete, events):
    import requests

    url = f"https://intervals.icu/api/v1/athlete/{quote(athlete, safe='')}/events/bulk"
    try:
        response = session.post(url, params={"upsert": "true"}, json=events, timeout=30)
        response.raise_for_status()
        result = response.json()
    except requests.HTTPError as exc:
        raise ValueError(f"Intervals.icu returned HTTP {exc.response.status_code}") from exc
    except (requests.RequestException, ValueError) as exc:
        raise ValueError("Intervals.icu request failed or returned invalid JSON") from exc

    expected = {event["external_id"] for event in events}
    if not isinstance(result, list) or {
        item.get("external_id") for item in result if isinstance(item, dict) and item.get("id")
    } != expected:
        raise ValueError("Intervals.icu did not confirm every event. Rerun to retry the upserts.")
    if any(not (item.get("workout_doc") or {}).get("steps") for item in result):
        raise ValueError("Intervals.icu saved an event without structured steps. Review the calendar.")


def encode_workout(garmin, workout, detail):
    event = workout.event()
    try:
        preview = workout_text(detail)
        event["description"] = preview
    except UnsupportedStep as exc:
        try:
            data = garmin.download_workout(workout.workout_id)
        except Exception as download_error:
            raise ValueError(f"{exc}. Garmin FIT download failed ({type(download_error).__name__})") from download_error
        if not isinstance(data, bytes) or data[8:12] != b".FIT":
            raise ValueError(f"{exc}. Garmin returned an invalid FIT file") from None
        event["filename"] = f"trenara-{workout.workout_id}.fit"
        event["file_contents_base64"] = base64.b64encode(data).decode("ascii")
        preview = f"Garmin FIT import ({exc})"
    return event, preview


def sync(garmin, *, dry_run, athlete, key):
    import requests

    today = garmin.today() if hasattr(garmin, "today") else date.today()
    events = []
    failures = 0
    for workout in discover(garmin, today):
        try:
            detail = garmin.get_workout_by_id(workout.workout_id)
        except Exception as exc:
            print(f"Garmin workout {workout.workout_id}: fetch failed ({type(exc).__name__})", file=sys.stderr)
            failures += 1
            continue

        try:
            sport = detail["sportType"]["sportTypeKey"]
            if sport.lower() not in RUN_SPORTS:
                continue
            event, preview = encode_workout(garmin, workout, detail)
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            print(f"{workout.name} (Garmin {workout.workout_id}): {exc}", file=sys.stderr)
            failures += 1
            continue

        events.append(event)
        if dry_run:
            date_and_time = event["start_date_local"].replace("T", " ")[:16]
            print(f"{date_and_time}  {event['name']}\n{preview}\n")
        elif "filename" in event:
            print(f"{workout.day}  {workout.name}: {preview}", file=sys.stderr)

    if dry_run:
        print(f"Previewed {len(events)} workout(s).")
    elif events:
        with requests.Session() as session:
            session.auth = ("API_KEY", key)
            for offset in range(0, len(events), 50):
                upload(session, athlete, events[offset:offset + 50])
        print(f"Synced {len(events)} workout(s) to Intervals.icu.")
    else:
        print("No matching workouts to sync.")
    if failures:
        print(f"Skipped {failures} workout(s) with errors.", file=sys.stderr)
    return int(bool(failures))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Sync Trenara running workouts to Intervals.icu.")
    parser.add_argument("--dry-run", action="store_true", help="print workouts without writing to Intervals.icu")
    parser.add_argument("--env-file", type=Path, default=Path(".env"), help="configuration file (default: .env)")
    args = parser.parse_args(argv)

    from dotenv import load_dotenv
    from garminconnect import Garmin

    try:
        if args.env_file != Path(".env") and not args.env_file.is_file():
            raise ValueError("the selected .env file does not exist")
        load_dotenv(args.env_file, interpolate=False)
        email = required_env("GARMIN_EMAIL")
        password = required_env("GARMIN_PASSWORD")
        athlete = key = None
        if not args.dry_run:
            athlete = required_env("INTERVALS_ATHLETE_ID")
            if not re.fullmatch(r"i?[0-9]+", athlete):
                raise ValueError("INTERVALS_ATHLETE_ID must be a numeric ID, optionally prefixed with i")
            key = required_env("INTERVALS_API_KEY")

        default_tokens = args.env_file.resolve().parent / ".garmin-tokens"
        tokens = Path(os.getenv("GARMIN_TOKEN_DIR") or default_tokens).expanduser()
        tokens.mkdir(mode=0o700, parents=True, exist_ok=True)
        with (tokens / "sync.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError("another sync is already using this Garmin token directory") from None
            garmin = Garmin(email, password, prompt_mfa=prompt_mfa)
            garmin.login(str(tokens))
            return sync(garmin, dry_run=args.dry_run, athlete=athlete, key=key)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
    except Exception as exc:
        print(f"Sync failed ({type(exc).__name__}). Check Garmin login, connectivity and token directory.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
