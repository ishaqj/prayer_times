from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

SOURCE_URL = "https://www.islamiskaforbundet.se/bonetider/"
TIMEZONE = ZoneInfo("Europe/Stockholm")
DATA_FILE = Path("data/prayer_times.json")
STATE_FILE = Path("data/notification_state.json")

PRAYERS = ("fajr", "dhohr", "asr", "magrib", "isha")
DISPLAY_NAMES = {
    "fajr": "Fajr",
    "dhohr": "Dhohr",
    "asr": "Asr",
    "magrib": "Magrib",
    "isha": "Isha",
}


@dataclass(frozen=True)
class PrayerDay:
    date: date
    times: dict[str, str]


def fetch_source() -> str:
    response = requests.get(
        SOURCE_URL,
        timeout=30,
        headers={
            "User-Agent": "prayer-times-bot/1.0 (+GitHub Actions)"
        },
    )
    response.raise_for_status()
    return response.text


def normalize_time(value: str) -> str:
    match = re.search(r"\b([01]?\d|2[0-3])[:.]([0-5]\d)\b", value)
    if not match:
        raise ValueError(f"Could not parse prayer time from: {value!r}")
    return f"{int(match.group(1)):02d}:{match.group(2)}"


def parse_month(html: str, expected_year: int | None = None, expected_month: int | None = None) -> dict:
    """
    Parse the prayer table.

    The source has changed its HTML over time, so this parser intentionally
    discovers a table by its prayer-name headers instead of relying on CSS
    classes or element IDs.
    """
    soup = BeautifulSoup(html, "html.parser")

    tables = soup.find_all("table")
    candidate = None

    for table in tables:
        headers = [
            " ".join(cell.stripped_strings).lower()
            for cell in table.find_all(["th", "td"])[:20]
        ]
        joined = " ".join(headers)
        if all(word in joined for word in ("fajr", "dhohr", "asr", "magrib", "isha")):
            candidate = table
            break

    if candidate is None:
        raise RuntimeError(
            "Could not find a prayer-times table containing "
            "Fajr, Dhohr, Asr, Magrib and Isha."
        )

    rows = candidate.find_all("tr")
    parsed: dict[str, dict[str, str]] = {}

    header_cells = rows[0].find_all(["th", "td"]) if rows else []
    headers = [" ".join(c.stripped_strings).lower() for c in header_cells]

    def header_index(*names: str) -> int | None:
        for index, header in enumerate(headers):
            compact = re.sub(r"[^a-z]", "", header)
            for name in names:
                if name in compact:
                    return index
        return None

    date_idx = header_index("datum", "date", "dag")
    indexes = {
        prayer: header_index(prayer)
        for prayer in PRAYERS
    }

    if any(indexes[p] is None for p in PRAYERS):
        # Some versions may have headers in a different order or the first
        # row may not be the actual header. Search the first few rows.
        for row in rows[:5]:
            candidate_headers = [
                " ".join(c.stripped_strings).lower()
                for c in row.find_all(["th", "td"])
            ]
            for prayer in PRAYERS:
                if indexes[prayer] is None:
                    for index, header in enumerate(candidate_headers):
                        if prayer in re.sub(r"[^a-z]", "", header):
                            indexes[prayer] = index
            if date_idx is None:
                for index, header in enumerate(candidate_headers):
                    compact = re.sub(r"[^a-z]", "", header)
                    if any(x in compact for x in ("datum", "date", "dag")):
                        date_idx = index

    if any(indexes[p] is None for p in PRAYERS):
        raise RuntimeError(f"Could not identify all prayer columns. Headers: {headers}")

    # If no date column exists, infer the date from a first column containing
    # a day number. The current-month page is expected to have one row per day.
    for row in rows[1:]:
        cells = [" ".join(c.stripped_strings) for c in row.find_all(["th", "td"])]
        if not cells:
            continue

        raw_day = cells[date_idx] if date_idx is not None and date_idx < len(cells) else cells[0]
        day_match = re.search(r"\b([1-9]|[12]\d|3[01])\b", raw_day)
        if not day_match:
            continue

        day = int(day_match.group(1))

        try:
            # If the page explicitly includes a full date, prefer it.
            full_date = re.search(r"\b(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})\b", raw_day)
            if full_date:
                row_date = date(
                    int(full_date.group(1)),
                    int(full_date.group(2)),
                    int(full_date.group(3)),
                )
            else:
                year = expected_year or datetime.now(TIMEZONE).year
                month = expected_month or datetime.now(TIMEZONE).month
                row_date = date(year, month, day)
        except ValueError:
            continue

        try:
            times = {}
            for prayer, index in indexes.items():
                assert index is not None
                if index >= len(cells):
                    raise ValueError("missing cell")
                times[prayer] = normalize_time(cells[index])
        except (ValueError, AssertionError):
            continue

        parsed[row_date.isoformat()] = times

    if not parsed:
        raise RuntimeError("The table was found, but no valid prayer rows were parsed.")

    return {
        "year": next(iter(parsed.values())) and int(next(iter(parsed)).split("-")[0]),
        "month": int(next(iter(parsed)).split("-")[1]),
        "timezone": "Europe/Stockholm",
        "source": SOURCE_URL,
        "fetched_at": datetime.now(TIMEZONE).isoformat(),
        "days": dict(sorted(parsed.items())),
    }


