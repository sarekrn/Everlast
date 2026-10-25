<img width="256" height="256" alt="everlast-256" src="https://github.com/user-attachments/assets/9e488d96-5477-44f4-b1fb-accd9a8f845b" />
# Everlast by Sarekrn

Everlast (formerly "EQTool Native") is a native Linux (and cross-platform) replacement for EQTool's core features on
Project 1999: spell and buff timers, a DPS meter, zone maps, and mob info with
respawn timers. It is written in plain Python with Tk, so it needs no Wine, no
.NET and no extra packages. It only reads your EverQuest log file, exactly like
EQTool does.

## Why a rewrite instead of a patch

The EQTool "Linux" download is the Windows WPF program packaged for Wine. WPF's
transparent, always-on-top windows are one of the least stable things you can run
under Wine, which is the usual source of the crashes. This version runs natively
and is built so a bad log line can never take it down: every parser step is
isolated, errors go to a log file, and the main loop always keeps running.

## Install

You need Python 3.8+ and Tk:

| Distro | Command |
| --- | --- |
| Debian / Ubuntu / Mint / Pop | `sudo apt install python3-tk` |
| Fedora | `sudo dnf install python3-tkinter` |
| Arch / Manjaro / SteamOS | `sudo pacman -S tk` |
| openSUSE | `sudo zypper install python3-tk` |

Then unzip this folder anywhere and run:

    ./install.sh        # optional: adds `everlast` and an app-menu entry
    ./everlast     # or: python3 -m eqtool_native

On first start it searches your Wine, Lutris, Bottles, Steam/Proton and ~/Games
folders for `eqgame.exe`. If it can't find it, Settings opens so you can pick the
folder yourself (the one with `eqgame.exe` and `spells_us.txt` in it).

In game, make sure logging is on: type `/log on`, or set `Log=TRUE` in
`eqclient.ini`. The newest `eqlog_*.txt` is picked up automatically, including
when you switch characters.

## Features

**Spell timers.** Buffs and debuffs are detected from the landing messages in
your client's `spells_us.txt`, grouped by target. Durations use the same P99
rules as EQTool (its formula table, caster-level rule and P99 spell fixes), at
your level (auto-detected from level-ups and `/who`, or set it in Settings).
If a spell's timer is ever wrong, right-click it and choose "Set duration for
..."; that correction is used for every future cast of the spell.
When several spells share a landing message, the one you just cast wins;
otherwise the timer is marked with `(?)`. Buffs are removed when their fade
message appears, debuffs when the mob dies or you zone. Long recasts get their
own timers. Right-click a timer to remove it; "Add timer" makes a custom one.

**Buff, mob and respawn timers** are three separate windows. Buff Timers holds
buffs on you and other players, recasts and custom timers. Mob Timers holds the
debuffs you've put on mobs (root, mez, slows, DoTs); untick "Debuffs here" in its
toolbar to keep debuffs with the buffs instead. Respawn Timers holds the respawn
timer of every mob you've killed, grouped by zone, with an "Add respawn" button
to start one by hand. Each window's "Clear" only clears its own timers, and
right-clicking a respawn or custom timer can restart it.

