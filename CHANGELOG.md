# Everlast by Sarekrn: changes

Each update raises the version by 0.1.

## v1.7
- Boats: timers to the next arrival at each port, started by the boat captains'
  announcements, as in EQTool: Barrel Barge (Oasis / Timorous Deep), Bloated Belly
  (Overthere), Maiden's Voyage (Butcherblock / Firiona Vie) and the Northern Desert of Ro /
  Iceclad boat. The timers renew for the next lap for two hours after the last
  announcement, and a banner/voice alert says when a boat arrives.
- Ring War: when Seneschal Aldikar calls "TROOPS, TAKE YOUR POSITIONS!" timers start for all
  3 waves (7 rounds 210 s apart, then a 300 s break), with a banner/voice alert at each round
  and break.
- CH chain: reads "001 CH -- Target" calls (also letters like "A CH Tank" and RCH) from
  raid, group, guild, say and ooc chat, shows each call as a 10 s timer grouped by target, and
  says "C H warning" when the slot just before yours is called (your slot is the position of
  a call you made yourself; the wrap-around from the end of the chain to slot 1 is handled).
  Optional tag: only calls starting with it count.
- Each of the three has its own switch in Settings, Timers (the feature), Alerts (banner) and
  Voice (spoken). All three appear in the Buff Timers window.

## v1.6
- Warns when the log you are following has been silent for a minute and a half: the status
  line at the bottom of the main window says so and reminds you about /log on. A character
  whose in-game logging is off writes nothing, so no timers, maps or alerts can work for it.

## v1.5
- Each character's level is remembered (learned from level-ups and /who lines). Switching
  characters used to fall back to "level 60 (assumed)", which skews spell durations that
  depend on level, until the log happened to mention the level. Type /who once on a
  character and Everlast keeps it from then on.

## v1.4
- Character log choice rewritten. Channel chat (guild, auction, /ooc) is written into the
  log of every client that is logged in, so an idle character's log can be the newest
  without being the one you play. Everlast now follows the log that gets lines only the
  playing client writes ("You hit...", "You have entered...", "Welcome to EverQuest!"); chat
  alone never moves it. On start it picks the log with the newest such line.
  (Tools > Character log still lets you pick one by hand.)

## v1.3
- Switching characters: Everlast now follows whichever character log is being written to
  by watching every eqlog file's size as well as its modified time (some setups don't keep
  the time current while the game holds the file open), checking once a second. When it
  moves to another character it forgets the last one's timers, fights, zone and Mana Sieve
  counts instead of carrying them over.
- Tools > Character log: pick a character's log by hand (or go back to Automatic) if
  Everlast ever follows the wrong one. The choice is not saved.

## v1.2
- Unlocked overlays can always be moved and resized: drag the window's toolbar or body
  (the DPS meter, for one, had nothing to grab if the window manager did not draw a title
  bar), and the resize grip in the corner is always there while unlocked.

## v1.1
- Minimizing now leaves only the tray icon: the taskbar button goes (it is hidden after
  the window manager's minimize animation finishes, which keeps Cinnamon stable). Turn it
  off in Settings, General ("Minimizing leaves only the tray icon") to keep the taskbar
  button. If the tray icon is not available, the window just minimizes as normal.

## v1.0
- /consider alert: when you consider a mob the banner shows and the voice says "KOS" if it
  glares at you threateningly or scowls at you, ready to attack, and "SAFE" for every other
  result (ally, warmly, kindly, amiably, indifferently, apprehensively, dubiously). Settings,
  Alerts and Voice tabs each have a switch for it.

## v0.9
- Mana Sieve counter (new "Mana sieve" window): counts every "<mob> staggers with mental
  anguish." in your log, whoever cast the sieve, so an enchanter can tell when a mob is out
  of mana. One row per mob with the count and how long since the last sieve. The count
  disappears when the mob dies (or you zone) and starts again at the next sieve. A mob
  nobody has sieved for 15 minutes is dropped. The Clear button resets all counts. When
  overlays are locked, the window hides itself while there is nothing to count.

## v0.8
- Fix for Cinnamon crashing (Muffin, in its window-stack sync): the tray icon's window was
  being shown as a normal window for an instant before the panel adopted it. It now stays
  unmapped until the panel takes it, which is what the tray protocol expects.

## v0.7
- Safer minimize: v0.6 hid the window the instant it was minimized, which could crash
  Cinnamon mid-animation. Minimizing is now the plain window-manager minimize again; the
  tray icon restores it. Removing it from the taskbar when minimized is an opt-in
  (Settings, General) and waits for the animation to finish. Launch with
  `EVERLAST_NO_TRAY=1 everlast` to run with no tray at all.

## v0.6
- New Everlast icon (a brass hourglass on slate): in the title bar, the app menu and the
  system tray.
- System tray: Everlast sits in the tray. Minimizing the main window hides it there; click
  the icon to bring it back or hide it, right-click for Show/Hide, Lock overlays, Settings
  and Exit. Settings, General tab: show in tray, minimize to tray, start hidden in the
  tray. If your panel has no tray, nothing is hidden and minimize works as usual.

## v0.5
- Custom triggers (Settings, Triggers tab): "when a log line looks like this, show this
  and/or say this". All 15 of EQTool's built-in triggers (ids 100-114) are there. Add, edit,
  turn on/off, delete, reset to defaults, try a log line against them, and import or
  export EQTool's trigger file format. `{name}` in a pattern captures words you can reuse
  in the text. Two of them (spell interrupted and fizzle) start off, because Everlast
  already announces those and names the spell.
- Dragon Roar: "DRAGON ROAR!" and "Dragon Roar out" when it lands, then a countdown to the
  next one (36 s): "in 6 Seconds!" down to "in 1", spoken at 6, 4, 2 and 1.
- Raid timers for the zone-wide spells of EQTool's raid mobs (Dragon Roar, breaths, clouds
  and so on, in 12 zones: Temple of Veeshan, Veeshan's Peak, Skyshrine, Sebilis, Nagafen,
  Vox, Dreadlands and more) using each spell's recast time.
- Enrage warning: "<mob> ENRAGED" (spoken), then "<mob> ENRAGE OFF" 12 s later, or sooner
  if the game says it is no longer enraged.
- Death Touch warning: when Fright or Dread call out a name, "DEATH TOUCH: <name>" (spoken),
  or "DEATH TOUCH ON YOU!" if it is you, with a 45 s "--DT--" timer.
- Raid spells with no duration (a roar or breath) are now recognised when they land.

## v0.4
- Pansy trap: now recognises the real message, "a grumpy tree throws a bunch of pansies at you".

## v0.3
- Pansy trap alert (banner and voice, Plane of Mischief). The game has no known trap
  message, so it fires on the first pansy that shows up, on a system line about a trap,
  or on any phrase you add under Settings, Alerts, "Extra trap phrases". Once a minute at most.

## v0.2
- Damage-over-time, root, snare and other detrimental timers on mobs now last one tick
  (6 s) longer, as in EQTool, so they no longer end a tick early. Buffs, spells on
  players and your own right-click duration corrections are unchanged.

## v0.1
- Renamed to Everlast by Sarekrn; the version shows in the title bar and main window.
- Main window tidied: Windows and Opacity tabs, a Tools menu (refresh, diagnostics,
  click-through test), and an Exit button.
- Tabs everywhere keep the dark theme, with brass text on the chosen tab.
- Settings split into General, Timers, Map, Alerts and Voice tabs.
- Group / raid invite alert and voice.
- Alert banner is fully transparent behind the text (opacity slider to 0%).
- "Mob names" checkbox on the map.
