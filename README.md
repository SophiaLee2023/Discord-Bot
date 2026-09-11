# Discord Time Tracking Bot

A Discord bot for tracking time spent on activities/commitments, with per-user
statistics, an activity heatmap, and manual session editing.

## Features

- **Activity management** — create activities, give them icons, pick a server default
- **Time tracking** — clock in/out, pause, resume, cancel
- **Per-user stats** — today, this week, all-time, plus a 12-week heatmap and key metrics
- **Manual sessions** — add, edit, combine, tag, and remove recorded sessions
- **Leaderboards** — rank everyone who tracked a given activity
- **Per-user timezones** — day boundaries and streaks follow each member's own timezone

## Setup

1. **Install dependencies**

   ```bash
   pip install -r requirements.txt
   ```

2. **Create a Discord bot** at the [Discord Developer Portal](https://discord.com/developers/applications),
   add a bot to the application, and copy its token. Enable the **Server Members**
   and **Message Content** privileged intents.

3. **Grant permissions** — `/impersonate` needs **Change Nickname** and
   **Manage Roles**. Everything else works with the default permissions Discord
   gives a bot on invite. The OAuth scopes are `bot` and `applications.commands`.

4. **Configure the token** — put it in a `.env` file next to `main.py`:

   ```
   DISCORD_TOKEN=your-token-here
   ```

   Optionally set `BOT_DB` to point at a database outside the project directory,
   which keeps it clear of anything that replaces the source files.

5. **Run the bot**

   ```bash
   python main.py
   ```

## Project layout

```
main.py               Startup, global channel check, error handling, event handlers
bot_commands/         One module per feature area, each exposing register(tree)
  activity_commands.py  /activity
  tracking.py           /clockin /clockout /pause /resume /cancel
  statistics.py         /stats /leaderboard (heatmap and streak logic)
  session_commands.py   /session
  user_timezone.py      /timezone
  administration.py     /admin /channel
  impersonate.py        /impersonate
  quotes.py             /quote /gnaij /say
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

Everything that persists lives in the SQLite database, so replacing the Python
files never loses data — including activity icons.

## The default activity

Every command that takes an activity treats it as **optional**: `/clockin`,
`/session add`, `/leaderboard`, and `/activity if`. Omit it and the command uses
the server's default activity, set by an admin with `/activity default <name>`.

If nothing is given and no default is configured, the command reports that
rather than guessing — it will never silently pick an activity for you.

## Input formats

### Durations

Anything that takes a duration accepts exactly these forms:

| Input | Meaning |
| --- | --- |
| `120` | 120 minutes (a bare number is always minutes) |
| `2h` | 2 hours |
| `15m` | 15 minutes |
| `45s` | 45 seconds |
| `2h 15m` | 2 hours 15 minutes |
| `2h 15m 45s` | 2 hours 15 minutes 45 seconds |
| `2h:15m:45s` | same, colon-separated |
| `2:15:45` | hours:minutes:seconds |

Units must appear in descending order (`h`, then `m`, then `s`). A bare `M:S`
such as `5:30` is **not** accepted — write `5m 30s` or `0:05:30`.

Durations are always displayed back as `2h:15m:45s`, with minutes and seconds
padded to two digits.

### Dates

Dates accept `YYYY-MM-DD`, `MM-DD`, or `MM-DD-YYYY`, and slashes work in place of
dashes. Where a date is optional it defaults to today in your timezone.

## Commands

`<angle brackets>` are required, `[square brackets]` are optional.

### Activity management
- `/activity add <name>` — add a new activity (admin only)
- `/activity remove <name>` — remove an activity and its icon (admin only)
- `/activity list` — list all activities
- `/activity icon <name> <image>` — set an activity's icon (admin only)
- `/activity default [name]` — set the server default activity to `name`, or omit it to view the current one (admin only to set)
- `/activity if <wage> [activity]` — estimate everyone's earnings at an hourly wage; `activity` defaults to the server default

### Time tracking
- `/clockin [activity] [user]` — start tracking time; `activity` defaults to the server default, `user` clocks in another member (admin only)
- `/clockout [user]` — stop tracking and record the session; `user` clocks out another member (admin only)
- `/pause` — pause your running clock
- `/resume` — resume your paused clock
- `/cancel [user]` — discard your active session without recording it; `user` targets another member (admin only)

### Statistics
- `/stats [user]` — today, this week, and all-time totals, a 12-week heatmap, and key metrics; `user` defaults to yourself
- `/leaderboard [activity]` — top 10 members for an activity; `activity` defaults to the server default

### Sessions
- `/session add <duration> [date] [activity] [user]` — record a session that was never clocked; `date` defaults to today, `activity` to the server default, `user` adds for another member (admin only)
- `/session list [user]` — list sessions grouped by date, each with its numeric id; `user` defaults to yourself
- `/session edit <id> [date] [duration]` — change a session's date and/or duration; provide at least one of the two
- `/session remove <id> [user]` — delete a session by its id; `user` targets another member (admin only)
- `/session combine <ids>` — merge sessions sharing a user, activity, and date; `ids` is comma-separated such as `11, 12, 13`, and the lowest is kept
- `/session tag <note> [id]` — attach a note to a session; `id` defaults to your active session

Only a session's owner or an admin may edit, remove, combine, or tag it.

### Timezone
- `/timezone [user] [timezone]` — view or set the timezone used for your daily stats; omit `timezone` to view or start typing it to preview zones with their current offset and local time, `user` targets another member (admin only)

Your timezone decides which calendar day a session lands on, and where "today",
"this week", and your streak begin. Members without one fall back to server time.

### Fun
- `/impersonate <user>` — copy a member's avatar, nickname, and role colour onto the bot (admin only)

Running it again switches to the new member and hands back the colour role the
previous run took. The bot keeps whichever identity was applied last; there is no
command to change it back — reset the nickname in Server Settings and the avatar
in the Discord Developer Portal.

Colour is handled by a role named **Flair** that the bot creates and owns. Each
call recolours it to match the member's display colour, so no member's own role
is ever touched and no permissions come along with it.

Discord shows a member's highest role that has a colour, so **the bot's own role
must be colourless** for Flair to take effect — that is the default for a bot's
integration role. If it has been given a colour, clear it in Server Settings →
Roles; the command says so when this happens. Do not instead drag Flair above the
bot's own role: a bot cannot edit a role at or above its highest one, so Flair
would show but never change colour again.

The avatar is an account-wide change, so it applies in every server the bot is
in, and Discord rate limits it to roughly twice an hour. The nickname is
per-server and needs **Change Nickname**; the Flair role needs **Manage Roles**.
Discord always shows the non-removable BOT tag, so the bot stays identifiable as
a bot.

### Quotes
- `/quote add <text>` — add a quote
- `/quote remove <id>` — remove a quote (admin only)
- `/quote list` — list all quotes
- `/gnaij` — post a random quote; saying "gnaij" in chat does the same thing
- `/vouch` — post random agreement from a fixed, uneditable pool of phrases
- `/say <message>` — make the bot repeat a message

### Server setup (server admins only)
- `/admin add <role>` — grant bot admin privileges to a role
- `/admin remove <role>` — revoke bot admin privileges from a role
- `/admin list` — list configured admin roles
- `/channel add <channel>` — restrict the bot to a channel
- `/channel remove <channel>` — lift a channel restriction
- `/channel list` — list allowed channels; none configured means the bot works everywhere

`/admin`, `/channel`, and `/commands` deliberately stay usable in every channel,
so a server admin cannot lock themselves out.

### Help
- `/commands` — show all commands grouped by category

## Permissions

Two levels:

- **Server admin** — holds Discord's Administrator permission. Required for
  `/admin` and `/channel`.
- **Bot admin** — has Manage Server, or holds a role added via `/admin add`.
  Required to manage activities, remove quotes, and act on other members.

## Database

SQLite (`bot_data.db` by default), holding activities and their icon bytes,
sessions, quotes, per-guild admin roles, allowed channels, default activity,
per-user timezones, and the id of the Flair role `/impersonate` maintains. The schema is created and upgraded automatically at startup,
so an existing database can be dropped in as-is. A database still named
`time_tracker.db` is picked up automatically, so an older deployment keeps
working after an update without being renamed.

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
