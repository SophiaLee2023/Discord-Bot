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

### Activity Management
- `.activitycreate <name>` - Create a new activity (requires admin role)
- `.activitylist` - List all activities

### Time Tracking
- `.clockin <activity_name>` - Clock in to an activity
- `.clockout` - Clock out of current activity
- `.status` - View current status and elapsed time
- `.activityname` - Quick shortcut to clock in (e.g., `.work`, `.exercise`, `.reading`)

### Statistics
- `.stats` - View your stats (today, this week, all-time) with heatmap and metrics
- `.stats @user` - View another user's stats (optional)
- `.leaderboard <activity_name>` - View leaderboard for an activity (top 10 users)

### Admin Role Management (Server Admin Only)
- `.adminrole add <role>` - Add a role that can create activities
- `.adminrole remove <role>` - Remove admin privileges from a role
- `.adminrole list` - View all configured admin roles

### Bot Channel Management (Server Admin Only)
- `.channel add <channel>` - Restrict bot to work in a channel
- `.channel remove <channel>` - Remove channel restriction
- `.channel list` - View all allowed channels (if none set, bot works everywhere)

### Help
- `.help` - Show all commands

## Database

The bot uses SQLite (`time_tracker.db`) to store:
- **Activities**: List of activities to track
- **Time Entries**: Clock in/out records with timestamps per user

## Example Workflow

1. Create activities:
   ```
   .activitycreate Work
   .activitycreate Exercise
   .activitycreate Reading
   ```

2. View available activities:
   ```
   .activitylist
   ```

3. Clock in:
   ```
   .clockin Work
   ```

   Or use the quick shortcut:
   ```
   .work
   ```

4. Check status:
   ```
   .status
   ```

5. Clock out:
   ```
   .clockout
   ```

6. View all stats with heatmap:
   ```
   .stats
   ```
   Shows today, this week, all-time breakdown, plus a 12-week heatmap and key metrics (streak, max day, average, most active day)
   
   View someone else's stats:
   ```
   .stats @username
   ```

7. View leaderboard for an activity:
   ```
   .leaderboard Work
   ```
