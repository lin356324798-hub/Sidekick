# Sidekick

**Your Android device, as of today, is home to an AI agent.** One Python file, zero third-party dependencies, a browser as its UI.

It doesn't keep you company — it **gets its hands dirty and does the work**: files, commands, code, the web, your phone, spreadsheets, images.

No server, no account, no subscription. It runs on your own device with no middleman — and if you want to go fully offline, just point it at a local model on your LAN.

> `Sidekick` is just the factory default name. Rename it to anything you like on the "Identity" page.

---

## 🌐 Choose your language / 选择语言

| Language | 语言 | Link |
|---|---|---|
| English | 英语 | **[README.en.md](README.en.md)** |
| 简体中文 | Chinese (Simplified) | **[README.zh-CN.md](README.zh-CN.md)** |
| 繁體中文 | Chinese (Traditional) | **[README.zh-TW.md](README.zh-TW.md)** |
| 日本語 | Japanese | **[README.ja.md](README.ja.md)** |
| 한국어 | Korean | **[README.ko.md](README.ko.md)** |
| Español | Spanish | **[README.es.md](README.es.md)** |
| Deutsch | German | **[README.de.md](README.de.md)** |
| Français | French | **[README.fr.md](README.fr.md)** |
| Русский | Russian | **[README.ru.md](README.ru.md)** |
| العربية | Arabic | **[README.ar.md](README.ar.md)** |

---

## What is this

An AI agent that lives on your Android phone or tablet. You talk to it through a browser; it works on the device itself.

Four things make it different from everything else you've installed:

- **Two commands and you're in** — no hidden step 2.5
- **It quietly gets stronger every day** — you do nothing, nobody pushes you updates
- **It gets back up when it crashes** — backup, syntax check, auto-rollback
- **Kill it mid-task, it picks up where it left off** — long jobs survive being interrupted

And because it grows from *your* usage, **every device ends up with a different agent — yours will look like you.**

Read the full description in your language above. The English version is [here](README.en.md).

---

## Quick start

```bash
pkg update && pkg install -y python curl
bash install.sh
```

Then open **http://127.0.0.1:8765/** in your browser.

---

## Verify the self-evolution yourself

These files are written by the agent at runtime — they're not pre-packaged marketing material:

```bash
cat ~/.termux-agent/evolve.json     # what it improved for itself, and why
cat ~/.termux-agent/selfheal.json   # which crashes it found and fixed
tail ~/.termux-agent/evolve.log     # live evolution & self-heal log
```
