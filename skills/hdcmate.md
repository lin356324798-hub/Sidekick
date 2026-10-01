# HDC 手机调试技能 —— 用平板远程操控华为手机

> 建立日期：2026-09-28
> 目标设备：华为手机 **SGT-AL50**（HarmonyOS，鸿蒙内核 1.13.0）
> 控制端：平板 Termux（uid 2000 shell）

---

## 一、这是什么

通过 **WiFi** 用平板远程操控华为手机。华为鸿蒙用的是自家 **HDC** 协议
（HarmonyOS Device Connector），**不是 adb** —— 用 `adb connect` 会显示
`offline`、`adb pair` 会报 `protocol fault`。

本技能用 Python 重写了 HDC 协议栈，实现在 `hdc.py`，并注册为工具 `hdcmate`。

---

## 二、文件清单

| 文件 | 作用 |
|---|---|
| `hdc.py` | HDC 协议实现（447 行）：帧编解码 / 握手 / RSA 认证 / 命令执行 |
| `hdc_key.json` | RSA-3072 密钥对（首次运行自动生成，勿删） |
| `hdc_target.json` | 记住的手机地址 `{host, port}` |
| `hdc_skill.md` | 本文档 |

---

## 三、怎么用

### 3.1 手机端准备（一次性）

1. 设置 → 关于手机 → **连点「版本号」7 次** → 激活开发者选项
2. 设置 → 系统和更新 → **开发人员选项** → 打开「**无线调试**」
3. 点进「无线调试」→ 记下「**IP 地址和端口**」（如 `<手机IP>:<端口>`）
4. 手机与平板必须在**同一个 WiFi**

### 3.2 设置目标（只需一次，之后自动记住）

```
hdcmate action=target host=<手机IP> port=<端口>
```

> ⚠️ 首次连接手机屏幕会弹「**允许调试**」→ **必须点允许**，否则回 `DAEMON_UNAUTH`。

### 3.3 执行命令

```
hdcmate value="param get const.product.model"
hdcmate value="ls /data/local/tmp"
hdcmate action=test        # 测连接（复用已有连接）
```

### 3.4 命令行直用（不经过工具）

```bash
python3 ~/.termux-agent/hdc.py connect <手机IP>:<端口>    # 只做连接+认证
python3 ~/.termux-agent/hdc.py exec <手机IP>:<端口> "命令"
python3 ~/.termux-agent/hdc.py keygen                         # 重新生成密钥对
```

---

## 四、协议要点（给未来的自己）

### 4.1 帧格式

```
'HW' + 00 00 + ver(1B) + protectLen(2B,BE) + payloadLen(4B,BE) + protect + payload
```

- `protect` 是 protobuf-like 的 4 个字段：
  `1:channelId, 2:commandFlag, 3:0, 4:PAYLOAD_VCODE(=9)`

### 4.2 命令字

| 常量 | 值 | 用途 |
|---|---|---|
| `CMD_KERNEL_HANDSHAKE` | 1 | 握手 |
| `CMD_KERNEL_CHANNEL_CLOSE` | 2 | 关闭通道 |
| `CMD_KERNEL_ECHO_RAW` | 10 | shell 输出回传 |
| `CMD_UNITY_EXECUTE` | 1001 | 执行一次性命令（主力） |
| `CMD_SHELL_INIT` | 2000 | 拉起交互式 shell |
| `CMD_SHELL_DATA` | 2001 | 写 shell 的 stdin |

### 4.3 认证流程（AUTH_NONE → PUBLICKEY → SIGNATURE → OK）

```
1. 客户端发握手：authType=AUTH_NONE(0)
   buf = TLV{authtype=1, supportfeatures=heartbeat}
   （TLV 是定长 16 字节头 + 值，空格 0x20 补齐）

2. 设备回 AUTH_PUBLICKEY(3)
   → 客户端推公钥：buf = hostName + 0x0c + publicPem(UTF-8)
   → 手机弹「允许调试」，用户点允许

3. 设备回 AUTH_SIGNATURE(2)
   → 客户端用私钥签名 token：RSA-3072 / PSS / SHA512 / saltlen=64
   → 发 base64 签名

4. 设备回 AUTH_OK(4)
   → 还要检查 TLV 里 daemonauthstatus != "DAEMON_UNAUTH"
   → 若为 DAEMON_UNAUTH 说明用户还没点允许
```

### 4.4 握手报文（protobuf-like 6 字段）

```
1: banner      = "OHOS HDC"
2: authType    = 数值
3: sessionId   = 数值（随机 uint32）
4: connectKey  = "host:port"（认证后续步骤留空）
5: buf         = bytes（TLV 或 公钥 info 或 签名）
6: version     = "Ver: 3.0.0b7fdbc1aa8c5fefaa"
```

---

## 五、手机端常用命令（鸿蒙）

### 5.1 应用管理

```bash
bm dump -a -l                                   # 列出所有应用（包名）
bm dump -a                                      # 列出应用（含中文标签）
bm dump -n <包名>                               # 查某个应用详情
bm install -p /data/local/tmp/xxx.hap           # 安装 HAP
bm uninstall -n <包名>                          # 卸载
bm clean -n <包名> -d                           # 清应用数据
aa start -b <包名> -a EntryAbility              # 启动应用
aa start -b <包名> -a EntryAbility -u <userId>  # 指定用户启动（可能被权限拦）
aa force-stop <包名>                            # 强制停止
```