**Timer window size and scrolling.** Each timer window has a list height that you
choose, so it no longer grows with the number of timers. Drag the corner grip (the
window must be unlocked) or type the height in Settings ("Buff list height" and
so on). Unlocked, you see the window at the height you set; if the list is longer
there is a scrollbar and the mouse wheel scrolls it (Ctrl + wheel is still the
window's opacity). In play mode (locked) the window is only as tall as the timers,
up to your height, and starts at the top of the list, which is sorted with you
first and the soonest-ending timers first in each group; untick "In play mode,
shrink timer windows to fit a short list" to keep it always that tall. A list
that is longer than its window keeps a slim scroll strip on its right edge even in
play mode. It is the one part of the window that takes clicks (everything else
still lets them through to the game) and the mouse does not hide the window while
it is over the strip or while you are dragging the slider, so you can scroll
without unlocking. Reach it from the right-hand side, or slide onto it across
the bars (they step aside as you pass, then the strip is under the pointer). On a
desktop without X Shape support the whole window is simply click-through, as before.

**Which buffs the Buff Timers window lists.** "Buff timers show" in Settings
has three choices: every buff I can see, "buffs I cast + all on me" (the default),
or only buffs I cast. Only buffs are filtered; your debuffs, recasts and custom
timers are always shown. Hidden timers still run (and still announce, if you have
those switches on); they just aren't listed.

**DPS meter.** Melee, non-melee (nukes are credited to you when you were
casting), DoTs and damage taken, per mob. Step back through recent fights with
the arrows; "Copy" puts a one-line summary on the clipboard to paste in game.

**Map.** Maps for all 121 classic, Kunark and Velious zones are built in (the
same map set EQTool ships, extracted from its program file). If you keep your
own map pack in `EverQuest/maps`, those take priority. The zone is detected
from your log, including the zone you were already in when the app started; your position updates every time you
`/loc`, so bind `/loc` to a hotkey or put it in a social you press often.
Scroll to zoom, drag to pan, "Follow" keeps you centered.

**Settings tabs.** Settings is split into General, Timers, Map, Alerts and Voice. The EverQuest folder stays at the top.

**Mob names.** The "Labels" checkbox on the map shows or hides every label; "Mob names" hides just the red ones (the hostile mobs in the P99 maps) and keeps places, vendors and quest NPCs.

**Map levels (the z axis).** Zones with several floors on top of each other can
show just the floor you are on. Use the "Levels" row: "Current level only" hides
the other floors, "Dim other levels" fades them (EQTool's way), "All levels"
shows everything. Your height comes from `/loc`, so type it (or bind it) to keep
the map in step; until then everything is shown. The arrows step up and down
through the zone's floors (found from the map itself: long runs of flat lines
at one height) and "Me" comes back to yours, which is handy for seeing where a
staircase leads. "Level height" sets how thick one floor is; the readout under
the controls says exactly which heights are being shown. The defaults are
EQTool's own: its 85 single-level zones start on "All levels" and its 36
multi-level zones (Lower Guk, Old Sebilis, Unrest, Qeynos, Runnyeye, Chardok...)
on "Current level only". Whatever you choose is remembered per zone. As in
EQTool, a line counts as on your floor if either end is within one level height
of you.

To look at any other zone, use **Find zone** at the top of the map (Ctrl+F jumps
there). Type any part of a name, in any word order, and pick from the results
with the arrow keys and Enter or a click. It understands common shorthand (NK,
EC, LGuk, PoF, KC, ToV, SoNH...) and forgives small typos. The dropdown below it
lists every map by its full zone name if you'd rather browse. If a zone isn't
recognized when you enter it, choosing its map either way remembers it for next
time.

**Respawn timers for every kill.** When you kill something that gives you
experience, a respawn timer for it appears in the Mob Timers window, grouped
under the zone. The trigger is the same as EQTool's: a "You have slain ..." (or
"... has been slain by ...") line followed within a few seconds by "You gain
experience!!" or "You gain party experience!!". Kills that give you nothing, like
a grey con or something another group killed, get no timer. The time comes from
EQTool's own respawn table, which is built in (all 121 zones, plus per-NPC times
for some zones, such as Butcherblock's guards at 24:00); the order is your own
rule for that mob, then the table's NPC-specific time, then the wiki figure if you
have looked the mob up, then the zone's time, then 6:40 for a zone it does not
know. Each kill is its own timer, labelled with your last `/loc` if it is less
than a minute old. In Settings you can turn the timers off, or have them start on
every death instead of waiting for experience; an expiring respawn only shows a
banner if you tick "A mob you killed is due to respawn". The table is a snapshot
of EQTool 5.25.812.1; `tools/extract_respawns.py` rebuilds it from any later
EQTool zip: `python3 tools/extract_respawns.py EQTool_Linux_x_y.zip`.

