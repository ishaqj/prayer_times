from datetime import datetime
from zoneinfo import ZoneInfo

from src.main import get_due_prayer, normalize_time

TZ = ZoneInfo("Europe/Stockholm")


def schedule():
    return {
        "year": 2026,
        "month": 8,
        "days": {
            "2026-08-13": {
                "fajr": "02:35",
                "dhohr": "12:59",
                "asr": "17:32",
                "magrib": "22:01",
                "isha": "23:04",
            }
        },
    }


def test_normalize_time():
    assert normalize_time("2:35") == "02:35"
    assert normalize_time("02:35") == "02:35"
    assert normalize_time("22:01") == "22:01"


def test_prayer_is_due_inside_window():
    now = datetime.fromisoformat("2026-08-13T13:04:00+02:00")
    assert get_due_prayer(now, schedule()) == ("dhohr", "12:59")


def test_prayer_is_not_due_after_window():
    now = datetime.fromisoformat("2026-08-13T13:09:00+02:00")
    assert get_due_prayer(now, schedule()) is None


def test_prayer_is_not_due_before_time():
    now = datetime.fromisoformat("2026-08-13T12:58:00+02:00")
    assert get_due_prayer(now, schedule()) is None


def test_isha():
    now = datetime.fromisoformat("2026-08-13T23:04:00+02:00")
    assert get_due_prayer(now, schedule()) == ("isha", "23:04")
