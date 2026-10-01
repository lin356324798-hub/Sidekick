# Sidekick

**A local AI agent that runs on your Android device.** One Python file, zero third-party dependencies, a browser as its UI.

It doesn't just chat — it **actually operates your device**: reading and writing files, running commands, editing code, browsing the web, controlling your phone, building spreadsheets, editing images.

> `Sidekick` is just the factory default name. Rename it to anything you like on the "Identity" page.

**Languages:** **English** | [简体中文](README.zh-CN.md) | [繁體中文](README.zh-TW.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | [Español](README.es.md) | [Deutsch](README.de.md) | [Français](README.fr.md) | [Русский](README.ru.md) | [العربية](README.ar.md)

---

## What makes it different

### 1. Foolproof install — two commands, no third step

No Python knowledge needed. No environment setup. No database. Install Termux, paste two lines, open the browser, and you're done.

```bash
pkg install -y python curl
bash install.sh
```

No Docker, no Node, no dependency conflicts. It uses **only the Python standard library**, so upstream updates can't break it — if it runs today, it'll still run three years from now.

### 2. It evolves itself every day — no maintenance, no remote updates

This is the most unusual part: **it gets a little better every day, and nobody needs to push you an update.**

Each day it reviews its own performance, finds something worth improving, makes the change, and writes it to a log. Here's real work it did on its own:

| Date | What it changed by itself |
|---|---|
| 09-28 | Added "archive by type" hints to `list_dir` — when listing files, it now tells you images belong in `Pictures/`, documents in `Documents/` |
| 09-28 | When a tool argument is misspelled, it silently drops unsupported arguments and suggests the correct nearby name (e.g. `cwd` → `command`) |
| 09-29 | `read_file` no longer spews garbage on binary files; it reports the file type and size instead |
| 10-01 | Made the console card fixed-height — because testing revealed the Save button shifted by 447px when switching tabs, causing mis-taps |

> These aren't marketing lines. They're excerpts from its own evolution log. Once it's running on your machine, it grows improvements that are **relevant to you**.

**What this means:** your agent and someone else's agent will diverge after a month of use. If you often have it organize photo albums, it evolves stronger image handling. If you use it for reports, it evolves a smoother document workflow. **Every device raises a one-of-a-kind agent.**

### 3. It fixes its own bugs — and you can watch it diagnose

When the program crashes, it doesn't just leave a stack trace. It has a built-in **self-healing** mechanism: detect the crash → read its own logs to locate it → fix the code → verify → log the outcome.

A real record (from `selfheal.json`):

```
10-01 14:22  Crash detected   watchdog log · fingerprint f99407371b41
10-01 14:23  Heal complete    Confirmed — line 2809 is unrelated log-reading
                              code, and tool_wx_auto is defined.
                              No further changes needed.
```

It will even **overturn its own wrong conclusion**: in another self-heal run it concluded "this is not a bug to fix — the file already healed itself," and laid out the evidence in a table. It doesn't change things blindly.

Before modifying itself it **backs up first**, **runs a syntax check** after, and **auto-rolls back** if it won't start. So "it fixes itself" doesn't mean "it bricks itself."

### 4. It picks up where it left off — long tasks survive interruption

A half-hour task, and Android kills Termux? After restart it **automatically resumes**, without you having to explain everything again.

```bash
$ tail evolve.log
[self-heal] Explicit resume marker found; continuing automatically
[self-heal] Auto-resuming 20260928-034008-1058 (boot), attempt 2
```

Phone reboot, network switch, Termux cleared by the system — it remembers where it was.

---

:::tip In one sentence

**Effortless to install, grows as you use it, fixes itself when broken, and resumes when interrupted.**

:::

### Verify it yourself — don't take my word for it

After installing, look at its own evolution records:

```bash
cat ~/.termux-agent/evolve.json     # what it improved for itself, and why
cat ~/.termux-agent/selfheal.json   # which crashes it found and fixed
tail ~/.termux-agent/evolve.log     # live evolution & self-heal log
```

These files are **written by the agent at runtime**, not pre-packaged marketing material. Run it for a while and they'll fill up with records that belong to you.

---

## Contents

- [What makes it different](#what-makes-it-different)
  - [Foolproof install](#1-foolproof-install--two-commands-no-third-step)
  - [Daily self-evolution](#2-it-evolves-itself-every-day--no-maintenance-no-remote-updates)
  - [Self-repair](#3-it-fixes-its-own-bugs--and-you-can-watch-it-diagnose)
  - [Resume after interruption](#4-it-picks-up-where-it-left-off--long-tasks-survive-interruption)
- [What it is](#what-it-is)
- [Installation](#installation)
- [Configuring the API key](#configuring-the-api-key)
- [Daily usage](#daily-usage)
- [Capabilities overview](#capabilities-overview)
- [Tool reference (20)](#tool-reference-20)
- [Skill library (36)](#skill-library-36)
- [Directory layout](#directory-layout)
- [Security notes](#security-notes)
- [FAQ](#faq)

---

## What it is

An AI assistant that lives on your phone or tablet, used through a browser interface.

**Design trade-offs**

| Principle | Explanation |
|---|---|
| **Zero third-party dependencies** | Uses only the Python standard library; `pkg install python` is all you need. No native modules requiring compilation, so upstream updates can never break it |
| **Local storage** | Conversations, memory, and keys all stay in `~/.termux-agent/` on your device. Nothing is uploaded to the cloud |
| **Can modify itself** | It can edit its own source code to add features, and rolls back automatically if the change breaks it |
| **Ships with a watchdog** | If the service dies it gets restarted automatically, without systemd (which Android doesn't have) |
| **No remote updates needed** | No server, no update push. Its evolution is self-driven; new capabilities don't depend on the author shipping a release |

---

## Installation

Prerequisites: [Termux](https://f-droid.org/packages/com.termux/) installed on your Android device (**the F-Droid build is recommended** — add-ons must share the main app's signature).

```bash
# 1. Install Python
pkg update && pkg install -y python curl

# 2. From this directory, run the one-shot installer
bash install.sh
```

The installer will, in order: check the environment → back up any existing installation → copy the program and skills → generate a default config (**without overwriting an existing API key**) → start the service and self-test.

---

## Configuring the API key

You need an API key on first run. Two ways:

**Option 1 (recommended)**: open `http://127.0.0.1:8765/` and fill it in under "Settings" in the top-right corner.

**Option 2**: from the command line
```bash
python3 ~/.termux-agent/agent.py config
```

It defaults to DeepSeek (`https://api.deepseek.com`), but you can point it at any OpenAI-compatible endpoint — including a local model running on your LAN.

---

## Daily usage

```bash
bash ~/.termux-agent/start.sh              # restore the service after restarting Termux
python3 ~/.termux-agent/agent.py doctor    # environment check + connectivity test
python3 ~/.termux-agent/agent.py restart   # restart the service
python3 ~/.termux-agent/agent.py selfcheck # self-inspection (source/watchdog/service/backups)
```

Then open **http://127.0.0.1:8765/** in your browser.

### Direct CLI use (no UI)

```bash
python3 ~/.termux-agent/agent.py "check disk usage"   # run once and exit
python3 ~/.termux-agent/agent.py -c                   # continue the last conversation
python3 ~/.termux-agent/agent.py chat                 # chat in the terminal (basic)
```

### Autostart on boot (optional)

Install [Termux:Boot](https://f-droid.org/packages/com.termux.boot/), open that app once manually, then:

```bash
python3 ~/.termux-agent/agent.py autostart
```

---

## Capabilities overview

| Category | Capabilities |
|---|---|
| **System operations** | Run shell commands, read system settings with shell privileges, install APKs, manage processes |
| **File handling** | Read/write/edit files, batch patches, browse directories, recursive regex search, paged reading of large files |
| **Networking** | Fetch web pages as text, keyless web search, streaming download of large files |
| **Code hosting** | Full GitHub workflow (create repo / clone / pull / commit / push / search code) |
| **Device control** | Remotely operate a Huawei phone (HDC protocol), act on this device as adb shell |
| **Image processing** | Remove text or watermarks, restore old photos, enhance quality, retouch portraits, cut out backgrounds |
| **Document output** | Generate Word / Excel / PPT, convert HTML to DOCX, contract templates, typesetting polish |
| **Task management** | To-do lists, parallel sub-agent reasoning, asking you for decisions |
| **Self-evolution** | Modify its own source to add features, with automatic backup + syntax check + rollback on failure |
| **Domain skills** | 36 domain skill documents (writing, design, legal, finance, debugging, …) |

---

## Tool reference (20)

### System & files

| Tool | Purpose | Highlights |
|---|---|---|
| `bash` | Run shell commands | The workhorse: install packages, manage files, run programs |
| `read_file` | Read text files (with line numbers) | Paged reading for large files; a negative `offset` reads logs from the end |
| `write_file` | Write a whole file | Creates parent directories automatically |
| `edit_file` | Exact string replacement | Requires a unique match; when several matches exist it reports their line numbers |
| `apply_patch` | Multi-hunk / multi-file patch | **Atomic**: if any hunk doesn't apply, nothing is written — no half-applied edits |
| `list_dir` | List a directory | Includes type, size, modification time |
| `grep` | Recursive regex search | Returns `file:line: content` |

### Networking

| Tool | Purpose | Notes |
|---|---|---|
| `fetch_url` | Fetch a page/API as plain text | Text reading only |
| `web_search` | Web search | **No API key required** |
| `download` | Streaming download of large files | No truncation, with timeout and automatic retry. Good for APKs and installers |

### Devices & external systems

| Tool | Purpose | Notes |
|---|---|---|
| `sysshell` | Execute as shell (adb) | Higher privileges than a normal app: read system settings, `dumpsys`, `getprop`, `pm/am`. **Not root** |
| `hdcmate` | Remotely control a Huawei phone | Uses the HDC protocol (not adb). `exec` runs a command / `target` remembers the address / `test` checks the connection. Supports full UI automation |
| `github` | Operate GitHub repositories | Nine actions: `list/repo/read/tree/clone/pull/push/create/search`. Requires a token |
| `wps` | Generate Word/Excel/PPT | Implemented via a local MCP service, **no account needed**. Files land in `~/storage/shared/WPS_AI/` |
| `imgedit` | AI image processing | Five operations: `erase` text/watermark / `restore` old photos / `enhance` quality / `beauty` portrait / `matting` cutout |

### Collaboration & self-evolution

| Tool | Purpose | Notes |
|---|---|---|
| `todo_write` | Maintain a task list | Splits tasks of 3+ steps into 2–6 items, shown live above your input box; after a reconnect it continues from there |
| `subagent` | Independent sub-agent thinking | Hands an isolated sub-problem to a separate agent (no access to local files). Good for parallelizing hard problems |
| `ask_user` | Ask you to decide | Opens a choice panel. Used only when your call is required: spending money, deleting data, choosing between approaches |
| `selfupdate` | **Modify itself** | Automatic backup + syntax check, immediate rollback on failure; the service restarts automatically on success |

### Archived

| Tool | Notes |
|---|---|
| `wx_auto` | WeChat auto-reply companion (screen reading + auto reply; requires authorization and the other party's knowledge). Three safety gates: unclear readings are only logged, it gives up if the send button can't be found, and it caps at 20 messages per hour |

---

## Skill library (36)

Skills are **domain methodology documents** handed to the AI. Before a relevant task, it reads the matching skill and follows the conventions and hard-won lessons inside.

### Writing (11)

| Skill | Purpose |
|---|---|
| `general-writer` | **L1 general-purpose writing fallback.** Official documents, weekly reports, proposals, emails, copywriting, essays, new media; 7-dimension quality scoring + a 10-genre adaptation matrix |
| `academic-paper-expert` | Academic papers: structure design, literature review, abstracts, APA/GB-T7714 citation rules, academic polishing |
| `tech-blog-expert` | Technical blogging: tutorials, architecture breakdowns, source-code analysis, open-source docs, README |
| `business-copy-expert` | Commercial copy: brand copy, marketing emails, product descriptions, slogans, ad compliance (AIDA model) |
| `work-report-expert` | Workplace reporting: year-end summaries, performance reviews, promotion speeches, weekly/monthly reports (Pyramid Principle + STAR) |
| `science-writing-expert` | Popular science: explanations, tech reviews, long-form reporting (Feynman technique) |
| `poetry-prose-expert` | Poetry and prose: modern poetry, classical Chinese verse, essays, literary criticism |
| `stock-research-report-expert` | **L2 securities research.** Industry deep-dives, single-stock research, event commentary, business plans; four length tiers |
| `legal-contract-expert` | **L2 legal contracts.** Drafting and review: completeness of required clauses, symmetry of rights and obligations, high-risk prevention |
| `humanizer-zh` | Remove the "AI flavor" (Chinese): detects and fixes issues based on Wikipedia's guide to AI writing signs |
| `humanizer` | Remove the "AI flavor" (English) |

### Document output (5)

| Skill | Purpose |
|---|---|
| `doc-typeset` | **Typesetting and polish.** Consumes design tokens + content and outputs polished HTML. Ships with 7 vertical templates (contracts / academic papers / official documents / business reports / meeting minutes / research reports / annual reports) |
| `html-to-docx` | High-fidelity HTML → Word. Handles CSS variable preprocessing, precise mapping for 10+ element types, 14 CSS properties |
| `format-extract` | Convert .docx to semantic HTML + extract embedded images (preserving heading hierarchy, table styles, indentation, colors) |
| `generate-fillable-contract-html` | Generate fillable Chinese contract, quotation, and power-of-attorney HTML forms |
| `underline-toolkit` | Fill-in-the-blank documents: `create` generates a blank template / `fill` writes data into an existing template (contracts, application forms, paper covers) |
| `html-review` | **HTML quality gate.** Runs 5-dimension checks on typeset output (token compliance, structural integrity, layout sanity, genre fit, moderation in decoration) and sends it back for targeted fixes if it fails |

### Design (8)

| Skill | Purpose |
|---|---|
| `design-router` | Design task dispatcher: decides which design family applies, then dispatches |
| `design-token` | Emits standardized design tokens per document type, driving all style decisions in doc-typeset |
| `design-variables` | Bind/unbind design variables (design tokens) to node properties |
| `ardot-design-to-code` | Turn design mockups into front-end code, or extract a design system / style guide from a website |
| `ardot-ui-design` | UI design: web pages, dashboards, landing pages, mobile interfaces |
| `ardot-poster` | Visual posters: posters, flyers, billboards, banners, campaign key visuals |
| `ardot-slides` | Presentation design (not .pptx files — design mockups) |
| `component-instance` | Component instance management: create/update instances, set component properties, switch variants |
| `shared-styles` | Bind/unbind shared styles (text styles, fills, strokes, effects) |

### Debugging & operations (5)

| Skill | Purpose |
|---|---|
| `termux-traps` | **Android Termux environment pitfalls**: the traps on this kind of device and the correct way to handle them |
| `log-debug` | Log troubleshooting guide: which file to check first when something goes wrong |
| `selfupdate` | The proper self-modification workflow: the correct channel and ground rules for editing `agent.py` |
| `ui-debug` | Web UI debugging: change the UI and verify it on the spot |
| `pc-debug` | Execute commands and read/write files on that Windows PC via the "PC debug bridge" |

### Device connectivity (2)

| Skill | Purpose |
|---|---|
| `hdcmate` | HDC phone debugging: protocol notes, common HarmonyOS commands, UI automation, pitfalls |
| `chrome-cdp` | Chrome debugging and scraping: read web data over the DevTools protocol without touching the screen |

### Skill management (4)

| Skill | Purpose |
|---|---|
| `find-skills` | Helps you discover and install skills |
| `skill-creator` | A guide to creating new skills |
| `marketplace-skill-installer` | Search and install from a skill marketplace |
| `underline-toolkit` | See "Document output" above |

---

## Directory layout

```
~/.termux-agent/
├── agent.py          Main program (single file, pure standard library)
├── cdp.py            CDP client (browser debugging/scraping; used by ui-debug / chrome-cdp)
├── chrome_read.py    Chrome web data reader (used by chrome-cdp)
├── hdc.py            HDC protocol implementation (used by hdcmate; 447 lines)
├── wps.py            WPS document generation
├── wps_mcp_server.py Local WPS MCP service
├── mcp_call.py       MCP call helper
├── config.json       Configuration (contains the API key, mode 600)
├── skills/           Skill documents (36)
├── sessions/         Conversation records
├── memory.md         Long-term memory
├── evolve.json       Evolution log (what it changed each day, and why)
├── evolve.log        Evolution runtime log
├── selfheal.json     Self-heal history (crash detected → fix complete)
├── selfheal.py       Self-heal and resume module
├── uploads/          Uploaded images
├── outputs/          Generated artifacts
├── versions/         Source version backups (used by self-update)
├── logs/             Logs
├── tmp/              Temporary files
├── supervisor.sh     Watchdog (auto-generated)
└── start.sh          Startup script
```

---

## Security notes

- The UI **listens only on `127.0.0.1`** and is not exposed to the LAN (the UI has command-execution privileges)
- `config.json` and token files are mode `600`
- Irreversible operations — formatting, writing directly to block devices, deleting the root directory — are intercepted automatically
- No root privileges, and it cannot read other apps' private data
- Environment self-check: `python3 ~/.termux-agent/agent.py doctor`

---

## FAQ

**The service won't start?**
```bash
python3 ~/.termux-agent/agent.py selfcheck   # see what it says about itself
tail -30 ~/.termux-agent/supervisor.log      # check the watchdog log
```

**Does it really evolve itself? What has it changed?**
Look at `~/.termux-agent/evolve.json` — every entry records what changed and why. Quick look from the command line:
```bash
python3 -c "import json;print('\n'.join(f'{e[\"time\"]}  {e[\"summary\"].splitlines()[0]}' for e in json.load(open('$HOME/.termux-agent/evolve.json'))['log']))"
```

**How is self-evolution different from a remote update?**
It downloads no update packages and no server pushes versions to it. It **reads its own code and logs and edits in place**. That's why capabilities differ between devices — it depends on how you use it.

**What if it breaks itself while editing its own code?**
The `selfupdate` channel **backs up first → runs a syntax check after the edit → restores immediately on failure**. Even if the new code starts but misbehaves, the watchdog rolls back to the previous version. The self-heal process itself leaves a record (`selfheal.json`).

**A long task was killed halfway by the system?**
After a restart it resumes automatically; no need to restate anything. You'll see "auto-resuming" in the log.

**I changed the code but nothing happened?**
New code only takes effect after the browser loads it once — just refresh the page.

**Where can I see the pitfalls it has hit?**
`skills/termux-traps.md` documents the environment traps on this kind of device.

**How do I add new capabilities?**
Use the `skill-creator` skill to create a new `.md` in `skills/`, or let the AI add a tool for itself with `selfupdate`.

---

## License

A personal project. Take it and use it however you like.
