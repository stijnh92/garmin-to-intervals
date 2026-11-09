import io
import unittest
from contextlib import redirect_stdout
from datetime import date
from unittest.mock import Mock, patch

from garmin_to_intervals.cli import sync
from garmin_to_intervals.sync import discover
from garmin_to_intervals.workouts import workout_text


RUN = {"sportTypeKey": "running"}


def step(kind, duration, *, condition="time", **extra):
    return {
        "stepType": {"stepTypeKey": kind},
        "endCondition": {"conditionTypeKey": condition},
        "endConditionValue": duration,
        **extra,
    }


def detail(*steps):
    return {"sportType": RUN, "workoutSegments": [{"workoutSteps": steps}]}


class WorkoutTests(unittest.TestCase):
    def test_renders_structured_pace_repeats_in_order(self):
        repeat = {
            "type": "RepeatGroupDTO",
            "numberOfIterations": 4,
            "stepOrder": 2,
            "workoutSteps": [
                step("interval", 80, condition="distance",
                     description="Run 80m in 00:14 (02:56 min/km)",
                     targetType={"workoutTargetTypeKey": "speed.zone"},
                     targetValueOne=1000 / 186, targetValueTwo=1000 / 166),
                step("recovery", 210),
            ],
        }
        workout = detail(step("cooldown", 30, stepOrder=3), repeat,
                         step("warmup", 1200, stepOrder=1))
        self.assertEqual(
            workout_text(workout),
            "Warmup\n"
            "- Warm Up 20m intensity=warmup\n\n"
            "Main set 4x\n"
            "- Run 0.08km 2:46-3:06 Pace intensity=active\n"
            "- Rest 3m 30s intensity=recovery\n\n"
            "Cooldown\n"
            "- Cool Down 30s intensity=cooldown",
        )

    def test_matches_schedule_and_handles_library_year_rollover_and_pagination(self):
        garmin = Mock()
        garmin.get_workouts.side_effect = [
            [
                {"workoutId": 1, "workoutName": "02/01 LSD", "sportType": RUN,
                 "createdDate": "2026-12-28T10:00:00"},
                {"workoutId": 2, "workoutName": "30/12 Tempo", "sportType": RUN,
                 "createdDate": "2027-01-01"},
            ],
            [
                {"workoutId": 3, "workoutName": "Easy run", "sportType": RUN},
                {"workoutId": 6, "workoutName": "28/12 LSD", "sportType": RUN},
                {"workoutId": 7, "workoutName": "31/12 Easy", "sportType": RUN},
                {"workoutId": 8, "workoutName": "29/12 Strides", "sportType": RUN},
            ],
            [],
        ]
        garmin.get_scheduled_workouts_by_period.return_value = [
            {"workoutId": 4, "workoutName": "02/01 LSD", "workoutType": "RUNNING",
             "scheduleDate": "2027-01-02"},
            {"workoutId": 5, "workoutName": "02/01 Easy", "workoutType": "cycling",
             "scheduleDate": "2027-01-02"},
            {"workoutId": 7, "workoutName": "31/12 Easy", "workoutType": "running",
             "scheduleDate": "2026-12-28"},
        ]

        results = discover(garmin, date(2026, 12, 29))
        self.assertEqual(
            [(item.workout_id, item.day) for item in results],
            [("8", date(2026, 12, 29)), ("2", date(2026, 12, 30)), ("4", date(2027, 1, 2))],
        )
        self.assertEqual([call.kwargs["start"] for call in garmin.get_workouts.call_args_list], [0, 2, 6])

    @patch("requests.Session")
    def test_replacement_and_retry_use_same_upsert_key(self, session_class):
        garmin = Mock()
        garmin.today.return_value = date(2026, 11, 9)
        garmin.get_scheduled_workouts_by_period.return_value = []
        stored = {}
        session = session_class.return_value.__enter__.return_value

        def post(url, *, params, json, timeout):
            self.assertEqual(url, "https://intervals.icu/api/v1/athlete/i123/events/bulk")
            self.assertEqual(params, {"upsert": "true"})
            self.assertEqual(session.auth, ("API_KEY", "test-key"))
            response = Mock()
            for event in json:
                stored[event["external_id"]] = event
            response.json.return_value = [
                dict(event, id=1, workout_doc={"steps": [{"duration": 720}]}) for event in json
            ]
            return response

        session.post.side_effect = post

        with redirect_stdout(io.StringIO()):
            for workout_id, duration in [(1, 600), (2, 720), (2, 720)]:
                garmin.get_workouts.side_effect = [[{
                    "workoutId": workout_id, "workoutName": "11/11 Easy run", "sportType": RUN,
                    "createdDate": "2026-11-09",
                }], []]
                garmin.get_workout_by_id.return_value = detail(step("main", duration))
                self.assertEqual(sync(garmin, dry_run=False, athlete="i123", key="test-key"), 0)

        self.assertEqual(len(stored), 1)
        event = next(iter(stored.values()))
        self.assertEqual(event["start_date_local"], "2026-11-11T12:00:00")
        self.assertEqual(event["name"], "Easy run")
        self.assertEqual(event["type"], "Run")
        self.assertTrue(event["description"].startswith("Main set\n"))
        self.assertIn("12m", event["description"])

    @patch("requests.Session")
    def test_dry_run_previews_fit_without_writing(self, session_class):
        garmin = Mock()
        garmin.today.return_value = date(2026, 11, 9)
        garmin.get_scheduled_workouts_by_period.return_value = []
        garmin.get_workouts.side_effect = [[{
            "workoutId": 1, "workoutName": "11/11 Easy run", "sportType": RUN,
        }], []]
        garmin.get_workout_by_id.return_value = detail(step("main", 10, condition="reps"))
        garmin.download_workout.return_value = bytes(8) + b".FIT"

        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(sync(garmin, dry_run=True, athlete=None, key=None), 0)
        self.assertIn("2026-11-11 12:00  Easy run", output.getvalue())
        self.assertIn("Garmin FIT import", output.getvalue())
        session_class.assert_not_called()