**已知包名**：
- 微信（鸿蒙版）：`com.tencent.wechat`
- 微信分身：**同包名**，`userId=999`（主号是 100）

### 5.2 系统信息

```bash
param get const.product.model          # 型号 → SGT-AL50
param get const.product.brand          # 品牌 → HUAWEI
uname -a                                # 内核信息
id                                      # 身份 → uid=2000(shell)
ps -A                                   # 进程列表
hidumper -s PowerManagerService         # 电源服务
hidumper -s BatteryService              # 电池
```

### 5.3 UI 自动化（uitest）⭐ 核心能力

```bash
uitest dumpLayout -p /data/local/tmp/lay.json   # 导出当前界面布局（JSON）
uitest uiInput click X Y                        # 点击坐标
uitest uiInput swipe X1 Y1 X2 Y2                # 滑动
uitest uiInput keyEvent <keyCode>               # 按键（返回=2）
uitest uiInput inputText X Y "文字"              # 输入文字
```

**布局 JSON 解析要点**：
- 每个节点含：`bounds`（`[x1,y1][x2,y2]`）、`text`、`type`、`clickable`
- **可点元素的中心点** = `((x1+x2)/2, (y1+y2)/2)`
- 快速看界面有什么：`grep -oE '"text":"[^"]{1,20}"' lay.json | sort -u`
- 按节点拆分：`sed 's/},{/}\n{/g' lay.json | grep 关键词`
- ⚠️ **绝对不要用 `tr '}' '\n'`** —— 会把 UTF-8 中文按字节拆坏

**实战案例（启动微信分身）**：
```
1. aa start -b com.tencent.wechat -a EntryAbility
   → 系统弹出「选择打开方式」（微信 / 微信1）
2. uitest dumpLayout -p /data/local/tmp/lay.json
3. 从 JSON 读出：
     微信1（分身） bounds=[81,2293][1199,2483] → 中心 (640,2388)
     微信（主）   bounds=[81,2484][1199,2674] → 中心 (640,2579)
4. uitest uiInput click 640 2388        ← 点分身
5. 再 dump 验证弹窗消失、界面已是微信
```

---

## 六、踩过的坑（重要经验）

| # | 坑 | 现象 | 解决 |
|---|---|---|---|
| 1 | **协议选错** | `adb connect` 端口通但 `offline`，`adb pair` 报 `protocol fault` | **鸿蒙用 HDC，不是 adb**；用 hdc.py |
| 2 | **工具参数冲突** | `got multiple values for argument 'value'` | 工具函数**第一个参数必须是 `cfg`**（框架按 `impl(self.cfg, **args)` 调用） |
| 3 | **分身识别不到** | `bm dump -a -l` 只列出一条微信 | 华为分身**同包名**，靠 **userId 区分**（100=主 / 999=分身） |
| 4 | **跨用户启动被拒** | `aa start -u 999` → `Error Code:10107101 Permission check failed` | shell 级没有 `INTERACT_ACROSS_LOCAL_ACCOUNTS`；**改用 uitest 点弹窗** |
| 5 | **中文被拆分** | `tr '}' '\n'` 后 grep 中文失败 | 用 `sed 's/},{/}\n{/g'` |
| 6 | **进程名截断** | `ps -A` 显示 `.tencent.wechat` | 用更宽的关键词 grep，或读 `/proc/<pid>/cmdline` |
| 7 | **端口会变** | 手机重启 / WiFi 变化后连不上 | 重新获取端口并 `hdcmate action=target` |
| 8 | **官方不给手机端工具** | 华为只发布 PC 版 hdc | 照社区逆向项目（EriDeLee/harmony-hdc，ArkTS）用 Python 重写 |

---

## 七、权限边界（说清楚，免得误期待）

| 能做 ✅ | 不能做 ❌ |
|---|---|
| 装 / 卸 / 清应用数据 | 读应用私有数据（微信数据库、相册原始库） |
| 改系统设置、启停应用 | 改系统分区 / root |
| 看系统信息（dumpsys/hidumper/param） | 绕过应用自身加密 |
| **UI 自动化**（dump 布局、点击、滑动、输入） | |
| 传文件到 `/data/local/tmp` | |
| 截图（`snapshot_display`） | |

**身份**：`uid=2000(shell)`，与 adb shell 同级，**不是 root**。

---

## 八、实测记录

| 项目 | 结果 |
|---|---|
| 手机型号 | **SGT-AL50**（HUAWEI） |
| 系统 | HarmonyOS（鸿蒙内核 1.13.0，#1 SMP aarch64 Toybox） |
| 认证 | ✅ AUTH_PUBLICKEY → AUTH_SIGNATURE → AUTH_OK |
| 执行命令 | ✅ `echo` / `id` / `uname -a` / `param get` |
| 列应用 | ✅ 383 个 |
| 启动微信 | ✅ `aa start -b com.tencent.wechat -a EntryAbility` |
| UI 自动化 | ✅ dump 布局 + 点击弹窗选「微信1」成功 |

---

## 九、参考来源

- 协议实现参考：`https://github.com/EriDeLee/harmony-hdc`（ArkTS，需代理访问）
  - 关键源码：`entry/src/main/ets/hdc/{Protocol,HdcConnection,HdcCrypto,HdcTypes}.ts`
- 帧格式与常量来源：该项目的 `HdcTypes.ts` 注释指向 `hdc-reverse-engineer`
- 依赖：`cryptography`（`pkg install python-cryptography`）