**Mob info and respawns.** `/consider` a mob and its P99 wiki page (level,
class, HP, special attacks, loot, respawn time) appears in the Mob Info window.
Lookups run in the background and are cached for a week. "Set respawn..."
saves a respawn time for that mob so every kill starts a timer, labeled with
where you were standing. Or enable "Start a respawn timer on every kill" in
Settings to time everything.

If a future EQTool release has updated maps, import them (the .zip or the .exe
both work):

    python3 -m eqtool_native --import-eqtool-maps EQTool_Linux_x_y.zip

Imported maps go to `~/.local/share/eqtool-native/maps` and are preferred over
the built-in ones.

**Alerts and voice.** Charm breaks, root breaks and timers running out appear
as a big yellow banner (48 pt bold by default) and "Charm break" / "Root break"
are spoken aloud. Breaks are detected from the game's own "Your charm spell has
worn off." / "Your Root spell has worn off." messages, and a spell counts as a
root or charm from its effects in the spell file (plus EQTool's root list), so
every root and charm spell is covered. When a buff you cast on another player
fades, you get "Spirit of Wolf faded on Bob" on the banner and spoken aloud;
this comes from the game's own "Your ... spell has worn off." message (matched
to the right player using your timers) or from the timer running out, and is
never announced twice. The banner stays hidden until something
happens, so it never blocks clicks on the game. Click "Position alerts" in the
main window to move or resize it; while it's showing, scroll the mouse wheel
over it (or press + / -) to change the text size live, from 16 to 160. The
banner grows by itself if the text no longer fits. Click "Lock alerts" when
done. The size is also in Settings as "Alert text size". Settings has an
"Alerts & voice" column to choose what gets announced and spoken, the text size,
how long alerts stay up, and a "Test alert and voice" button.

Charm breaks and root breaks play recorded voice clips (included in
`eqtool_native/sounds`). Buff fades are announced by the computer voice, which
says which buff, and on whom: "Spirit of Wolf faded" when one falls off you,
"Spirit of Wolf faded on Bob" when one you cast on someone fades. A buff on you
is announced the moment the game says it has faded (or when its timer runs out,
whichever comes first, never twice). Under "Buff fades are announced" in
Settings you can choose the spoken name, the recorded "Buff faded" clip, or the
clip and then the name. Announcements play one after another, never over each
other, and the oldest are dropped if too many pile up. Urgent ones (fizzles,
interrupted casts, charm and root breaks, and watched spells) are played ahead of
anything else that is waiting.

**Custom triggers.** Settings, Triggers tab. A trigger says: when a log line matches this
pattern, show this text on the banner and/or say this. `{name}` in the pattern captures
words and can be used in the text, for example `^{who} backstabs {target} for {damage}
points` shows "{who} backstabs {target} for {damage}". Everlast ships EQTool's 15 built-in
triggers (spell did not take hold, out of mana, dispelled, regen faded, corpse out of range,
sense heading and so on). Use "Try a log line" to check one before you rely on it. The list
imports and exports in EQTool's own file format.

**Raid warnings.** Dragon Roar: the banner and voice say it landed, then count down to the next
one. A timer is also started for each raid mob's zone-wide spell (breaths, clouds). Enrage:
"<mob> ENRAGED", then "ENRAGE OFF" after 12 s. Death Touch: when Fright or Dread call out a
name you get "DEATH TOUCH: <name>" and a 45 s timer. The data comes from EQTool 5.25
(`tools/extract_raid.py`). Each has an on/off box for the banner and one for the voice
(Settings, Alerts and Voice).