def validate_schedule(data: dict) -> None:
    days = data.get("days", {})
    if not days:
        raise ValueError("Prayer schedule contains no days.")

    year = int(data["year"])
    month = int(data["month"])

    if not (1 <= month <= 12):
        raise ValueError("Invalid month.")

    for iso_date, times in days.items():
        parsed_date = date.fromisoformat(iso_date)
        if parsed_date.year != year or parsed_date.month != month:
            raise ValueError(f"Date {iso_date} is outside schedule month.")

        for prayer in PRAYERS:
            normalize_time(times[prayer])

    # A monthly table should contain most/all days. Allow a small margin for
    # source formatting anomalies, but reject obviously broken scrapes.
    if len(days) < 25:
        raise ValueError(f"Only {len(days)} days parsed; refusing to replace schedule.")


def save_schedule(data: dict) -> bool:
    validate_schedule(data)

    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)

    new_text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    old_text = DATA_FILE.read_text(encoding="utf-8") if DATA_FILE.exists() else None

    if old_text == new_text:
        print("Prayer schedule unchanged.")
        return False

    DATA_FILE.write_text(new_text, encoding="utf-8")
    print(f"Saved {len(data['days'])} days to {DATA_FILE}.")
    return True


def load_schedule() -> dict:
    if not DATA_FILE.exists():
        raise FileNotFoundError(
            f"{DATA_FILE} does not exist. Run: python -m src.main scrape"
        )
    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    validate_schedule(data)
    return data


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"sent": []}
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def get_due_prayer(now: datetime, data: dict) -> tuple[str, str] | None:
    today = now.date().isoformat()
    day = data["days"].get(today)

    if not day:
        return None

    current_minutes = now.hour * 60 + now.minute

    for prayer in PRAYERS:
        hour, minute = map(int, day[prayer].split(":"))
        prayer_minutes = hour * 60 + minute
        delta = current_minutes - prayer_minutes

        if 0 <= delta < 10:
            return prayer, day[prayer]

    return None


def send_ntfy(topic: str, prayer: str, prayer_time: str) -> None:
    payload = {
        "topic": topic,
        "title": f"🕋 {DISPLAY_NAMES[prayer]}",
        "message": f"{DISPLAY_NAMES[prayer]} It's time to pray! — {prayer_time}",
        "priority": 4,
        "tags": [],
    }

    response = requests.post(
        "https://ntfy.sh/",
        json=payload,
        timeout=15,
    )
    response.raise_for_status()


def git_commit_if_changed(message: str) -> None:
    subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
    subprocess.run(
        ["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"],
        check=True,
    )

    result = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
    if not result.stdout.strip():
        print("No Git changes.")
        return

    subprocess.run(["git", "add", "data/"], check=True)
    subprocess.run(["git", "commit", "-m", message], check=True)
    subprocess.run(["git", "push"], check=True)


def command_scrape() -> None:
    now = datetime.now(TIMEZONE)
    html = fetch_source()
    data = parse_month(html, now.year, now.month)
    changed = save_schedule(data)
    if changed and os.getenv("CI") == "true":
        git_commit_if_changed(
            f"chore: update prayer times {data['year']}-{data['month']:02d}"
        )


def command_check(now: datetime, send: bool) -> int:
    data = load_schedule()
    due = get_due_prayer(now, data)

    if due is None:
        print(f"{now.isoformat()}: no prayer due.")
        return 0

    prayer, prayer_time = due
    key = f"{now.date().isoformat()}:{prayer}"
    state = load_state()
    sent = set(state.get("sent", []))

    print(
        f"{now.isoformat()}: {DISPLAY_NAMES[prayer]} is due "
        f"at {prayer_time}. key={key}"
    )

    if key in sent:
        print("Already notified.")
        return 0

    if not send:
        print("DRY RUN: notification not sent.")
        return 0

    topic = os.getenv("NTFY_TOPIC")
    if not topic:
        raise RuntimeError("NTFY_TOPIC environment variable is required when --send is used.")

    send_ntfy(topic, prayer, prayer_time)

    sent.add(key)
    # Keep only recent state entries.
    state["sent"] = sorted(sent)[-100:]
    save_state(state)

    if os.getenv("CI") == "true":
        git_commit_if_changed(f"chore: mark prayer notified {key}")

    print("Notification sent.")
    return 0


def command_test_notification() -> None:
    topic = os.getenv("NTFY_TOPIC")
    if not topic:
        raise RuntimeError("NTFY_TOPIC environment variable is required.")

    response = requests.post(
        "https://ntfy.sh/",
        json={
            "topic": topic,
            "title": "🧪 Prayer Times test",
            "message": "GitHub Actions ntfy notification is working.",
            "priority": 4,
            "tags": ["white_check_mark"],
        },
        timeout=15,
    )
    response.raise_for_status()
    print("Test notification sent.")


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("scrape")

    check = sub.add_parser("check")
    check.add_argument("--now", help="ISO datetime, e.g. 2026-08-13T12:59:00+02:00")
    check.add_argument("--send", action="store_true")

    sub.add_parser("test_notification")

    args = parser.parse_args()

    if args.command == "scrape":
        command_scrape()
    elif args.command == "check":
        now = (
            datetime.fromisoformat(args.now)
            if args.now
            else datetime.now(TIMEZONE)
        )
        raise SystemExit(command_check(now, args.send))
    elif args.command == "test_notification":
        command_test_notification()


if __name__ == "__main__":
    main()
