# My Scripts and Tools

A personal grab-bag of setup scripts, utilities, and reference notes for Windows, Linux, and macOS.

- [Repo Contents](#repo-contents)
- [macOS Keyboard Shortcuts](#macos-keyboard-shortcuts)
- [Linux Keyboard Shortcuts](#linux-keyboard-shortcuts)
- [MongoDB Basic Commands](#mongodb-basic-commands)
- [Git Commit Format](#git-commit-format)
- [Robocopy Reference](#robocopy-reference)

---

## Repo Contents

### Windows setup and maintenance

| Script | What it does |
| --- | --- |
| `WinGetSystemRestore.bat` | Reinstalls a full personal app set via `winget`. |
| `CAPSystemRestore.bat` | Smaller `winget` app set for a work/CAP machine. |
| `WinGetNewProjectSetup.bat` | Minimal dev stack (JDK, Terminal, MongoDB, Sublime). |
| `SetJava.bat` | Sets `JAVA_HOME` to JDK 25 and adds it to `PATH`. Run as Administrator. |
| `NVIDIA-Monitor.bat` | Loops `nvidia-smi` once a second as a live GPU monitor. |
| `TraceRoute.bat` | Prompt-driven `tracert` wrapper. |
| `remove_nul_files.bat` | Deletes `*.nul` files and Windows reserved-name `NUL` files. |
| `install-maven.ps1` | Downloads Maven and adds it to `PATH`. |
| `Set-ClaudeGitBashPath.ps1` | Finds Git Bash and sets `CLAUDE_CODE_GIT_BASH_PATH` for Claude Code. |
| `add-claude-to-path.ps1` | Adds the Claude install directory to the user `PATH`. |
| `Start-OpenWebUI.ps1` | Starts an Open WebUI server (`-Host`, `-Port` params). |

### Linux

| Script | What it does |
| --- | --- |
| `install_comfyui_manjaro.sh` | Installs NVIDIA drivers, Python, and ComfyUI on Manjaro. |
| `archComCMD.sh` | Menu for Arch updates and MongoDB start/dump/restore. |
| `DarkSiteArch.sh` | Menu for Tor + nginx hidden-service setup on Arch. |
| `DarkSitePI.sh` | Same menu, Debian/Raspberry Pi flavor (`apt`). |
| `FunnyCMD.sh` | Menu for installing/running `cmatrix` and a terminal clock. |

Shell scripts saved from Windows may carry CRLF line endings. Strip them before running:

```bash
sed -i 's/\r$//' ~/Downloads/install_comfyui_manjaro.sh && bash ~/Downloads/install_comfyui_manjaro.sh
```

### Python and Java utilities

| Script | What it does |
| --- | --- |
| `code_to_markdown.py` | Converts `.java` and `.py` files in a directory to markdown with syntax highlighting. |
| `rtf_to_markdown.py` | Converts `.rtf` documents to markdown, installing dependencies as needed. |
| `archiver.py` | Tars up a Redmine backup with a dated archive name. |
| `WinEvenniaInstaller.py` | Clones and sets up an Evennia MUD under `C:/EvenniaWorlds`. |
| `Launcher.java` | Small Swing launcher window. |

### Applications

| Project | What it does |
| --- | --- |
| [`AutoTranslate/`](AutoTranslate/README.md) | Batch-translates a folder of images with a local vision model (Ollama or LM Studio), writing a `.txt` beside each image. Tkinter GUI plus a headless `--cli` mode; installers for Windows and Linux. |
| [`PPM2PNG/`](PPM2PNG/README.md) | Batch-converts PPM images to PNG from a text file list. |

### Notes

- [MacRestoreList.md](MacRestoreList.md) — macOS apps to reinstall after a wipe.

---

## macOS Keyboard Shortcuts

### Essentials

| Shortcut | Action |
| --- | --- |
| `⌘ + Space` | Spotlight search |
| `⌘ + Tab` | Switch applications |
| `⌘ + \`` | Switch windows within the current app |
| `⌘ + Q` | Quit application |
| `⌘ + W` | Close window or tab |
| `⌘ + M` | Minimize window |
| `⌘ + H` | Hide the current app |
| `⌘ + Option + H` | Hide all other apps |
| `⌘ + ,` | Open the current app's Preferences/Settings |
| `⌘ + Option + Esc` | Force Quit dialog |

### Editing

| Shortcut | Action |
| --- | --- |
| `⌘ + C` / `⌘ + V` / `⌘ + X` | Copy / Paste / Cut |
| `⌘ + Option + Shift + V` | Paste and match style |
| `⌘ + Z` / `⌘ + Shift + Z` | Undo / Redo |
| `⌘ + A` | Select all |
| `⌘ + F` | Find |
| `⌘ + G` / `⌘ + Shift + G` | Find next / previous |
| `⌘ + ←` / `⌘ + →` | Start / end of line |
| `⌘ + ↑` / `⌘ + ↓` | Start / end of document |
| `Option + ←` / `Option + →` | Move one word left / right |
| `⌘ + Delete` | Delete to start of line |
| `Fn + Delete` | Forward delete |

### Finder

| Shortcut | Action |
| --- | --- |
| `⌘ + Shift + N` | New folder |
| `⌘ + Shift + .` | Toggle hidden files |
| `⌘ + Shift + G` | Go to folder… |
| `⌘ + Delete` | Move to Trash |
| `⌘ + Shift + Delete` | Empty Trash |
| `Space` | Quick Look preview |
| `⌘ + I` | Get Info |
| `⌘ + D` | Duplicate |
| `⌘ + Option + V` | Move here (after `⌘ + C`) |

### Screenshots and system

| Shortcut | Action |
| --- | --- |
| `⌘ + Shift + 3` | Screenshot entire screen |
| `⌘ + Shift + 4` | Screenshot a selection |
| `⌘ + Shift + 4` then `Space` | Screenshot a window |
| `⌘ + Shift + 5` | Screenshot / screen recording toolbar |
| `Ctrl + ←` / `Ctrl + →` | Switch desktops (Spaces) |
| `Ctrl + ↑` | Mission Control |
| `⌘ + Ctrl + Q` | Lock screen |
| `⌘ + Ctrl + F` | Toggle full screen |

---

## Linux Keyboard Shortcuts

Desktop shortcuts vary by environment; the ones below are the GNOME/KDE defaults. Terminal and readline shortcuts are consistent nearly everywhere.

### Desktop

| Shortcut | Action |
| --- | --- |
| `Super` | Activities / application launcher |
| `Alt + Tab` | Switch applications |
| `Alt + \`` | Switch windows within the current app |
| `Alt + F2` | Run command |
| `Alt + F4` | Close window |
| `Super + ←` / `Super + →` | Snap window to left / right half |
| `Super + ↑` / `Super + ↓` | Maximize / restore window |
| `Super + L` | Lock screen |
| `Super + D` | Show desktop |
| `Ctrl + Alt + T` | Open terminal |
| `Ctrl + Alt + F3`…`F6` | Switch to a virtual console (`Ctrl + Alt + F2` returns to the desktop) |
| `PrtSc` | Screenshot whole screen |
| `Shift + PrtSc` | Screenshot a selection |

### Terminal

| Shortcut | Action |
| --- | --- |
| `Ctrl + C` | Interrupt the running command |
| `Ctrl + D` | End of input / logout |
| `Ctrl + Z` | Suspend to background (`fg` to resume) |
| `Ctrl + L` | Clear the screen |
| `Ctrl + S` / `Ctrl + Q` | Pause / resume terminal output |
| `Ctrl + Shift + C` / `Ctrl + Shift + V` | Copy / paste in most terminal emulators |
| `Shift + PgUp` / `Shift + PgDn` | Scroll the terminal buffer |

### Bash / readline line editing

| Shortcut | Action |
| --- | --- |
| `Ctrl + A` / `Ctrl + E` | Jump to start / end of line |
| `Alt + B` / `Alt + F` | Move back / forward one word |
| `Ctrl + U` | Cut from cursor to start of line |
| `Ctrl + K` | Cut from cursor to end of line |
| `Ctrl + W` | Cut the word before the cursor |
| `Ctrl + Y` | Paste the last cut text |
| `Ctrl + R` | Reverse search through history |
| `Ctrl + G` | Cancel a reverse search |
| `!!` | Repeat the last command (`sudo !!` reruns it as root) |

---

## MongoDB Basic Commands

### Service control

```bash
sudo systemctl start mongodb      # start (Arch: mongodb, Debian/Ubuntu: mongod)
sudo systemctl stop mongodb
sudo systemctl status mongodb
sudo systemctl enable mongodb     # start on boot
```

### Shell and databases

```bash
mongosh                           # connect to localhost:27017
mongosh "mongodb://host:27017/mydb"
```

```javascript
show dbs                          // list databases
use mydb                          // switch to (and create on first write) a database
db                                // show the current database
show collections                  // list collections
db.dropDatabase()                 // delete the current database
```

### Collections and documents

```javascript
db.createCollection("users")
db.users.drop()

// Create
db.users.insertOne({ name: "Dave", role: "admin", age: 42 })
db.users.insertMany([{ name: "Ada" }, { name: "Grace" }])

// Read
db.users.find()                              // all documents
db.users.find().pretty()                     // formatted
db.users.find({ role: "admin" })             // filtered
db.users.findOne({ name: "Dave" })
db.users.find({ age: { $gt: 30 } })          // $gt $gte $lt $lte $ne $in $nin
db.users.find({ role: "admin", age: { $lt: 50 } })          // AND
db.users.find({ $or: [{ role: "admin" }, { age: { $lt: 30 } }] })
db.users.find({}, { name: 1, _id: 0 })       // projection
db.users.find().sort({ age: -1 }).limit(10).skip(20)
db.users.countDocuments({ role: "admin" })
db.users.distinct("role")

// Update
db.users.updateOne({ name: "Dave" }, { $set: { role: "owner" } })
db.users.updateMany({ role: "admin" }, { $inc: { age: 1 } })
db.users.updateOne({ name: "New" }, { $set: { age: 1 } }, { upsert: true })
db.users.replaceOne({ name: "Dave" }, { name: "Dave", role: "owner" })

// Delete
db.users.deleteOne({ name: "Ada" })
db.users.deleteMany({ role: "admin" })
```

### Indexes and inspection

```javascript
db.users.createIndex({ name: 1 })              // 1 = ascending, -1 = descending
db.users.createIndex({ email: 1 }, { unique: true })
db.users.getIndexes()
db.users.dropIndex("name_1")
db.users.find({ name: "Dave" }).explain("executionStats")
db.stats()
db.users.stats()
```

### Aggregation

```javascript
db.users.aggregate([
  { $match: { age: { $gte: 18 } } },
  { $group: { _id: "$role", count: { $sum: 1 }, avgAge: { $avg: "$age" } } },
  { $sort: { count: -1 } }
])
```

### Backup and restore

```bash
mongodump --db mydb --out ~/backups/                    # dump one database
mongodump --out ~/backups/                              # dump everything
mongorestore --db mydb ~/backups/mydb/                  # restore one database
mongorestore --drop ~/backups/                          # restore all, replacing existing
mongoexport --db mydb --collection users --out users.json
mongoimport --db mydb --collection users --file users.json
```

### Users

```javascript
use admin
db.createUser({
  user: "admin",
  pwd: passwordPrompt(),
  roles: [{ role: "userAdminAnyDatabase", db: "admin" }]
})
show users
db.dropUser("admin")
```

---

## Git Commit Format

```
[type]([optional scope]): [description]
```

**Type**

| Type | Meaning |
| --- | --- |
| `feat` | New feature for the user |
| `fix` | Bug fix for the user; include the bug number in the scope if it was customer-reported |
| `docs` | Documentation changes |
| `style` | Formatting, missing semicolons, etc; no production code change |
| `refactor` | Refactoring production code, e.g. renaming a variable |
| `test` | Adding or refactoring tests; no production code change |
| `chore` | Build tasks, tooling, etc; no production code change |
| `other` | Anything else |

**Scope**

`BREAKING CHANGE` — warns users of changes that break previous behavior.

---

## Robocopy Reference

```bat
robocopy "[Source Directory]" "[Destination Directory]" /e /w:5 /r:2 /COPY:DATSOU /DCOPY:DAT /MT
```

| Flag | Meaning |
| --- | --- |
| `/e` | Copy all folders, including empty ones |
| `/r:n` | Retry count on failed copies; `/r:0` means no retries |
| `/w:n` | Wait seconds between retries; `/w:0` means no wait |
| `/COPYALL` or `/COPY:DATSOU` | Copy Data, Attributes, Timestamps, Security, Owner, and Auditing info |
| `/DCOPY:DAT` | Copy Data, Attributes, and Timestamps for directories |
| `/MT:n` | Multithreaded transfer with `n` threads (defaults to 8) |
| `/MIR` | Mirror source to destination — **deletes any files in the destination that are not in the source** |
| `/MOVE` | Move instead of copy — **deletes all files from the source after copying** |
| `/LFSM:100M` | Low Free Space Mode with a 100 MB floor (`10M` = 10 MB, `1G` = 1 GB) |
| `/RH:1700-0900` | Only run between 5 PM and 9 AM; pauses during business hours |
| `/LOG+:C:\robocopy.log` | Append all output to a log file (use a path under your user folder if not running as admin) |
| `/TEE` | Show console output as well as writing to the log |

---

## License

[MIT](LICENSE)