**Pansy traps.** In the Plane of Mischief the banner says "PANSY TRAP!" and the voice says "Pansy trap" when a trap is tripped. It listens for "a grumpy tree throws a bunch of pansies at you" and, as a backup, fires on the first pansy that shows up, on a system line about a trap, or on any text you add in Settings, Alerts, "Extra trap phrases" (copy the exact line from your log). At most once a minute.

**Group invites.** When someone invites you to a group or raid ("Soren invites you to join a group.") the banner shows "GROUP INVITE: Soren" and the computer voice says it. Switch either off in Settings.

**Fizzles and interrupts.** When a spell fizzles ("Your spell fizzles!") or is
interrupted ("Your spell is interrupted."), the banner shows "FIZZLE: Spirit of
Wolf" / "INTERRUPTED: Spirit of Wolf" and the computer voice says "Spirit of Wolf
fizzled" / "Spirit of Wolf interrupted", naming the spell you were casting (just
"Fizzle" or "Interrupted" if it can't tell which). These are EQTool's own
patterns. Each has its own banner switch and voice switch in Settings, on by
default, and with no computer voice installed they at least make the system beep.

**Watched spells.** Some spells you always want to hear about when they end, such
as a DoT on a mob. The list in Settings ("Always announce when these wear off")
starts with the shaman epic's DoT, Curse of the Spirits (writing it "Curse of
Spirits" works too). When one wears off you get "Curse of the Spirits wore off on
a gnoll" on the banner and in the voice. It comes from the game's own "Your ...
spell has worn off." line, or the spell's own fade text, or the timer running
out, whichever happens first, and never twice. If the mob dies first there is
nothing to announce. To add a spell without typing, right-click its timer and
choose "Announce when ... wears off". In Settings you can preview them ("Play"), swap in
your own recording ("Choose...", any WAV/MP3/OGG/FLAC), go back to the
included one ("Default"), or untick "Use recorded clips" to hear the synthetic
voice instead. Clips play through PipeWire, PulseAudio or ALSA (WAV), or
ffplay, mpv, GStreamer, mpg123 or VLC (other formats), whichever you have.
If a clip can't be played, the synthetic voice says it instead.

The computer voice is a program already on your system. It tries `espeak-ng`,
`espeak`, `flite`, `festival` and finally `spd-say` (speech-dispatcher), in that
order; `spd-say` is last because it is often installed with nothing behind it,
and then it says nothing while reporting success. "Computer voice" in Settings
lets you pick one, and "Test computer voice" speaks a sample with the choice you
have selected and shows what the program really said, including its error
message if it failed, so a silent setup can be diagnosed. If none is found
Settings shows the install command for your distribution:

| Distro | Command |
| --- | --- |
| Debian / Ubuntu / Mint | `sudo apt install espeak-ng` |
| Fedora | `sudo dnf install espeak-ng` |
| Arch / Manjaro / SteamOS | `sudo pacman -S espeak-ng` |
| openSUSE | `sudo zypper install espeak-ng` |

Without a computer voice, buff fades fall back to the recorded clip. Any other
voice program works too: put its command in "Custom voice command", using
`{text}` where the words go (for example `piper-say {text}`).

**Transparency.** The main window has an Opacity slider for each overlay
(Buff timers, Mob timers, Respawn timers, DPS, Map, Mob info and the Alert
banner), from 0% to
100%. Each slider changes only its own window, live, even while the overlays
are locked, and every value is remembered. You can also hold Ctrl and scroll
over a window to change just that one (over the map, plain scrolling still
zooms); its slider follows along. See-through windows need a compositing
desktop (GNOME, KDE, Cinnamon, Xfce with compositing on, or picom).

The alert banner works the same way: its text is always 100% opaque, and its
slider fades only the dark panel behind it (the banner's text lives in its own
solid window, just big enough for the text and centred on the frame you
positioned, with a translucent panel behind it). At 0% the panel is gone and
only the text's own tight dark box remains. A true see-through background behind
the lettering would need per-pixel transparency, which Tk cannot do.

