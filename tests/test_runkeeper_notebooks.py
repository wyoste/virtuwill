"""RunKeeper: the two bronze notebooks' reading of the export. Their code cells run here without Spark (the cell
tagged "run" is skipped); the activities and tracks are made up."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tests.test_workout_routes import TRACK, gpx

ROOT = Path(__file__).resolve().parent.parent


def notebook(name):
    """A notebook's code cells, run as a module, all but the one that runs it."""
    cells = json.loads((ROOT / "jobs" / name).read_text())["cells"]
    namespace = {"__name__": name}
    for cell in cells:
        if cell["cell_type"] == "code" and "run" not in cell["metadata"].get("tags", []):
            exec(compile("".join(cell["source"]), name, "exec"), namespace)
    return SimpleNamespace(**namespace)


activities_nb = notebook("runkeeper_activities_to_bronze.ipynb")
gpx_nb = notebook("runkeeper_gpx_to_bronze.ipynb")

HEADER = ("Activity Id,Date,Type,Route Name,Distance (mi),Duration,Average Pace,Average Speed (mph),Calories Burned,"
          "Climb (ft),Average Heart Rate (bpm),Friend's Tagged,Notes,GPX File\n")
CSV = HEADER + (
    'a1,2019-05-04 16:35:09,Running,,3.10,28:30,9:11,6.53,350,120,,,"Easy, with ""strides""",2019-05-04-163509.gpx\n'
    "a2,2019-05-06 06:10:00,Strength Training,,0.00,45:00,,,250,,,,,\n"
    "a3,2019-05-07 18:00:00,Cycling,,20.5,1:05:12,3:10,18.9,900,800,,,,\n")


class NotebookTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "activity_logs" / "2026-09").mkdir(parents=True)
        (self.dir / "gpx_maps").mkdir()
        (self.dir / "activity_logs" / "2026-09" / "cardioActivities.csv").write_text(CSV)
        (self.dir / "activity_logs" / "measurements.csv").write_text("Date,Weight\n2019-05-04,180\n")
        (self.dir / "gpx_maps" / "2019-05-04-163509.gpx").write_bytes(gpx(TRACK))
        (self.dir / "gpx_maps" / "route.gpx").write_bytes(gpx(TRACK))           # no start time in its name

    def test_the_run_cell_is_the_only_one_tagged(self):
        for name in ("runkeeper_activities_to_bronze.ipynb", "runkeeper_gpx_to_bronze.ipynb"):
            cells = json.loads((ROOT / "jobs" / name).read_text())["cells"]
            run = [c for c in cells if "run" in c["metadata"].get("tags", [])]
            self.assertEqual(len(run), 1, name)
            self.assertIn("run(spark", "".join(run[0]["source"]))

    def test_headers_and_units(self):
        self.assertEqual(activities_nb.column("Distance (km)"), ("distance", "km"))
        self.assertEqual(activities_nb.column("Average Heart Rate (bpm)"), ("average_heart_rate_bpm", None))
        self.assertEqual(activities_nb.column("Friend's Tagged"), ("friends_tagged", None))
        self.assertEqual(activities_nb.column("Something New"), (None, None))

    def test_reads_the_activity_log(self):
        activities, files = activities_nb.read_activities(str(self.dir / "activity_logs"))
        self.assertEqual(len(files), 1)                                       # measurements.csv isn't read
        by_id = {a["activity_id"]: a for a in activities}
        self.assertEqual(by_id["a1"]["notes"], 'Easy, with "strides"')
        self.assertEqual((by_id["a1"]["distance_unit"], by_id["a1"]["speed_unit"], by_id["a1"]["climb_unit"]), ("mi", "mph", "ft"))
        self.assertEqual((by_id["a1"]["gpx_file"], by_id["a2"]["gpx_file"]), ("2019-05-04-163509.gpx", None))
        rows, summary = activities_nb.plan(activities, {})
        self.assertEqual((summary["activities"], summary["with_gpx_file"], summary["new_or_changed"]), (3, 1, 3))
        self.assertEqual(set(rows[0]) - set(activities_nb.COLUMNS), set())

    def test_unchanged_activities_are_not_written_again_and_the_newest_export_wins(self):
        activities, _ = activities_nb.read_activities(str(self.dir / "activity_logs"))
        rows, _ = activities_nb.plan(activities, {})
        existing = {r["activity_id"]: r["_row_hash"] for r in rows}
        self.assertEqual(activities_nb.plan(activities, existing)[1]["new_or_changed"], 0)
        newer = self.dir / "activity_logs" / "CardioActivities (1).csv"     # any case
        newer.write_text(CSV.replace("28:30", "29:00"))
        os.utime(newer, (2e9, 2e9))
        activities, _ = activities_nb.read_activities(str(self.dir / "activity_logs"))
        rows, summary = activities_nb.plan(activities, existing)
        self.assertEqual(([r["activity_id"] for r in rows], summary["activities"]), (["a1"], 3))
        self.assertEqual(rows[0]["duration"], "29:00")

    def test_reads_the_tracks(self):
        self.assertEqual(gpx_nb.activity_start("2026-09-28-200444.gpx"), "2026-09-28 20:04:44")
        self.assertIsNone(gpx_nb.activity_start("2026-13-28-200444.gpx"))
        files = gpx_nb.read_files(str(self.dir / "gpx_maps"))
        rows, summary = gpx_nb.plan(files, {})
        self.assertEqual((summary["gpx_files"], summary["without_start_in_name"], summary["new_or_changed"]), (2, 1, 2))
        track = gpx_nb.with_track(next(r for r in rows if r["gpx_file"] == "2019-05-04-163509.gpx"))
        self.assertEqual((track["activity_start"], track["points"], track["track_started_at"]),
                         ("2019-05-04 16:35:09", 5, "2026-03-01T07:00:00Z"))
        self.assertIn("<trkpt", track["gpx"])
        self.assertEqual(set(track) - set(gpx_nb.COLUMNS), set())
        existing = {r["gpx_file"]: r["_row_hash"] for r in rows}
        self.assertEqual(gpx_nb.plan(files, existing)[1]["new_or_changed"], 0)
