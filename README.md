# Discord Time Tracking Bot

A Discord bot for tracking time spent on activities/commitments, with per-user
statistics, an activity heatmap, and manual session editing.

## Features

- **Activity management** — create activities, give them icons, pick a server default
- **Time tracking** — clock in/out, pause, resume, cancel, with buttons on the clock-in message
- **Per-user stats** — today, this week, all-time, plus a 12-week heatmap and key metrics
- **Manual sessions** — add, edit, combine, tag, and remove recorded sessions, by command or from the clock-out message
- **Leaderboards** — rank everyone who tracked a given activity
- **Per-user timezones** — day boundaries and streaks follow each member's own timezone
- **Fun** — a shared quote collection, a catchphrase trigger, `/vouch`, `/say`, and `/impersonate`

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
main.py               Startup, clock-button re-arming, channel check, error handling, events
bot_commands/         One module per feature area, each exposing register(tree)
  activity_commands.py  /activity
  tracking.py           /clockin /clockout /pause /resume /cancel
  statistics.py         /stats /leaderboard (heatmap and streak logic)
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
| `2h` | 2 hours |
| `15m` | 15 minutes |
| `45s` | 45 seconds |
| `2h 15m` | 2 hours 15 minutes |
| `2h15m` | same, without the space |
| `36h` | 36 hours (hours are not capped at 24) |
| `2h 15m 45s` | 2 hours 15 minutes 45 seconds |
| `2h:15m:45s` | same, colon-separated |
| `2:15:45` | hours:minutes:seconds |

Units must appear in descending order (`h`, then `m`, then `s`), and the
separator between them is optional. Every number needs a unit, so a plain `120`
is **not** accepted — write `120m` or `2h`. A bare `M:S` such as `5:30` is not
accepted either — write `5m30s` or `0:05:30`. There is no day or year unit;
write long durations in hours.

Durations are always displayed back as `2h:15m:45s`, with minutes and seconds
padded to two digits and hours counting up without limit.

### Dates

Dates accept `MM-DD` or `MM-DD-YYYY`, and slashes work in place of dashes. The
month always comes first, so a year-first `2026-09-25` is **not** accepted —
write `09-25-2026`. Where a date is optional it defaults to today in your
timezone.

Dates are displayed back as `MM-DD-YYYY`, the same order they are typed in, so a
date read off `/session list` can be pasted straight back into a command.

## Commands

`<angle brackets>` are required, `[square brackets]` are optional. Every reply
is posted publicly in the channel, so anyone can read the answer — no command
replies only to the member who ran it.

### Activity management
- `/activity add <name>` — add a new activity (admin only)
- `/activity remove <name>` — remove an activity and its icon (admin only)
- `/activity list` — list all activities
- `/activity icon <name> <image>` — set an activity's icon (admin only)
- `/activity default [name]` — set the server default activity to `name`, or omit it to view the current one (admin only to set)
- `/activity if <wage> [activity]` — estimate everyone's earnings at an hourly wage; `activity` defaults to the server default

### Time tracking
- `/clockin [activity]` — start tracking time; `activity` defaults to the server default. The message it posts carries **Clock out**, **Pause**, and **Resume** buttons
- `/clockout` — stop tracking and record the session
- `/pause` — pause your running clock
- `/resume` — resume your paused clock
- `/cancel` — discard your active session without recording it

`/clockout` reports the new session's id alongside its duration, date, and any
note, so it can be edited, tagged, or removed straight away without looking it
up in `/session list`. `/session add` reports the id the same way.

That summary carries **Edit** and **Note** buttons of its own. Edit opens a popup
holding the session's current date and duration, so correcting a clock left
running all night is a matter of typing `2h` over what is there; either field can
be left alone. Note opens a popup for the session's note, prefilled with whatever
it already says, and submitting an empty box clears it. Both accept the same
formats the commands do, reject bad input without changing anything, and redraw
the summary in place rather than posting again. Only the session's owner can use
them — an admin who needs to change someone else's session still uses
`/session edit` or `/session tag` with its id.

The clock-in message is the live view of a session. Its **Status** line reads
`Running since 20 minutes ago` or `Paused at 1h:30m:00s · paused 5 minutes ago`,
written as a Discord timestamp so the elapsed time counts along in every client
without the bot touching the message again. A clock resumed after a pause also
says how much was `already tracked`, so the banked time is never hidden. The
activity icon stays in place, and only the member who clocked in can press the
buttons. Pausing and resuming edit that
message rather than posting anything new, and `/pause` and `/resume` update it
too, so the two ways of driving a clock never disagree. Clocking out — by button
or by command — always posts its summary as a new message and leaves the clock-in
message showing a final status with the buttons gone.