For the three timer windows the slider fades only the background: the bars and
their text are always 100% solid. (A Tk window can only be made see-through as
a whole, so a separate translucent panel sits behind the window and the slider
controls that.) The timer windows are always exactly as tall as their timers,
so there is no empty area; on an X11 desktop they are additionally cut down to
just their bars and text with the X Shape extension. On a Wayland desktop that
cutting is not used: XWayland shows the never-painted memory of a cut-away area
(leftover graphics from other windows), so there the window is kept fully painted
and only made tight. In play mode (locked) a timer window with no timers is
unmapped entirely. Because of that, the timer windows are always
borderless; drag the toolbar or the bars to move them and use the corner grip
to resize. If you'd rather have ordinary windows whose opacity covers
everything, untick "Solid timer bars over a see-through background" in
Settings. On Windows, where the Shape extension doesn't exist, the timer
windows use the ordinary whole-window opacity.

**Game clock vs. your PC clock.** EverQuest stamps its log with the clock of
the machine it runs on, and under Wine that clock can be in a different time
zone from the rest of your system (often exactly one hour out, from daylight
saving). Timers run on the log's timestamps, so they came out an hour too long
or too short. The app now compares each new log line with your PC's clock and
corrects for any whole-time-zone difference (the main window says when it is
doing so).

**Moving windows.** The timer windows have no title bar, so while they are
unlocked a gold strip across the top ("drag here to move") is the handle: drag
it to move the window, and use the grip in the bottom-right corner to resize.
The strip disappears when you lock the overlays. The map is always kept on top
of everything, whatever "Keep windows on top" says, and is put back within a
fraction of a second if clicking the game raises the game over it (it is left
alone while a menu or dialog is open).

**How a Tk window is built on X11 (and why it matters).** A Tk toplevel is a stack
of X windows: Tk's own window sits inside a "wrapper" window, and that wrapper is
what the screen, the compositor and the pointer deal with. `wm frame` and
`winfo id` both name only Tk's inner window. Click-through, the see-through cut-out,
the strip exception, the opacity hint and the decoration hints are therefore all
applied to the outermost window of the stack (found by walking up from Tk's window
to the one whose parent is the root), and the shape also on the windows inside it.
Diagnostics prints each overlay's whole stack so this can be checked. Before this,
they were applied to Tk's inner window alone, which left the wrapper fully clickable
(it swallowed every click) and unshaped (so where the inner window was cut away, the
wrapper showed whatever the screen last had there).

**X errors cannot take the program down.** Xlib's default response to a failed X
request is to print the error and exit the whole process, and Tk replaces windows
often enough that an occasional failed request is normal (a question about a
wrapper window that has just been thrown away, say). The overlay code uses its own
X connections, and a guard now logs and ignores errors on those, while errors on Tk's
own connection still go to Tk's handler. Before asking about a window, the code
makes Tk flush its pending requests so the window exists as far as the server is
concerned, and if a window in the stack is missing it simply uses what it found.
Diagnostics reports how many errors the guard has absorbed.

**If the program ever fails to start because of a window problem**, run it with
`EQTOOL_NO_XTRICKS=1 ./everlast`. That switches off every direct X window trick
(click-through, cut-out shapes, the opacity hint, the scroll strip, Diagnostics'
window inspection), so the overlays are plain windows, and everything else still
works. Please tell me what happened, with `~/.cache/eqtool-native/eqtool.log`.

