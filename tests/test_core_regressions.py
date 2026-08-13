from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "outputs" / "音乐信息与封面一键整理.py"
spec = importlib.util.spec_from_file_location("music_organizer", SOURCE)
assert spec and spec.loader
organizer = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = organizer
spec.loader.exec_module(organizer)


def fake_track(kind: str = "FLAC") -> dict:
    metadata = {
        "TITLE": "盛夏的果实",
        "ARTIST": "莫文蔚",
        "ALBUM": "NO.1精选辑",
        "ALBUMARTIST": "",
        "YEAR": "",
        "DATE": "",
    }
    return {
        "path": Path(r"C:\fixture\莫文蔚-盛夏的果实.flac"),
        "filename": "莫文蔚-盛夏的果实.flac",
        "type": kind,
        "size": 123,
        "mtime_ns": 456,
        "duration_ms": 251160,
        "metadata": metadata,
        "cover_data": None,
        "cover_mime": None,
        "cover_count": 0,
    }


class MetadataAndPlanTests(unittest.TestCase):
    def test_calendar_date_validation(self) -> None:
        for value in ("2000", "2000-06", "2000-06-16"):
            self.assertTrue(organizer.valid_release_date(value))
        for value in ("", "2000-13", "2000-02-30", "00-01-01"):
            self.assertFalse(organizer.valid_release_date(value))

    def test_partial_metadata_is_writable_even_when_still_incomplete(self) -> None:
        track = fake_track()
        target = dict(track["metadata"], ALBUMARTIST="莫文蔚")
        plan = organizer.manual_plan_from_track(track, target)
        self.assertEqual(plan["action"], "write")
        self.assertFalse(plan["metadata_ready"])
        self.assertEqual(set(plan["metadata_changes"]), {"ALBUMARTIST"})

    def test_cover_only_is_writable(self) -> None:
        track = fake_track()
        plan = organizer.manual_plan_from_track(track, track["metadata"])
        plan["cover_ready"] = True
        plan["cover_path"] = Path("cover.jpg")
        organizer.update_plan_action(plan)
        self.assertEqual(plan["action"], "write")

    def test_asf_remains_blocked(self) -> None:
        track = fake_track("ASF")
        target = dict(track["metadata"], ALBUMARTIST="莫文蔚")
        plan = organizer.manual_plan_from_track(track, target)
        self.assertEqual(plan["action"], "skip")

    def test_manual_date_normalization_and_rejection(self) -> None:
        value = organizer.normalize_metadata_dates(
            dict(fake_track()["metadata"], YEAR="2000", DATE="")
        )
        self.assertEqual(value["DATE"], "2000")
        with self.assertRaises(ValueError):
            organizer.normalize_metadata_dates(
                dict(fake_track()["metadata"], YEAR="2000", DATE="2001-01")
            )

    def test_metadata_valid_no_longer_controls_action(self) -> None:
        track = fake_track()
        plan = organizer.manual_plan_from_track(
            track, dict(track["metadata"], YEAR="2000", DATE="2000-06")
        )
        self.assertFalse(plan["metadata_ready"])
        self.assertEqual(plan["action"], "write")

    def test_rejected_photo_does_not_block_partial_metadata(self) -> None:
        track = fake_track()
        plan = organizer.manual_plan_from_track(
            track, dict(track["metadata"], ALBUMARTIST="莫文蔚")
        )
        plan["cover_ready"] = True
        plan["cover_path"] = Path("photo.jpg")
        plan["cover_kind"] = "artist-photo"
        plan["artist_photo_pending"] = True
        plan["artist_photo_approved"] = False
        organizer.update_plan_action(plan)
        self.assertEqual(plan["action"], "write")


class RealFileWriteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.flac_source = Path(
            r"D:\gmskywalker\Music\new\莫文蔚-盛夏的果实.flac"
        )
        cls.mp3_source = Path(
            r"D:\gmskywalker\Music\陈奕迅-谁知我这种男孩子(来不及听你说爱我插曲)-《野孩子》改编.mp3"
        )
        if not cls.flac_source.is_file() or not cls.mp3_source.is_file():
            raise unittest.SkipTest("real audio fixtures unavailable")

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="music-organizer-test-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_flac_one_field_write_preserves_every_other_field(self) -> None:
        path = self.temp_dir / self.flac_source.name
        shutil.copy2(self.flac_source, path)
        track = organizer.scan_paths([path])[0][0]
        before = dict(track["metadata"])
        after = dict(before, ALBUMARTIST="莫文蔚")
        organizer.write_temp_file(path, "FLAC", after, None, {"ALBUMARTIST"})
        verified = organizer.verify_temp_file(
            path, "FLAC", before, after, {"ALBUMARTIST"}, expect_cover=False
        )
        self.assertEqual(verified["metadata"]["ALBUMARTIST"], "莫文蔚")
        for field in organizer.CORE_FIELDS:
            if field != "ALBUMARTIST":
                self.assertEqual(verified["metadata"][field], before[field])

    def test_mp3_manual_clear_changes_only_requested_field(self) -> None:
        path = self.temp_dir / self.mp3_source.name
        shutil.copy2(self.mp3_source, path)
        track = organizer.scan_paths([path])[0][0]
        before = dict(track["metadata"])
        changed_field = "ALBUM"
        after = dict(before, ALBUM="")
        organizer.write_temp_file(path, "MP3", after, None, {changed_field})
        verified = organizer.verify_temp_file(
            path, "MP3", before, after, {changed_field}, expect_cover=False
        )
        self.assertEqual(verified["metadata"][changed_field], "")
        for field in organizer.CORE_FIELDS:
            if field != changed_field:
                self.assertEqual(verified["metadata"][field], before[field])

    def test_apply_plans_partial_write_uses_safe_temp_replace(self) -> None:
        path = self.temp_dir / self.flac_source.name
        shutil.copy2(self.flac_source, path)
        track = organizer.scan_paths([path])[0][0]
        target = dict(track["metadata"], ALBUMARTIST="莫文蔚")
        plan = organizer.manual_plan_from_track(track, target)
        run_dir = self.temp_dir / "record"
        run_dir.mkdir()
        results = organizer.apply_plans(self.temp_dir, [plan], run_dir)
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["success"], results[0])
        after = organizer.scan_paths([path])[0][0]
        self.assertEqual(after["metadata"]["ALBUMARTIST"], "莫文蔚")
        self.assertEqual(after["duration_ms"], track["duration_ms"])

    def test_apply_plans_blocks_file_changed_after_preview(self) -> None:
        path = self.temp_dir / self.flac_source.name
        shutil.copy2(self.flac_source, path)
        track = organizer.scan_paths([path])[0][0]
        target = dict(track["metadata"], ALBUMARTIST="莫文蔚")
        plan = organizer.manual_plan_from_track(track, target)
        path.touch()
        run_dir = self.temp_dir / "record"
        run_dir.mkdir()
        results = organizer.apply_plans(self.temp_dir, [plan], run_dir)
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0]["success"])
        self.assertIn("changed after scanning", results[0]["error"])


class TrackCatalogTests(unittest.TestCase):
    def test_cached_candidate_is_rescored_for_current_track(self) -> None:
        metadata = fake_track()["metadata"]
        candidate = {
            "provider": "musicbrainz-recording",
            "title": "盛夏的果实",
            "artist": "莫文蔚",
            "album": "NO.1",
            "release_date": "2000-06",
            "duration_ms": 249000,
            "date_scope": "current-release",
        }
        score = organizer.track_candidate_score(metadata, 251160, candidate)
        self.assertGreater(score[0], 0.90)
        candidate.update(
            score=score[0],
            title_similarity=score[1],
            artist_similarity=score[2],
            album_similarity=score[3],
            duration_similarity=score[4],
        )
        self.assertTrue(organizer.accepted_track_candidate(candidate))

    def test_current_release_candidate_requires_album_match(self) -> None:
        candidate = {
            "release_date": "2024-01-01",
            "date_scope": "current-release",
            "title_similarity": 1.0,
            "artist_similarity": 1.0,
            "album_similarity": 0.1,
            "duration_similarity": 1.0,
            "score": 0.95,
        }
        self.assertFalse(organizer.accepted_track_candidate(candidate))


if __name__ == "__main__":
    unittest.main(verbosity=2)