The buttons survive a restart. A press carries no memory of the session — the
clock-in buttons look it up by the id of the message they are on, which the
database records when `/clockin` posts it, and the clock-out buttons read the id
out of the `Session #12` line in the summary they are attached to. One view of
each kind registered at startup therefore drives every such message the bot has
ever sent. Pressing a button on a session that has since
been clocked out or cancelled simply settles that message to a final status and
takes the buttons off.

Running `/clockin` while a clock is already going does not just refuse. It names
the activity, shows how long it has been running, and offers a **Go to your
clock** link straight to the message holding that session's buttons. If the clock
is merely paused it asks for a `/resume` rather than telling you to clock out.

### Statistics
- `/stats [user]` — today, this week, and all-time totals, a 12-week heatmap, and key metrics; `user` defaults to yourself and is the **only** way to look at another member
- `/leaderboard [activity]` — top 10 members for an activity; `activity` defaults to the server default

### Sessions
- `/session add <duration> [date] [activity]` — record a session of your own that was never clocked, and report its id; `date` defaults to today, `activity` to the server default
- `/session list` — list your sessions grouped by date, each with its numeric id
- `/session edit <id> [date] [duration]` — change a session's date and/or duration; provide at least one of the two
- `/session remove <id>` — delete one of your own sessions by its id
- `/session combine <ids>` — merge sessions sharing a user, activity, and date; `ids` is comma-separated such as `11, 12, 13`, and the lowest is kept
- `/session tag <note> <id>` — attach a note to a session by its id

Every command here names its session by the numeric id `/session list` and the
clock-out summary both show; none of them guess at your active session. Every
session command acts on your own sessions, and no command takes a member —
there is no way to clock in, add, or list on another member's behalf. `/session
remove` is self-only even for admins: it always deletes the caller's own session,
so nobody can delete someone else's tracked time. An admin can still edit,
combine, or tag another member's session by passing its numeric id. Notes are
capped at 500 characters.

### Timezone
- `/timezone [timezone]` — view or set the timezone used for your daily stats; omit `timezone` to view, or start typing it to preview zones with their current offset and local time

Your timezone decides which calendar day a session lands on, and where "today",
"this week", and your streak begin. Members without one fall back to server time.
Each member sets only their own; not even an admin can set it for them.

### Fun
- `/quote add <text>` — add a quote to the shared collection
- `/quote remove <id>` — remove a quote by its id (admin only)
- `/quote list` — list quotes with their ids, oldest first
- `/gnaij` — post a random quote; saying "gnaij" in chat does the same thing
- `/vouch` — post random agreement from a fixed, uneditable pool of phrases
- `/say <message>` — make the bot repeat a message
- `/impersonate <user>` — copy a member's avatar, nickname, and role colour onto the bot (admin only)

Every command in this section posts as a plain channel message instead of a
command reply, so there is no "used /say" line naming whoever ran it — the bot
simply speaks. Errors (an empty quote, a missing id, no admin rights) still come
back as a normal reply to the member who ran the command.

Anyone can add a quote, and quotes are shared by every server the bot is in.
Empty quotes are rejected and surrounding whitespace is trimmed. `/quote list`
shows the first 200 quotes and footers the total when there are more.

`/gnaij` also fires on its own: any message containing **gnaij** as a whole word
gets a random quote back, in any capitalisation. Other bots never trigger it, and
it obeys the same channel restrictions as the slash commands. The bot also shows
"Playing /gnaij" as its status.

`/say`, `/vouch`, `/gnaij`, and the chat trigger post plain text rather than an
embed, and can never ping a role or `@everyone` — only user mentions survive, so
nobody can turn the bot into a mass-ping.

#### /impersonate

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
  Required to manage activities, remove quotes, use `/impersonate`, and edit,
  combine, or tag another member's session by id. Removing a session is never
  possible for anyone but its owner.

No command takes a member to act as — clocking in and out, `/cancel`,
`/session add`, `/session list`, and `/timezone` always apply to whoever ran
them. `/stats [user]` is the one command that reads another member's data, and
any member may use it.

## Database

SQLite (`bot_data.db` by default), holding activities and their icon bytes,
sessions (including the message their clock-in buttons live on), quotes,
per-guild admin roles, allowed channels, default activity, per-user timezones,
and the id of the Flair role `/impersonate` maintains. The schema is created and upgraded automatically at startup,
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
