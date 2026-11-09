"""Discover matching Garmin workouts and build stable calendar event payloads."""

import hashlib
import re
from dataclasses import dataclass
from datetime import date, timedelta

NAME_PATTERN = re.compile(r"(?P<day>[0-9]{2})/(?P<month>[0-9]{2})\s+(?P<title>.+)")
RUN_SPORTS = {"run", "running"}


@dataclass(frozen=True)
class Workout:
    workout_id: str
    day: date
    name: str
    title: str
    scheduled: bool
    modified: str

    @property
    def external_id(self):
        key = f"{self.day.isoformat()} {' '.join(self.title.casefold().split())}"
        digest = hashlib.sha256(key.encode()).hexdigest()[:32]
        return f"trenara-garmin-{digest}"

    def event(self):
        return {
            "category": "WORKOUT",
            "type": "Run",
            "name": self.title,
            "start_date_local": f"{self.day.isoformat()}T12:00:00",
            "external_id": self.external_id,
        }


def parse_date(value):
    return date.fromisoformat(value[:10]) if value else None


def nearest_date(match, reference):
    dates = []
    for year in range(max(1, reference.year - 4), min(9999, reference.year + 4) + 1):
        try:
            dates.append(date(year, int(match["month"]), int(match["day"])))
        except ValueError:
            continue
    return min(dates, key=lambda day: abs(day - reference), default=None)


def candidate(row, today, *, scheduled=False):
    name = (row.get("workoutName") or "").strip()
    match = NAME_PATTERN.fullmatch(name)
    if not match or not row.get("workoutId"):
        return None
    sport = (row.get("sportType") or {}).get("sportTypeKey") or row.get("workoutType")
    if sport and sport.lower() not in RUN_SPORTS:
        return None
    if not scheduled and not sport:
        return None
    modified = row.get("updatedDate") or row.get("createdDate") or ""
    if scheduled:
        day = parse_date(row.get("scheduleDate") or row.get("calendarDate") or row.get("date"))
    else:
        reference = parse_date(row.get("createdDate") or modified) or today
        day = nearest_date(match, reference)
    if day is None:
        return None
    return Workout(str(row["workoutId"]), day, name, match["title"], scheduled, modified)


def library_rows(garmin):
    start = 0
    seen = set()
    while page := garmin.get_workouts(start=start, limit=100):
        ids = {row["workoutId"] for row in page}
        if ids <= seen:
            raise ValueError("Garmin returned a repeated workout page")
        seen.update(ids)
        yield from page
        start += len(page)


def discover(garmin, today):
    start = (today - timedelta(days=31)).isoformat()
    end = (today + timedelta(days=31)).isoformat()
    scheduled = garmin.get_scheduled_workouts_by_period(start, end) or []
    candidates = [candidate(row, today, scheduled=True) for row in scheduled]
    scheduled_ids = {workout.workout_id for workout in candidates if workout}
    for row in library_rows(garmin):
        workout = candidate(row, today)
        if workout and workout.workout_id not in scheduled_ids:
            candidates.append(workout)

    winners = {}
    for workout in sorted(
        (workout for workout in candidates if workout and workout.day >= today),
        key=lambda item: (item.scheduled, item.modified, int(item.workout_id)),
    ):
        winners[workout.external_id] = workout
    return sorted(winners.values(), key=lambda item: (item.day, item.name))
