# pc-debug

> 通过「PC 调试桥」在**那台 Windows 电脑**上执行命令、读写文件、看系统状态。桥跑在电脑上，平板通过 adb 反向映射访问，地址就是 `http://127.0.0.1:18800`。

## 什么时候用它

用户说「看看电脑怎么了」「电脑上那个文件」「帮我在这台电脑上跑个命令」「电脑卡不卡」这类**针对电脑本身**的活时用它。
（在**平板**上干活用 `bash`；在**电脑**上干活用这个。）

## 权限

**完全放开**：命令无黑名单、无确认、无沙箱。可以直接读写电脑上的任意文件。
这是用户明确要求的，所以不用畏手畏脚 —— 但破坏性操作（删除、覆盖、格式化）不可逆，
动手前最好先跟用户确认一句。

## 接口速查

基础地址 `http://127.0.0.1:18800`

| 用途 | 调用 |
|---|---|
| 存活检查 | `curl -s http://127.0.0.1:18800/ping` |
| **执行命令** | `curl -s -G http://127.0.0.1:18800/run --data-urlencode "cmd=要执行的命令"` |
| 指定目录 / 超时 / 外壳 | 再加 `--data-urlencode "cwd=C:\某目录"`、`"timeout=120"`、`"shell=cmd"` |
| 读文件 | `curl -s -G http://127.0.0.1:18800/read --data-urlencode "path=C:\x\y.txt"` |
| 列目录 | `curl -s -G http://127.0.0.1:18800/ls --data-urlencode "path=C:\Users"` |
| 系统概况 | `curl -s http://127.0.0.1:18800/sysinfo` |
| 写文件 | POST `/write`，JSON 体 `{"path":"...","data":"...","base64":false}` |

## 关键坑

1. **命令里的中文和特殊字符必须 URL 编码** —— 用 `-G --data-urlencode "cmd=..."`。
   **不要**手工拼 `?cmd=...`，会乱码或被截断。
2. **默认外壳是 bash**（电脑上装了 Git Bash）。
   - `ver`、`dir` 这类是 **cmd 内建命令**，bash 里没有 → 报 `command not found`
   - 要用 cmd 加 `"shell=cmd"`；要用 Windows 的 PowerShell 加 `"shell=ps"`
3. **返回是 JSON**：`{"ok":bool, "code":退出码, "stdout":"...", "stderr":"...", "cwd":"...", "elapsed":秒}`
   - `ok=false` 但 stdout 有内容是正常的（命令本身返回非零），**别只看 ok，要看 stdout**。
4. **桥挂了怎么办**：`/ping` 没响应就是桥不在跑。
   - 启动桥（电脑侧）：`python pcbridge.py --port 18800`
   - 重建映射（电脑侧）：`adb reverse tcp:18800 tcp:18800`
   - **这两个动作只能从电脑做，平板做不了** → 告诉用户，让他在电脑上处理。
5. **依赖 adb 连接**：反向映射随 adb 连接存在。USB 拔了或 adb 重启后映射会掉，需要重做第 4 条。

## 典型用法

```bash
# 电脑卡不卡
curl -s http://127.0.0.1:18800/sysinfo

# 看桌面有什么
curl -s -G http://127.0.0.1:18800/ls --data-urlencode "path=C:\Users\林峰\Desktop"

# 用 Windows 的 PowerShell 查服务
curl -s -G http://127.0.0.1:18800/run \
  --data-urlencode "cmd=Get-Service | Where-Object {\$_.Status -eq 'Running'} | Select -First 10" \
  --data-urlencode "shell=ps"

# 看日志末尾
curl -s -G http://127.0.0.1:18800/run \
  --data-urlencode "cmd=tail -30 /c/Users/林峰/WorkBuddy/2026-08-29-02-56-29/pcbridge.log"
```

## 审计

电脑上所有经过桥的操作都记在 `pcbridge.log`（只记录，不阻止）。
