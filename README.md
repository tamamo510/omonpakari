# omonpakari

An unhurried health companion plugin for [Hermes Agent](https://github.com/NousResearch/hermes-agent).

*Omonpakari* (慮り) is a Japanese word for considerate foresight: thinking ahead
for someone, including what they have not said out loud.

## What it is for

Many health trackers remind, prompt and nudge. For people who live with
chronic fatigue, attention differences or weather-sensitive symptoms, those
nudges are pressure, and pressure makes things worse.

omonpakari takes the opposite stance:

- **It never starts the conversation.** No reminders, no check-ins, no
  "did you take your medication?".
- **It keeps context in mind quietly.** Before the agent replies, the plugin
  can place a short, factual note about recent weather pressure and the
  user's own notes, so the agent understands the situation without asking.
- **It answers when asked.** When the user brings up a headache or a bad day,
  the agent can look at pressure changes and past notes together with them.

## Status

Under construction for the Hermes Agent Plugin Hackathon (November 2026).
This first commit is the skeleton only: the plugin loads and registers nothing yet.

Planned surfaces:

| Surface | Purpose |
|---|---|
| Tool: pressure lookup | Current and recent barometric pressure (OpenWeather) |
| Tool: notes | Log symptoms and medication in the user's own words |
| Tool: correlation | Look at notes against pressure changes, on request |
| Hook: `pre_llm_call` | Place a short, quiet context note before the reply |
| Storage: `plugin_db` | Notes stay in the local Hermes plugin data directory |
| Skill | How to talk about health without nagging or diagnosing |

## Privacy and network

- Notes are stored locally under the Hermes plugin data directory. Nothing is
  uploaded by this plugin.
- The only planned network call is to the OpenWeather API for pressure data,
  using a key the user provides through Hermes (`OPENWEATHER_API_KEY`).
  No key is stored in this repository.
- No telemetry.

## Not medical advice

omonpakari helps a person keep track of how they feel. It does not diagnose,
and it does not suggest changing medication.

## License

MIT
