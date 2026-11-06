# Redesign notes (working document)

Status 2026-11-06: implemented on this branch. Six tools (pressure, log,
cycle, interactions, life, correlate), the hook now places a "quiet picture
of today" with consideration guidance instead of the old restrictive line,
and the test suite covers the new behaviour (177 tests passing locally).
Upstream check (2026-11-06): current NousResearch/hermes-agent still uses
register(ctx), pre_llm_call and plugin-catalog YAML with a 40-hex SHA pin,
so the submission shape is unchanged; the compat contract is additive
(hook kwargs), which this plugin already follows.

Context: after the first working draft (3 tools + quiet-context hook, 149 tests
passing), a design review against the original October 26 concept found that
the draft had drifted from "thinking ahead for someone" into a passive logbook.
This branch rebuilds the insides. The platform work (Hermes plugin plumbing,
pressure fetching/caching, storage) stays.

## What was wrong

1. The hook's closing line restricted use to "only when asked / only on health
   topics". The concept's "only when the person speaks first" means *no
   proactive nudging* (no notifications, no pestering) — not "answer only
   what is asked". Consideration must work quietly inside ordinary
   conversation (plans, meals, going out), factoring in what was NOT asked.
2. No menstrual cycle at all. For irregular cycles a single predicted date is
   a lie; the design must return averages, spread, and a window (range).
3. No absorption/interaction check. The founding incident (June 2026): iron
   proposed together with cocoa, which inhibits iron absorption ~71%.
   "No medication advice" was over-extended and removed the pre-proposal
   check entirely. Checking sourced interaction facts against logged times
   is information, not advice.
4. Supplements and food were merged into one log stream with medications,
   and meals were not first-class records with times — which makes any
   interaction check impossible.
5. The correlate window (48h in the hook) cannot read the body's main axes:
   cycle phase needs weeks, absorption needs same-day timing, symptom
   patterns need months.

## Rebuild plan

Tools (5):

- `omonpakari_pressure` — unchanged.
- `omonpakari_log` — typed entries: symptom / medication / supplement /
  food / cycle / note, each timestamped, in the person's own words.
- `omonpakari_cycle` — log period start/end; returns average cycle length,
  spread, next window as a range, and current phase as a tentative
  estimate (never a single date for irregular data).
- `omonpakari_interactions` — a sourced table of common absorption
  interactions (iron inhibitors: cocoa, matcha/green tea tannins,
  black tea, coffee, soy protein, Ca/Mg/Zn, fiber; promoters: vitamin C,
  citric acid). Scans the log and reports, as facts, entries that fall
  within the person's configured spacing (default 2h) of an iron entry —
  together with a remembered personal alternative when one is set.
- `omonpakari_correlate` — counts overlaps against pressure AND cycle
  phase AND interaction collisions; window selectable (30d / 90d).

New first-class store: **life memory** — the person's habits and usual
choices (usual non-caffeine morning drink, morning routine, what warms
them up), taught by the person or distilled from repeated log patterns.
The hook places relevant life-memory notes next to the numbers.

Hook (`quiet_context`) rewrite: instead of "recent records + gag rule",
place a compact **quiet picture of today**: pressure now/trend, cycle
phase estimate, any interaction collisions today, recent log digest,
relevant life-memory notes. Closing guidance: use this quietly in any
topic; let it shape suggestions (including what NOT to propose, and what
alternative fits this person); do not lecture, diagnose, or recite
numbers. No pestering — that stays; but "never speak first" was the old
draft's mistake and is dropped: speaking first, when the moment genuinely
calls for it (host-provided turns), is part of omonpakari — a matter of
knowing the person, not of quotas or templates.

Copy: README's framing changes from "leaves the talking to you /
patterns on request" to "thinks ahead with you when you talk".

Two layers: this public core stays generic (no person's name or body in
code or defaults). Personal specifics live in the home deployment's
configuration only.

Tests to add: interaction collision detection; cycle range math with
irregular data (no fake single dates); hook contains phase + collisions
+ life-memory alternatives; the old restrictive closing line is gone.

Deadline: catalog submission by 2026-11-12 07:00 JST.
