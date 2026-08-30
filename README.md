# Discord Time Tracking Bot

A Discord bot for tracking time spent on activities/commitments with per-user statistics.

## Features

- **Activity Management**: Create and list activities to track
- **Time Tracking**: Clock in/out of activities
- **Per-User Tracking**: Each user tracks their own time
- **Statistics**: View time tracked for:
  - Today
  - This week
  - All-time (per activity)
- **Current Status**: Check what you're currently clocked into and how long

## Setup

1. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Create a Discord Bot** (if you haven't already):
   - Go to [Discord Developer Portal](https://discord.com/developers/applications)
   - Create a new application
   - Go to "Bot" section and click "Add Bot"
   - Copy the bot token

3. **Configure the token**:
   - The `.env` file should already have your `DISCORD_TOKEN`
   - Make sure it's set correctly

4. **Run the bot**:
   ```bash
   python main.py
   ```

## Commands

All commands are slash commands and will appear in Discord's command preview when you type `/`.

### Activity Management
- `/activity create <name>` - Create a new activity (admin only)
- `/activity list` - List all activities
- `/activity delete <name>` - Delete an activity (admin only)
- `/activity icon <name> <image>` - Set an icon for an activity (admin only)

### Time Tracking
- `/clockin <activity_name> [user]` - Clock in to an activity (admins can optionally specify another user)
- `/clockout [user]` - Clock out of current activity (admins can optionally specify another user)
- `/status` - View current status and elapsed time

### Statistics
- `/stats [user]` - View your stats (today, this week, all-time) with heatmap and metrics
- `/leaderboard <activity_name>` - View leaderboard for an activity (top 10 users)

### Admin Role Management (Server Admin Only)
- `/admin add <role>` - Add a role that can create activities
- `/admin remove <role>` - Remove admin privileges from a role
- `/admin list` - View all configured admin roles

### Bot Channel Management (Server Admin Only)
- `/channel add <channel>` - Restrict bot to work in a channel
- `/channel remove <channel>` - Remove channel restriction
- `/channel list` - View all allowed channels (if none set, bot works everywhere)

### Time Entry Management (Admin Only)
- `/time add <user> <activity> <minutes> [date]` - Add time to a user in minutes (date: YYYY-MM-DD HH:MM, YYYY-MM-DD, today, yesterday; defaults to now)
- `/time remove <user> <activity> <minutes> [date]` - Remove time from a user in minutes (date: YYYY-MM-DD HH:MM, YYYY-MM-DD, today, yesterday; defaults to now)

### Help
- `/commands` - Show all available commands organized by category

## Database

The bot uses SQLite (`time_tracker.db`) to store:
- **Activities**: List of activities to track
- **Time Entries**: Clock in/out records with timestamps per user

## Example Workflow

1. Create activities:
   ```
   /activity create Work
   /activity create Exercise
   /activity create Reading
   ```

2. View available activities:
   ```
   /activity list
   ```

3. Clock in:
   ```
   /clockin Work
   ```

4. Check status:
   ```
   /status
   ```

5. Clock out:
   ```
   /clockout
   ```

6. View all stats with heatmap:
   ```
   /stats
   ```
   Shows today, this week, all-time breakdown, plus a 12-week heatmap and key metrics (streak, max day, average, most active day)

   View someone else's stats:
   ```
   /stats @username
   ```

7. View leaderboard for an activity:
   ```
   /leaderboard Work
   ```

## Admin Features

### Clock in/out other users
Admins can optionally specify a user parameter to clock in/out other users:
```
/clockin Work @user
/clockout @user
```

### Manage admin roles
Server admins can manage which roles have admin privileges:
```
/admin add moderator
/admin remove moderator
/admin list
```
