# omonpakari

A quiet barometric-pressure and symptom journal for
[Hermes Agent](https://github.com/NousResearch/hermes-agent).

*Omonpakari* (慮り) is a Japanese word for considerate foresight: thinking
ahead for someone, including what they have not said out loud.

## What it does

People with weather-sensitive headaches, migraines, joint pain or chronic
fatigue often feel a pressure drop before they can name it. Most trackers
answer with reminders and check-ins, which become one more thing to keep up
with.

omonpakari gives your Hermes agent the facts instead, and leaves the talking
to you:

- **Pressure now, before and ahead.** Current barometric pressure, how it
  changed over the past hours and how it is forecast to change (OpenWeather).
- **A journal in your own words.** Symptoms, medications or supplements, and
  short notes. No fixed list of conditions or drug names.
- **Patterns on request.** Compares your entries with pressure, against how
  common low or falling pressure was overall, with raw counts.
- **Quiet background context.** Before each reply, a short factual note about
  pressure and the last 48 hours of entries, so the agent can adjust its tone
  without asking.

## Considerate by design

- **It never starts a conversation.** No reminders, schedules, notifications
  or "did you take your medication?". There is no code path that sends a
  message on its own.
- **The hook only places context.** `pre_llm_call` runs before a turn *you*
  started. It reads local storage, adds a short note (at most 800 characters)
  ending with a line that tells the agent not to bring it up on its own
  (pressure only when you talk about how you feel or your plans, or ask;
  journal entries only when you talk about how you feel or ask), and never
  blocks a turn.
  On turns nobody typed (scheduled cron runs, delegated subagents, webhooks,
  Kanban workers, batch and curator runs, agent-to-agent calls) it does
  nothing at all, so health context never ends up in an automated message.
  When the note has to be shortened, its closing line always stays. It does not
  read or store your message or the conversation history.
- **It does not repeat itself.** Within a conversation the note is placed
  again only when its content changes (new entries, a new pressure reading)
  or after six hours, because placed context stays in the conversation history.
- **It never makes you wait.** The hook never calls the network. When the saved
  location's data is over an hour old, it refreshes once in a background
  thread, at most once an hour, and only during a turn you started.
- **Numbers, not verdicts.** No diagnosis, no warnings, no medication advice.
  If you set your own limit for a medication, the journal reports
  `count_in_window` against your `max_count`, and nothing more.
- **Your words stay yours.** The journal stores the short label and note the
  agent passes, never your whole message. You can delete one entry or purge
  everything.

## Install

```bash
hermes plugins install omonpakari   # from the Hermes plugin catalog
hermes plugins enable omonpakari    # if it was installed disabled
```

Before the catalog entry is merged, pass this repository's Git URL (or
`owner/repo`) to `hermes plugins install` instead.

Requires Hermes Agent 0.22.0 or newer. No extra Python dependencies.

### API key

Get a free key at <https://openweathermap.org/api> (the free tier covers the
`2.5/weather` and `2.5/forecast` endpoints used here). Hermes asks for
`OPENWEATHER_API_KEY` during install and stores it in `~/.hermes/.env`; you can
also set it in the Desktop app under Settings → Plugins → omonpakari. The
plugin reads it only through Hermes (`agent.secret_scope.get_secret`). No key
is stored in this repository.

Only the pressure tool needs the key. Journaling and correlation work without
it, and without a key the pressure tool returns `missing_key` instead of
failing. Hermes only asks for `requires_env` at install time; a skipped key
does not keep the plugin from loading, and you can add it later.

### Location

There is no default location. Either:

- tell the agent where you are and ask it to remember (the pressure tool then
  saves latitude/longitude rounded to about 1 km), or
- set `latitude` and `longitude` in the plugin settings
  (`plugins.entries.omonpakari.settings` in `config.yaml`, or the Desktop form).

The journal database keeps only a short place key derived from the rounded
coordinates, so readings from different places are not mixed. Coordinates are
never placed in the background note or in tool results.

### Time zone

Times without an offset (an `at` such as `2026-11-06T09:00`) are read in the
time zone set for Hermes (the top-level `timezone` in `config.yaml`, an IANA
name such as `Europe/Berlin`), and in the machine's local time when none is
set. Set it when Hermes runs on a server in another zone.

## Tools

| Tool | What it does |
|---|---|
| `omonpakari_pressure` | Current pressure, `level` (`very_low` / `low` / `normal` / `high`), `trend`, change over the past `window_h` and over the forecast ahead (each with the hours actually covered), the lowest point ahead, and forecast points. Failures return a reason only: `missing_key`, `location_not_set`, `invalid_location`, `auth_failed`, `rate_limited`, `network`, `bad_response`, `http_<status>`. Readings are cached for 30 minutes. |
| `omonpakari_log` | `add` a `symptom`, `medication` or `note` with the user's own `label`, optional `severity` (1-5), `dose`, `note`, `at`; `list` entries with filters; `delete` one; `set_limit` for a medication; `purge` everything (`confirm: true`). `at` may not be in the future or more than 10 years back. Each new entry gets the nearest stored pressure reading (within 90 minutes) without a network call. |
| `omonpakari_correlate` | Hourly pressure series from stored readings. Reports the baseline share of low and falling hours, the share of your entries in those hours, their ratio (`lift`), mean pressure change before entries versus all hours, and raw counts. Returns `insufficient_data` below 5 entries with pressure spread over at least 3 days, or below 72 hours of coverage. The falling share is left out (`null`) when fewer than 5 entries have a known change, and `small_sample` is true below 10 days of entries. |

The bundled skill `omonpakari:omonpakari` tells the agent how to use all of
this without nagging or diagnosing.

## Settings

All optional, under `plugins.entries.omonpakari.settings`:

| Key | Default | Meaning |
|---|---|---|
| `latitude`, `longitude` | none | Saved location |
| `low_hpa` / `very_low_hpa` / `high_hpa` | 1010 / 1003 / 1020 | Level thresholds (hPa) |
| `drop_hpa` | 6 | A change of at least this within `window_h` counts as falling or rising |
| `window_h` | 24 | Hours for past and forecast change (3-72) |
| `quiet_context` | true | Place the background note before replies |
| `background_refresh` | true | Allow the hourly background refresh for the saved location |

## Privacy and network disclosure

- **Third-party network calls:** `api.openweathermap.org` only, for pressure at
  the location you set, from the pressure tool or the background refresh. The
  API key travels in the request query as OpenWeather requires; it is redacted
  from logs and never returned to the agent.
- **Storage:** `<HERMES_HOME>/plugin-data/omonpakari/data.db` (SQLite through
  Hermes' `plugin_db`). Nothing is written inside the plugin directory.
- **Shared chats:** the background note goes into every conversation this
  Hermes profile handles, messaging-gateway chats included. If other people
  talk to the same agent (a group chat, a shared bot), set `quiet_context` to
  false so your journal stays out of their conversations.
- **No telemetry, no remote notifications, no shell commands, no files read
  outside Hermes' own storage.**

## Not medical advice

omonpakari helps a person keep track of how they feel. It does not diagnose,
and it does not suggest starting, stopping or changing any medication.

## Development

```bash
python -m pytest            # offline; OpenWeather and Hermes storage are faked
hermes plugins validate .
hermes plugins doctor . --ci
```

## License

MIT
