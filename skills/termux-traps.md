# Termux 环境坑 —— 这台平板上的注意事项

> 建立日期：2026-09-29
> 设备：华为 MatePad Pro MRO-W00（鸿蒙 4.2 / 底层 Android 12）

---

## 一、权限与系统

| 事项 | 说明 |
|---|---|
| **没有 root** | 不要试 `sudo` / `su`，必失败。需要系统权限时用 `sysshell`（adb shell 身份，uid 2000） |
| **装软件用 `pkg install`** | 不是 apt/yum。Termux 的包管理器 |
| **没有 systemd** | 后台常驻用 `nohup` 或 `termux-wake-lock`，**不要用 systemctl** |
| **访问相册/下载** | 先跑 `termux-setup-storage`，之后路径在 `~/storage/shared/` |
| **读系统设置 / dumpsys / pm / am** | 普通 bash 被拒时改用 `sysshell` 工具 |

---

## 二、文件系统

- **`/tmp` 不可写** —— 临时文件写在 `$HOME` 或 `$PREFIX/tmp`
- **家目录** `$HOME`（`/data/data/com.termux/files/home`）**不会被安卓系统清理**，可以放心放东西
- **共享存储**在 `~/storage/shared/`（= 安卓的"内部存储"），手机文件管理器能看到
- **U 盘**挂载点形如 `/storage/XXXX-XXXX`；Android 12 下**普通应用可能看不到**，脚本要准备好"找不到"的兜底提示

---

## 三、工具链

| 工具 | 注意 |
|---|---|
| `python3` | 3.14.6，可用 |
| `qjs`（QuickJS-ng） | **没有 `-c` 选项**；**没有 `std` 对象**（`std.loadFile` 会报 `std is not defined`）。验 JS 语法就直接 `qjs file.js`，报 `ReferenceError` = 语法没问题（缺浏览器环境而已） |
| `cdp.py` | 本机自写的 CDP 客户端，用法见 `ui-debug.md` |
| `hdcmate` | 远程操控华为手机（HDC 协议，**不是 adb**），见 `hdcmate.md` |

---

## 四、浏览器（华为浏览器）

- **拒绝 `input tap` 合成事件**，HOME 键注入也不稳
- **无法用 uiautomator 操作网页** → 前端问题只能：让用户操作 + dump 屏幕看实际渲染
- **用 CDP 直接读写页面**是唯一可靠的自动化通道

---

## 五、资源

- **内存 11.5GB**（可用约 4.26GB）—— 避免跑高内存任务
- **存储 226GB 总 / 约 176GB 可用**
- 大输出要分页处理，别一次性打印几万行

---

## 六、教训

- **不要在对话进行中 `location.reload()`** —— 会把承载对话的 WebSocket 掐断，用户看到"话说一半消失"（详见 `ui-debug.md`）
- **行号会漂移**：多个进程会改同一批文件（agent.py 被自动进化/自愈改），改代码前重新 grep
