# Sidekick

**A local AI agent that runs on your Android device.** One Python file, zero third-party dependencies, a browser as its UI.

It doesn't just chat — it **actually operates your device**: reading and writing files, running commands, editing code, browsing the web, controlling your phone, building spreadsheets, editing images.

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

An AI agent that lives on your Android phone or tablet, used through a browser interface.

It is built around four ideas:

- **Foolproof install** — two commands, no third step
- **Evolves itself every day** — no maintenance, no remote updates
- **Fixes its own bugs** — with backup, syntax check and auto-rollback
- **Resumes after interruption** — long tasks survive being killed

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
