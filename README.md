# Discord Time Tracking Bot

A Discord bot for tracking time spent on activities: clock in and out, per-user
stats with a 12-week activity grid and leaderboards, manual session editing, and
a few joke commands.

## Setup

1. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

2. Create a bot at the [Discord Developer Portal](https://discord.com/developers/applications)
   and copy its token. Enable the **Server Members** and **Message Content**
   privileged intents. Invite it with the `bot` and `applications.commands`
   scopes; `/impersonate` also needs **Change Nickname** and **Manage Roles**.

3. Put the token in a `.env` file next to `main.py`:

   ```
   DISCORD_TOKEN=your-token-here
   ```

   Set `BOT_DB` to keep the database outside the project directory.

4. Run it:

   ```bash
   python main.py
   ```

## Commands

`<required>`, `[optional]`. Every reply is public. An omitted `[activity]` falls
back to the server default; with no default set the command says so rather than
guessing.

### Time tracking
- `/clockin [activity]` — start tracking
- `/clockout` — stop and record the session
- `/pause` / `/resume` — stop and restart the clock
- `/cancel` — discard the active session without recording it

The clock-in message carries **Clock out**, **Pause**, **Resume**, and **Cancel**
buttons, keeps the activity icon, and shows a live status — `Running since 20
minutes ago`, or `Paused at 1h:30m:00s` with what was already tracked. Only the
member who clocked in can press them, `/pause` and `/resume` update the same
message, and the buttons keep working after a restart.

Clocking out posts a separate summary naming the session's id, with **Edit** (date
and duration) and **Note** buttons that open a popup and redraw the summary in
place. Running `/clockin` while a clock is open reports what is running, for how
long, and links to its message.

### Sessions
- `/session add <duration> [date] [activity]` — record a session that was never clocked, reporting its id; `date` defaults to today
- `/session list` — your sessions grouped by date, each with its id
- `/session edit <id> [date] [duration]` — change a session's date and/or duration
- `/session remove <id>` — delete one of your own sessions
- `/session combine <ids>` — merge sessions sharing an activity and date, keeping the lowest id; `ids` is comma-separated, such as `11, 12, 13`
- `/session tag <note> <id>` — attach a note to a session

Sessions are always named by the id shown in `/session list` and on the clock-out
summary. An admin can edit, combine, or tag another member's session by id, but
nobody can remove a session that is not their own. Notes cap at 500 characters.

### Activity management
- `/activity add <name>` — add an activity (admin only)
- `/activity remove <name>` — remove an activity and its icon (admin only)
- `/activity list` — list all activities
- `/activity icon <name> <image>` — set an activity's icon (admin only)
- `/activity default [name]` — set the server default, or omit `name` to view it (admin only to set)
- `/activity if <wage> [activity]` — estimate everyone's earnings at an hourly wage

### Statistics
- `/stats [user]` — today, this week, and all-time totals, a 12-week activity grid, and key metrics; `user` defaults to yourself
- `/leaderboard [activity]` — top 10 members for an activity

### Timezone
- `/timezone [timezone]` — view or set your timezone; start typing to preview zones with their current offset and local time

Your timezone decides which calendar day a session lands on and where "today",
"this week", and your streak begin. Members without one use server time, and each
member sets only their own.

### Fun
- `/quote add <text>` — add a quote
- `/quote remove <id>` — remove a quote (admin only)
- `/quote list` — list quotes with their ids
- `/gnaij` — post a random quote; saying **gnaij** in chat does the same
- `/vouch` — post random agreement from a fixed, uneditable pool
- `/say <message>` — make the bot repeat a message
- `/impersonate <user>` — copy a member's avatar, nickname, and colour onto the bot (admin only)

These post as plain channel messages rather than command replies, so the bot
appears to speak on its own; only errors reply to whoever ran the command. None of
them can ping a role or `@everyone`. Quotes are shared by every server the bot is
in, and `/quote list` shows the first 200.

`/impersonate` puts the colour on a **Flair** role the bot creates and owns, so no
member's own role is touched — and the bot's own role must stay colourless for it
to show. The avatar is account-wide and Discord rate limits it to roughly twice an
hour. There is no undo: reset the nickname in Server Settings and the avatar in
the Developer Portal.

### Server setup (server admins only)
- `/admin add|remove <role>` — grant or revoke bot admin privileges for a role
- `/admin list` — list configured admin roles
- `/channel add|remove <channel>` — restrict the bot to a channel, or lift that
- `/channel list` — list allowed channels; none configured means everywhere

`/admin`, `/channel`, and `/commands` stay usable in every channel, so an admin
cannot lock themselves out.

### Help
- `/commands` — show all commands grouped by category

## Input formats

**Durations** — `2h`, `15m`, `45s`, `2h15m`, `2h 15m 45s`, `2h:15m:45s`, or
`H:M:S` as `2:15:45`. Units run largest to smallest, the separator between them is
optional, and hours are not capped at 24. Every number needs a unit, so `120` is
rejected — write `120m`. A bare `M:S` such as `5:30` is rejected too — write
`5m30s`. Durations display as `2h:15m:45s`.

**Dates** — `MM-DD` or `MM-DD-YYYY`, with slashes allowed. The month always comes
first, so `2026-09-25` is rejected — write `09-25-2026`. An omitted date means
today in your timezone. Dates display as `MM-DD-YYYY`.

## Permissions

- **Server admin** — holds Discord's Administrator permission. Required for
  `/admin` and `/channel`.
- **Bot admin** — has Manage Server, or a role added with `/admin add`. Required
  to manage activities, remove quotes, use `/impersonate`, and edit, combine, or
  tag another member's session.

No command acts on another member's behalf. `/stats [user]` is the only one that
reads another member's data, and any member may use it.

## Project layout

```
main.py               Startup, button re-arming, channel check, error handling, events
bot_commands/         One module per feature area, each exposing register(tree)
  activity_commands.py  /activity
  tracking.py           /clockin /clockout /pause /resume /cancel, clock buttons
  statistics.py         /stats /leaderboard, activity grid and streak logic
  session_commands.py   /session
  user_timezone.py      /timezone
  administration.py     /admin /channel
  impersonate.py        /impersonate
  quotes.py             /quote /gnaij /vouch /say
  help_command.py       /commands
utils/                Shared helpers, no command definitions
  db.py                 Connection handling, schema creation and migration
  permissions.py        Admin-role and channel checks (cached per guild)
  icons.py              Activity icon storage
  timezones.py          Timezone lookup, previewing, per-user resolution
  activities.py         Activity lookup and autocomplete
  discord_utils.py      Embed and reply helpers
  time_utils.py         Duration and date parsing/formatting
  session_utils.py      Session SQL and list rendering
```

Everything that persists lives in SQLite (`bot_data.db` by default) — activities
and their icon bytes, sessions, quotes, admin roles, allowed channels, timezones —
so replacing the Python files never loses data. The schema is created and upgraded
at startup, and a database still named `time_tracker.db` is picked up
automatically.

## Example workflow

```
/activity add Work
/activity icon Work <attach image>
/timezone timezone:America/Los_Angeles
/clockin Work
/pause
/resume
/clockout
/stats
/leaderboard Work
```
