# omonpakari

Considerate health context for
[Hermes Agent](https://github.com/NousResearch/hermes-agent).

*Omonpakari* (慮り) is a Japanese word for considerate foresight: thinking
ahead for someone, including what they have not said out loud.

## What it does

Most health trackers treat a person as a list of measurements to be checked
off, and answer with reminders and check-ins that become one more burden.
omonpakari takes the opposite shape: it gives the agent the material to
*know the person*, and lets that knowledge quietly shape any conversation —
plans, meals, an outing, work, rest — the way someone who lives with them
would.

- **Pressure now, before and ahead.** Current barometric pressure, how it
  changed over the past hours and how it is forecast to change (OpenWeather).
- **A journal in your own words, typed by kind.** Symptoms, medications,
  supplements, food and drink, and short notes — kept as separate kinds,
  because *when* something was taken matters as much as *what*.
- **A cycle that respects irregularity.** Period starts and ends, observed
  cycle lengths, the next window returned as a **range of dates** (never a
  single predicted day), and the current phase as a tentative estimate.
- **Interaction checks, with sources.** A reference table of common
  interactions — absorption around iron (cocoa, tea tannins, coffee, soy
  protein, calcium and other minerals inhibit it; vitamin C and citric acid
  help), and pairings beyond iron, such as NSAIDs with alcohol, two NSAIDs
  together, or acetaminophen with alcohol — matched against your journal by
  time and reported as facts, before the agent ever proposes the
  combination. The table is a starting set, meant to grow: an organ does
  not get to declare part of what reaches it "not my department".
- **Life notes: the person's own alternatives.** Habits and usual choices
  the user teaches ("in the morning I drink hojicha, non-caffeine"), stored
  in their words. When a suggestion conflicts with their body, the agent can
  offer the remembered alternative instead of a generic one.
- **Patterns on request.** Entries compared with pressure — and with cycle
  phase — against baselines, with raw counts.
- **A quiet picture of today.** Before each reply, a short note assembles
  pressure, cycle phase, today's interaction flags, recent entries and life
  notes, so the agent can weigh them *before* suggesting anything.

The founding incident: an agent once warmly proposed cocoa together with a
morning iron supplement — cocoa blocks roughly 71% of that iron's
absorption. The knowledge existed; nothing checked it at the moment of the
suggestion. omonpakari exists so that moment is covered: knowing is not
enough, the check has to sit between knowing and speaking.

## Considerate by design

- **No pestering, ever.** No reminders, no "did you take your medication?",
  no repeated nudges, nothing that hurries the person into acting. But
  *not pestering* is not the same as *never speaking first*: a quiet
  word first, when the moment genuinely calls for one — a sharp
  pressure fall ahead of a planned outing, a cycle window opening on a
  heavy day — is part of what consideration means. There is no quota or
  template for that; it is the judgment of someone who knows the
  person, not a notification policy. The sending channel
  belongs to the host (Hermes); this plugin's part is to always have the
  honest picture ready, in and out of conversation.
- **The hook places a picture, not a gag.** `pre_llm_call` runs before a
  turn *you* started. It reads local storage and adds a short note (at most
  1,200 characters) whose closing line instructs the agent to let the
  picture inform *any* topic — to weigh it before suggesting, to prefer
  your own alternatives, and not to propose items flagged under today's
  interactions near their conflicting entry — while never lecturing,
  diagnosing or reciting the numbers. It never blocks a turn.
  On turns nobody typed (scheduled cron runs, delegated subagents, webhooks,
  Kanban workers, batch and curator runs, agent-to-agent calls) it does
  nothing at all, so health context never ends up in an automated message.
  It does not read or store your message or the conversation history.
- **It does not repeat itself.** Within a conversation the note is placed
  again only when its content changes or after six hours, because placed
  context stays in the conversation history.
- **It never makes you wait.** The hook never calls the network. When the
  saved location's data is over an hour old, it refreshes once in a
  background thread, at most once an hour, and only during a turn you
  started.
- **Numbers and ranges, not verdicts.** No diagnosis, no warnings, no
  medication advice. Cycle windows are ranges; phases are estimates;
  interaction hits are sourced facts about timing. If you set your own
  limit for a medication, the journal reports `count_in_window` against
  your `max_count`, and nothing more.
- **Your words stay yours.** The journal stores the short label and note
  the agent passes, never your whole message. You can delete one entry or
  purge everything.

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

Only the pressure tool needs the key. Everything else works without it, and
without a key the pressure tool returns `missing_key` instead of failing.

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
| `omonpakari_log` | `add` a `symptom`, `medication`, `supplement`, `food` or `note` with the user's own `label`, optional `severity` (1-5), `dose`, `note`, `at`; `list` entries with filters; `delete` one; `set_limit` for a medication; `purge` everything (`confirm: true`). `at` may not be in the future or more than 10 years back. Each new entry gets the nearest stored pressure reading (within 90 minutes) without a network call. |
| `omonpakari_cycle` | `mark` a period `start` or `end`; `status` returns observed cycle lengths (average, shortest, longest), the next window as a range of dates, days since the last start and the current phase as an estimate; `delete` one mark. A single predicted day is never produced. |
| `omonpakari_interactions` | `check` scans the journal for entries within the spacing window (default 2 hours, configurable) of an iron entry that are known to inhibit or promote its absorption, with the fact and its source, and attaches the person's own alternative when a life note provides one; `table` returns the reference table itself. |
| `omonpakari_life` | `set` a life note under a short topic, in the user's words, with an optional `relates` (what this is the person's alternative to); `list`; `delete` by topic. Only what the user taught — the tool never invents a note. |
| `omonpakari_correlate` | Hourly pressure series from stored readings. Reports the baseline share of low and falling hours, the share of your entries in those hours, their ratio (`lift`), mean pressure change before entries versus all hours, and raw counts; with two or more observed cycles it also breaks entries down `by_phase`. Returns `insufficient_data` below 5 entries with pressure spread over at least 3 days, or below 72 hours of coverage. `small_sample` is true below 10 days of entries. |

The bundled skill `omonpakari:omonpakari` tells the agent how to use all of
this: consider first, check combinations before proposing them, prefer the
person's own alternatives — without nagging or diagnosing.

## Settings

All optional, under `plugins.entries.omonpakari.settings`:

| Key | Default | Meaning |
|---|---|---|
| `latitude`, `longitude` | none | Saved location |
| `low_hpa` / `very_low_hpa` / `high_hpa` | 1010 / 1003 / 1020 | Level thresholds (hPa) |
| `drop_hpa` | 6 | A change of at least this within `window_h` counts as falling or rising |
| `window_h` | 24 | Hours for past and forecast change (3-72) |
| `interaction_window_h` | 2 | Hours around an iron entry that count as "near" for the absorption check (0.5-12) |
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

omonpakari helps a person keep track of how they feel and what they take.
It does not diagnose, and it does not suggest starting, stopping or changing
any medication. The interaction table reports sourced facts about absorption
timing only.

## Development

```bash
python -m pytest            # offline; OpenWeather and Hermes storage are faked
hermes plugins validate .
hermes plugins doctor . --ci
```

## License

MIT
