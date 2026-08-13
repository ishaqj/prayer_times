# Sweden Prayer Times → ntfy

GitHub Actions project that:

1. Scrapes the current month's prayer times from Islamiska Förbundet.
2. Stores the schedule in `data/prayer_times.json`.
3. Runs every 5 minutes and checks today's prayer times.
4. Sends a notification through ntfy when a prayer is due.
5. Uses a small committed notification state file to prevent duplicate notifications.

## Setup

### 1. Create an ntfy topic

Use an unguessable topic, for example:

`prayer_times_swe_<random-string>`

Subscribe to it in the ntfy app.

### 2. Add the GitHub repository variable

GitHub:

`Settings → Secrets and variables → Actions → Variables`

Create:

`NTFY_TOPIC=<your topic>`

No ntfy password is required for a normal public topic.

### 3. Push the repository

```bash
git init
git add .
git commit -m "Add prayer time notifications"
git branch -M main
git remote add origin <YOUR_REPOSITORY_URL>
git push -u origin main
```

### 4. Test locally

Requires Python 3.11+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python -m src.main scrape
python -m src.main check --now "2026-08-13T12:59:00+02:00"
```

`check` defaults to dry-run and will NOT send ntfy.

To send a real notification:

```bash
export NTFY_TOPIC="your-topic"
python -m src.main check --now "2026-08-13T12:59:00+02:00" --send
```

### 5. Test the scraper

```bash
python -m pytest
```

### 6. Test from GitHub Actions

Go to:

`Actions → Prayer Times → Run workflow`

You can choose:

- `scrape`: refresh the current month's data
- `check`: check the current time
- `test_notification`: send a real test notification

The scheduled workflows are:

- Scraper: daily at 00:05 Europe/Stockholm
- Prayer checker: every 5 minutes

## Notification window

The checker uses a 10-minute window:

`prayer_time <= now < prayer_time + 10 minutes`

This is intentional because GitHub Actions scheduled workflows are not real-time and may start late.

The notification state contains a deterministic key such as:

`2026-08-13:fajr`

so repeated runs don't send the same prayer twice.

## Data

`data/prayer_times.json` is deliberately committed to Git. It contains the current month's complete schedule and metadata about when it was fetched.

The scraper validates that the parsed month and number of days are plausible before replacing the existing file.

## Source

https://www.islamiskaforbundet.se/bonetider/
