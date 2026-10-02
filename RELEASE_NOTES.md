# Release Notes

## v1.8.5 — 2026-10-02

Diff since **v1.7.4**. This release is mostly about **surviving in the real world**:
not losing your conversation, not corrupting your files, not fighting itself over a port,
and not tripping over the same bug twice.

---

### 📝 Fixed: conversation turns silently dropped

A turn could be discarded without a trace when `conn.sid` and `agent.session_id`
disagreed — the session had already moved on by the time the turn finished writing.

Turns now carry a `started_sid` field, and the finalize check compares against the
session id the turn *began* with rather than whatever the session happens to be now.
A log line is written either way, so a dropped turn is never invisible again. (#3 of 3)

### 🛡️ Hardened: writes can no longer corrupt your data

`write_text` truncates before it writes. If the process got killed mid-write — and on
Android, it will — you were left with a half-written JSON file, which showed up as
"my session history vanished."

All critical writes (sessions, config) now go through `_write_text_atomic`:
**temp file → fsync → `os.replace`**. Either the old file or the new file exists.
Never a broken one in between.

### 🔒 Fixed: two services fighting over the same port

A single-instance `flock` now guards the server process. Previously two `web --serve`
processes could race for port 8765, and the watchdogs would then kill each other in a
loop — the service would thrash instead of run.

The boot script template was fixed too: it now starts through the watchdog (starting the
service bare meant `pgrep` couldn't match it, which is what allowed the second copy to
sneak in).

### ⚙️ Fixed: watchdog process management

- `supervisor.pid` was being deleted unconditionally — on a failed kill this let two
  watchdogs exist at once, each trying to kill the other's service. Now only removed
  when the kill actually succeeded.
- Config loading: `DEEPSEEK_API_KEY` is a common environment variable name, so it no
  longer overrides your configured `api_key` unless your `base_url` actually points at
  DeepSeek. It used to hijack requests aimed at other providers.

### 🌍 New: platform abstraction layer

Introduced `platform_layer.py` with real call sites wired into `_kill_proc`,
`_exec_stream`, and `tool_bash` — groundwork for running on Windows / macOS / Linux.
Alongside it, `supervisor_py.py` gives non-POSIX systems a Python watchdog, so crash
recovery is no longer bash-only.

Behaviour on Android/Termux is unchanged.

### 🈯 Bilingual UI and backend

- The UI language dropdown went from 10 options down to **Simplified Chinese / English** —
  fewer half-finished translations, better ones that remain.
- Added `build_system_prompt_en()`: the entire system prompt is now written natively in
  English rather than machine-translated.
- Added `TOOL_DESC_EN` + `localize_tools()` — all 20 tool descriptions and parameter docs
  have English variants. **Tool names, parameter names, and schemas are never altered**,
  so switching language can't break a tool call.
- Self-check prompt, sub-agent system prompt, `ask_user` responses, and auto-continue
  markers all follow the selected language.
- 217 UI strings per language, verified symmetric.

Language switches take effect on the next turn.

### 🔎 New: startup self-audit for untracked changes

If `VERSION` changes but there's no matching entry in the changelog, the agent now writes
one and says why. This catches edits made directly to `agent.py` that bypassed the
`selfupdate` path — those skip the auto-backup, skip syntax checking, and leave no trace
of what was changed.

### 🐛 Fixed: memory and changelog pages squeezed into a sliver

Reported as "the content is crushed into a thin strip in the middle." Three bugs stacked:

1. CSS selector `.pane.grow.on > *:last-child > .ctl` never matched — a `button.q`
   follows the `.row`. Now uses `.pane.grow.on > .row > .ctl`.
2. The `data-t` attribute lives on an inner `<span>`, but the JS read it off the outer
   container and got an empty string, so the `grow` class was never applied. Now falls
   back to the first descendant carrying `data-t`.
3. Once `.row` became a column, `align-items: center` flipped its axis to horizontal —
   and "center + shrink to content width" crushed the textarea to **183px** wide.
   Changed to `align-items: stretch`, with `.lb` on its own line.

Measured: memory-page textarea went from 183×341 to **648×409**; the changelog list to
648×439; zero pixel shift in the bottom buttons; the other six pages untouched.

### 🏗️ Codebase housekeeping

Startup now scans `agent.py` for duplicate top-level definitions and warns. Python takes
the *last* definition, so an accidental redefinition silently cancels your change.

---

### Notes

- **No breaking changes.** Configuration files and session data carry forward as-is.
- Settings, sessions, and memory from v1.7.4 work without migration.
- Dependencies: still **zero**. Standard library only.