**Leftover graphics.** A window whose pixels are never painted shows whatever was
last in video memory (pieces of other windows), on some desktops. So nothing is
left to the X server's own background fill: every overlay window is covered edge
to edge by a widget that paints it, the translucent panels behind the timers and
the alert banner are painted by a canvas of their own, and the timer windows are
only as big as their contents. If stray images still build up inside an overlay
while it stays on screen, "Refresh overlays" (main window) unmaps and re-maps all
of them, which is what hover-hide does as your mouse passes over one: it repaints
each window and puts it, and its panel, back in order. "Refresh overlays every
(s)" in Settings does the same on a timer (0 = never). Once a second the overlays
only re-assert what could have changed (click-through, stacking, and the shape or
panel only if their contents changed or Tk gave the window a new X window), so an
idle overlay is not rebuilt over and over. Diagnostics also lists any window that
is fullscreen or asks the compositor to step aside (Wine does that for fullscreen
games), which can switch off the transparency the overlays rely on: run it while
the game is up and the problem is showing.

**Hiding title bars.** Tick "Hide title bars on overlay windows" in Settings
and the Timers, DPS, Map and Mob Info windows lose their title bar and border
but otherwise work normally (you can still click, type and right-click in
them). Move a window by dragging its toolbar or any empty area, and resize it
with the grip in its bottom-right corner. On Linux this asks the window
manager to drop the decorations (the standard _MOTIF_WM_HINTS hint, honored by
GNOME, KDE, Xfce and most others).

**Locking and click-through.** Click "Lock overlays" in the main window to
put the overlays in play mode: every overlay loses its title bar, is fixed in
place (no accidental moving or resizing) and is kept on top of everything,
including a full-screen game. The ones with "Click-through" ticked (next to
their opacity slider) let every mouse click pass straight through to the game.
The click-through is re-applied every second, so it holds even if Tk or the
window manager recreates the window underneath. In this play mode the timer
windows also hide their toolbar and show nothing at all when no timers are
running.
Windows with it unticked stay interactive while locked; the map is like that
by default, so you can still drag to pan and scroll to zoom. Unlock to move or
resize windows. The alert banner is always click-through. Click "Unlock overlays" to get
normal windows back; the main window itself is never click-through. This uses
the X Shape extension on Linux (including XWayland) and the standard
transparent-window style on Windows; if neither is available the status line
says so.

**Diagnostics.** If overlays don't behave (they ignore opacity, or clicks don't
go through), use the three buttons at the bottom of the main window: Settings,
Diagnostics, and Click-through test (the same test is also inside the
Diagnostics window). The main window can no longer be shrunk below its
contents, so these buttons are always visible. Diagnostics reads the live
state of your windows. It reads the real window
state from your X server and reports, for every overlay: whether it is a true
unmanaged window, its click region and visible region, the opacity hint the
compositor was given, what is stacked above it, and whether a compositing
manager is running at all (without one, window opacity cannot work; the main
window also warns about this). "Probe pointer in 5 s" then tells you exactly
which window would receive a click at the mouse position: hold the pointer over
a locked overlay and it says either that the click passes through to (for
example) EverQuest, or that one of EQTool's windows still catches it. "Click-through
test" opens a window with a grey control button C and two buttons (A and B)
that each have a red see-through overlay over them, set up the way the locked
timer windows are (B is also cut to a shape). Click C, then inside each red square (D is the hover-hide test: move the mouse
onto its red square, wait for it to vanish, and click where it was; a line shows
the mouse position your desktop reports):
every click that reaches the window is counted and logged, the real positions
of the buttons and overlays are shown, and a plain-language conclusion says
whether the clicks passed through, were caught by the overlay, or went
somewhere else. It tests your actual desktop, independent of the game. "Copy all" puts the report on the clipboard; it is also saved to
`~/.cache/eqtool-native/diagnostics.txt`. On a Wayland desktop the report
explains the XWayland caveat instead of guessing.

