Calendar Bot is a simple discord bot that interacts with a google calendar to assist in scheduling events.
Our group uses it to schedule their D&D sessions on a shared google calendar.

It has 3 slash commands:

/create (title, date)
Creates an all day event for the day entered.

/games
Lists up to 10 events with game in the title.

/list
Lists the next 10 events. A green check will appear before any event with game in the title. All other events will be proceeded by a red X.
(We use it to show when players are unavailable).

Note:
The bot will check the environmental variable to find the calendar, discord, and guild IDs.
credentials.json is also required
