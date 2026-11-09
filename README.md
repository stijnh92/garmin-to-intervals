# Garmin to Intervals.icu

Sync planned Trenara runs from Garmin Connect to the Intervals.icu calendar. The CLI reads Garmin's workout library and nearby scheduled workouts, then upserts structured **Run** workouts.

## Setup on Linux

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), copy this directory to your server, then run these commands from the project directory.

```sh
uv sync --locked
install -m 600 .env.example .env
```

Fill in `.env` on the server with your Garmin Connect email and password, and the athlete ID and API key from Intervals.icu settings. For credentials containing spaces or `#`, use quotes around the value. Exported environment variables take precedence over the file.

```sh
uv run --locked garmin-to-intervals --dry-run
uv run --locked garmin-to-intervals
```

The dry run reads Garmin and prints the calendar dates and workout steps without writing to Intervals.icu. Run the first preview in an interactive terminal so you can enter a Garmin MFA code if prompted. The CLI reuses Garmin tokens stored in `.garmin-tokens/` beside the env file. Use `--env-file /path/to/.env` to select a different configuration, or set `GARMIN_TOKEN_DIR` to change the token directory.

To run every morning at 06:00 in the server's timezone, add an entry like this using `crontab -e`. Replace both paths with the ones on your server. Run the interactive preview first as the same Linux user.

```cron
0 6 * * * cd /path/to/garmin-to-intervals && /home/you/.local/bin/uv run --locked garmin-to-intervals >> sync.log 2>&1
```

## What gets synced

- Running workouts whose Garmin names start with `DD/MM ` are treated as Trenara exports. Other running workouts named the same way also match.
- The CLI scans the full Garmin workout library and scheduled workouts within 31 days on either side of today. Scheduled entries use Garmin's calendar date. For library entries, the year comes from the nearest matching day and month to the Garmin creation date, falling back to the modification date or today if needed.
- The preview and sync include workouts planned for today or later, using Garmin's local date.
- Intervals.icu workouts start at 12:00 local time and use the title without the `DD/MM` prefix.
- Time, distance, warm-up, cool-down, recovery, rest, repeat blocks, pace ranges, and supported zone and cadence targets are translated to [Intervals.icu workout builder text](https://forum.intervals.icu/t/workout-builder/1163). Nested repeats are expanded inside their parent block. Open laps use a one-second placeholder for Intervals.icu's duration estimate. When a step cannot be expressed in builder text, such as a custom absolute heart-rate range, the CLI attempts to upload Garmin's FIT workout instead.
- The resolved date and normalized workout title identify an event. A replacement Garmin workout with the same date and title updates the same Intervals.icu event. When both sources list that workout, the scheduled entry takes precedence. A new date or title creates a separate event. The CLI leaves earlier events on the calendar.
- Conversion and download failures are reported per workout. The CLI still syncs the others and exits with status 1 so scheduled jobs can detect a partial failure. Rerunning after an upload failure uses the same [Intervals.icu upsert keys](https://forum.intervals.icu/t/uploading-planned-workouts-to-intervals-icu/63624).

## Tests

```sh
uv run --locked python -m unittest discover -s tests -v
```