**Wayland.** On a Wayland desktop this app runs through XWayland, and in our
testing the desktop hands any click on an overlay to its window and the X
server then drops it, so true click-through cannot work there: not for the
red squares in the click-through test, and not for the locked timer windows.
To limit the damage, timer windows are only as tall as their timers, so the
rest of the screen stays clickable ("Make timer windows only as tall as their
timers" in Settings). On top of that, a locked overlay (and the alert banner) hides itself
while your mouse is over it, so a click there reaches the game, and returns
when the mouse leaves ("Hide a locked overlay while the mouse is over it" in
Settings; on by default only on a Wayland desktop, where clicks on an overlay
really are lost, and applied to every overlay that has "Click-through" ticked;
the map is interactive by default, so it is not hidden unless you tick its
Click-through box). On X11 clicks pass straight through, so it is off unless you
turn it on. If the desktop stops reporting the pointer position (for example
over a native Wayland window) the overlay comes back after a few seconds rather
than staying hidden. For true click-through, log in to an X11 session (chosen
on your login screen; on KDE Plasma it is "Plasma (X11)"). The main window and
the Click-through test both show which kind of session you are in.

## Tips

- Always-on-top over the game works best with EverQuest in windowed or
  borderless-windowed mode.
- On Wayland the windows run through XWayland, which is fine for this.
- Settings live in `~/.config/eqtool-native/settings.json`; the error log and
  the mob cache are in `~/.cache/eqtool-native/`.

## Troubleshooting

To check what the parser sees in any log, without the UI:

    python3 -m eqtool_native --replay ~/path/to/eqlog_Name_P1999Green.txt

That prints detected zones, kills, the timers that would be running and a DPS
breakdown for the last fights. If something is detected wrong, the log lines
plus `~/.cache/eqtool-native/eqtool.log` are exactly what's needed to fix it.

To see exactly what your spell file says about one spell and the timer it
produces:

    python3 -m eqtool_native --spell-info "Bedlam"

Run the tests with `python3 -m unittest discover tests` (the click-through test
needs an X display; `xvfb-run` works too).

## Layout

    eqtool_native/
      logwatch.py   log line parsing, tailing the active log
      spells.py     spells_us.txt loader, buff duration formulas
      engine.py     turns log lines into timers / fights / zone / location
      trackers.py   timer and DPS bookkeeping
      maps.py       zone names and map file loading
      respawn.py    respawn times by zone / NPC (from respawn_data.json, EQTool's table)
      map_levels.json  EQTool's per-zone floor settings (level height, single-level flag)
      announcer.py  plays clips and speech one after another on a background thread
      mobinfo.py    P99 wiki lookups and caching
      maps_data/    built-in zone maps (from EQTool, originally community map packs)
      dotnet_resources.py  reads embedded resources from EQTool.exe (map import)
      settings.py   settings file and EverQuest folder auto-detection
      speech.py     text to speech through the system's speech program
      ui/xinspect.py     reads live X11 window state (shapes, opacity, stacking)
      ui/diagnostics.py  the Diagnostics window and pointer probe
      ui/           Tk windows

## System tray

Everlast puts its icon in the system tray (the classic X11 tray that Cinnamon, MATE, XFCE,
KDE and LXQt provide). Minimizing leaves only the tray icon (no taskbar button); click the icon to
show or hide it, right-click for the menu. All three behaviors can be switched off in
Settings, General. If the panel has no tray, Everlast leaves the window alone.

## Mana Sieve counter

The Mana sieve window counts the sieves that have landed on each mob, yours and everyone
else's (it reads "<mob> staggers with mental anguish."). The count goes when the mob dies
or you zone, and starts over at the next sieve.

## Boats, Ring War and the CH chain

All three are optional (Settings, Timers tab) and each has banner and voice switches.

- **Boats.** Stand near a boat captain and when the announcement shows up in your log, timers
  for the next arrival at each port appear in the Buff Timers window.
- **Ring War.** Seneschal Aldikar's "TROOPS, TAKE YOUR POSITIONS!" starts timers for every
  round and break of all three waves.
- **CH chain.** Chat calls such as `001 CH -- Beefwich` become timers; post your own slot
  (`002 CH -- Beefwich`) and Everlast says "C H warning" when slot 001 is called.
