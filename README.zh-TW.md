# Sidekick

**跑在 Android 裝置上的本地 AI agent。** 一個 Python 檔案、零第三方依賴、瀏覽器當介面。

它不只是聊天 —— 它能**真的操作你的裝置**：讀寫檔案、執行指令、改程式碼、開網頁、連手機、做試算表、處理圖片。

> `Sidekick` 只是出廠預設名稱，安裝後可在「身份」頁改成任何你喜歡的名字。

**語言：** [English](README.en.md) | [简体中文](README.zh-CN.md) | **繁體中文** | [日本語](README.ja.md) | [한국어](README.ko.md) | [Español](README.es.md) | [Deutsch](README.de.md) | [Français](README.fr.md) | [Русский](README.ru.md) | [العربية](README.ar.md)

---

## 它跟別的 AI 有什麼不一樣

### 1. 傻瓜式安裝 —— 兩行指令，沒有第三步

不用懂 Python，不用設定環境，不用裝資料庫。裝好 Termux，貼兩行指令，打開瀏覽器就能用。

```bash
pkg install -y python curl
bash install.sh
```

沒有 Docker、沒有 Node、沒有依賴衝突。**只用 Python 標準函式庫**，所以不會被上游更新搞壞 —— 今天能跑，三年後照樣能跑。

### 2. 每天自己進化 —— 不用你管，也不用遠端更新

這點最特別：**它每天都自己變強一點，不需要任何人推更新包給你。**

它每天自動檢討自己的表現，挑出可改進的地方，改完寫進日誌。看幾個它自己做的工：

| 日期 | 它自己改了什麼 |
|---|---|
| 09-28 | `list_dir` 新增「依類型歸檔建議」—— 看檔案時直接告訴你圖片該放 `Pictures/`、文件該放 `Documents/` |
| 09-28 | 工具參數打錯時，自動丟棄不支援的參數，並提示相近的正確名稱（例如 `cwd` → `command`） |
| 09-29 | `read_file` 遇到二進位檔案不再吐滿螢幕亂碼，改為回報檔案類型與大小 |
| 10-01 | 控制台卡片改固定高度 —— 因為實測發現切換分頁時「儲存」按鈕會位移 447px，手指容易按空 |

> 這些不是宣傳詞，是它自己的進化日誌原文。裝到你的機器上後，它會依自己的使用情況長出**跟你有關**的改進。

**這代表什麼：** 你的 agent 和別人的 agent，用一個月後會長得不一樣。你常讓它整理相簿，它就進化出更強的圖片處理；你常拿它寫報告，它就進化出更順的文件流程。**每台裝置養出來的都是獨一無二的。**

### 3. 出 BUG 自己修 —— 你能看到它怎麼排除故障

程式掛了不會只留個錯誤訊息。它內建**自癒**機制：偵測到崩潰 → 自己讀日誌定位 → 改程式碼 → 驗證 → 記錄歸檔。

真實記錄（來自 `selfheal.json`）：

```
10-01 14:22  發現崩潰    看門狗日誌 · 指紋 f99407371b41
10-01 14:23  自癒完成    Confirmed — line 2809 是無關的日誌讀取程式碼，
                        且 tool_wx_auto 已定義，無需進一步修改
```

它甚至會**推翻錯誤判斷**：另一次自癒的結論是「這不是待修的 bug，檔案已自我修復」，並列出證據鏈表格。它不亂改。

改自己之前**先備份**，改完**做語法檢查**，起不來**自動還原**。所以「自己修自己」不等於「把自己搞死」。

### 4. 斷線也能接著做 —— 長任務不怕中斷

跑了半小時的任務，Termux 被系統砍了？重啟後它**自動接著跑**，不用你重新交代一遍。

```bash
$ tail evolve.log
[自癒] 發現顯式接續標記，自動繼續跑一輪
[自癒] 自動續跑 20260928-034008-1058（boot），第 2 次
```

手機重開、切換網路、Termux 被系統清掉 —— 它記得自己做到哪了。

---

:::tip 一句話總結

**裝起來不費力，用起來會長大，壞了會自己修，斷了能接著做。**

:::

### 裝完就能驗證 —— 不用信我說

裝好之後，直接看它自己的進化記錄：

```bash
cat ~/.termux-agent/evolve.json     # 它每天為自己做了什麼（含原因）
cat ~/.termux-agent/selfheal.json   # 它發現並修復過哪些崩潰
tail ~/.termux-agent/evolve.log     # 進化與自癒的即時日誌
```

這些檔案是它執行時**自己寫**的，不是預先塞進去的宣傳素材。你的裝置跑一段時間後，裡面會長出屬於你的記錄。

---

## 目錄

- [它跟別的 AI 有什麼不一樣](#它跟別的-ai-有什麼不一樣)
  - [傻瓜式安裝](#1-傻瓜式安裝--兩行指令沒有第三步)
  - [每天自己進化](#2-每天自己進化--不用你管也不用遠端更新)
  - [出 BUG 自己修](#3-出-bug-自己修--你能看到它怎麼排除故障)
  - [斷線也能接著做](#4-斷線也能接著做--長任務不怕中斷)
- [它是什麼](#它是什麼)
- [安裝](#安裝)
- [設定 API Key](#設定-api-key)
- [日常使用](#日常使用)
- [能力總覽](#能力總覽)
- [工具詳解（20 個）](#工具詳解20-個)
- [技能庫（36 個）](#技能庫36-個)
- [目錄結構](#目錄結構)
- [安全性說明](#安全性說明)
- [常見問題](#常見問題)

---

## 它是什麼

一個住在你手機／平板裡的 AI 助理，透過瀏覽器介面使用。

**設計取捨**

| 原則 | 說明 |
|---|---|
| **零第三方依賴** | 只用 Python 標準函式庫，`pkg install python` 即可執行。不碰任何需要編譯的原生模組，因此永遠不會因為上游更新而失效 |
| **本地儲存** | 對話、記憶、金鑰全在本機 `~/.termux-agent/`，不上傳雲端 |
| **能改自己** | 可以修改自己的原始碼來加功能，改壞了自動還原 |
| **內建看門狗** | 服務掛了自動拉起，不依賴 systemd（Android 沒有） |
| **無需遠端更新** | 沒有伺服器、沒有更新推送。它的進化由自己完成，功能成長不依賴作者發布新版 |

---

## 安裝

前置：Android 裝置裝好 [Termux](https://f-droid.org/packages/com.termux/)（**建議用 F-Droid 版**，外掛必須與主程式同簽章）。

```bash
# 1. 裝 Python
pkg update && pkg install -y python curl

# 2. 進入本目錄，一鍵安裝
bash install.sh
```

安裝腳本會依序：檢查環境 → 備份既有安裝 → 複製程式與技能 → 產生預設設定（**不覆蓋既有 API Key**）→ 啟動服務並自我檢測。

---

## 設定 API Key

首次執行需要填 API Key，兩種方式：

**方式一（推薦）**：打開 `http://127.0.0.1:8765/`，右上角「設定」裡填。

**方式二**：指令列
```bash
python3 ~/.termux-agent/agent.py config
```

預設接 DeepSeek（`https://api.deepseek.com`），也可換成任何相容 OpenAI 格式的介面 —— 包括跑在你區域網路裡的本地模型。

---

## 日常使用

```bash
bash ~/.termux-agent/start.sh              # 重啟 Termux 後恢復服務
python3 ~/.termux-agent/agent.py doctor    # 環境自檢 + 連線測試
python3 ~/.termux-agent/agent.py restart   # 重啟服務
python3 ~/.termux-agent/agent.py selfcheck # 自我體檢（原始碼／看門狗／服務／備份）
```

然後在瀏覽器打開 **http://127.0.0.1:8765/**。

### 指令列直接使用（不開介面）

```bash
python3 ~/.termux-agent/agent.py "看看磁碟用量"   # 跑一次就結束
python3 ~/.termux-agent/agent.py -c              # 續聊上次對話
python3 ~/.termux-agent/agent.py chat            # 終端機裡對話（簡陋）
```

### 開機自動啟動（選用）

裝好 [Termux:Boot](https://f-droid.org/packages/com.termux.boot/)、先手動打開一次該 App，然後：

```bash
python3 ~/.termux-agent/agent.py autostart
```

---

## 能力總覽

| 分類 | 能力 |
|---|---|
| **系統操作** | 執行 shell、以 shell 權限讀系統設定、安裝 APK、管理程序 |
| **檔案處理** | 讀寫改檔案、批次補丁、目錄瀏覽、全文正規表達式檢索、分頁讀大檔 |
| **連網** | 網頁抓取轉文字、免 Key 連網搜尋、大檔串流下載 |
| **程式碼託管** | GitHub 全流程（建倉庫／clone／pull／commit／push／搜尋程式碼） |
| **裝置互聯** | 遠端操控華為手機（HDC 協定）、以 adb shell 身分操作本機 |
| **影像處理** | 擦除文字浮水印、老照片修復、畫質增強、人像美化、去背 |
| **文件產出** | Word / Excel / PPT 產生，HTML 轉 DOCX，合約範本，排版美化 |
| **任務管理** | 待辦清單、子代理平行思考、關鍵決策徵詢 |
| **自我進化** | 修改自己的原始碼加功能，自動備份 + 語法檢查 + 失敗還原 |
| **垂直技能** | 36 個領域技能文件（寫作、設計、法律、金融、除錯……） |

---

## 工具詳解（20 個）

### 系統與檔案

| 工具 | 作用 | 亮點 |
|---|---|---|
| `bash` | 執行 shell 指令 | 主力工具，可裝套件、管理檔案、跑程式 |
| `read_file` | 讀文字檔（附行號） | 大檔分頁讀，`offset` 傳負數可從尾端看日誌結尾 |
| `write_file` | 整檔寫入 | 自動建立上層目錄 |
| `edit_file` | 精確字串替換 | 要求唯一符合；多處變動會提示行號供定位 |
| `apply_patch` | 多處／多檔補丁 | **原子生效**：任何一處對不上就整體不改，不會改一半 |
| `list_dir` | 列目錄 | 含類型、大小、修改時間 |
| `grep` | 遞迴正規表達式檢索 | 回傳 `檔案:行號: 內容` |

### 連網

| 工具 | 作用 | 注意 |
|---|---|---|
| `fetch_url` | 抓網頁／API 轉純文字 | 只適合讀文字 |
| `web_search` | 連網搜尋 | **無需 API Key** |
| `download` | 串流下載大檔 | 不截斷、帶逾時、自動重試，適合 APK／安裝包 |

### 裝置與外部系統

| 工具 | 作用 | 說明 |
|---|---|---|
| `sysshell` | 以 shell（adb）身分執行 | 權限高於一般應用程式，可讀系統設定、`dumpsys`、`getprop`、`pm/am`。**不是 root** |
| `hdcmate` | 遠端控制華為手機 | 走 HDC 協定（非 adb）。`exec` 執行指令／`target` 記住位址／`test` 測連線。可做完整 UI 自動化 |
| `github` | 操作 GitHub 倉庫 | `list/repo/read/tree/clone/pull/push/create/search` 九個動作，需設定 Token |
| `wps` | 產生 Word/Excel/PPT | 本地 MCP 服務實作，**不需登入帳號**，檔案落在 `~/storage/shared/WPS_AI/` |
| `imgedit` | 圖片 AI 處理 | 五種操作：`erase` 擦文字浮水印／`restore` 老照片修復／`enhance` 畫質增強／`beauty` 人像美化／`matting` 去背 |

### 協作與自我進化

| 工具 | 作用 | 說明 |
|---|---|---|
| `todo_write` | 維護任務清單 | 3 步以上的任務拆成 2~6 步，即時顯示在你輸入框上方，中斷重連後據此續做 |
| `subagent` | 子代理獨立思考 | 把獨立子問題丟給它單獨想（不接觸本機檔案），適合平行推進多個難點 |
| `ask_user` | 徵詢你的決策 | 彈出選擇面板。只在必須由你拍板時用：花錢、刪資料、方案取捨 |
| `selfupdate` | **修改自己** | 自動備份 + 語法檢查，失敗立即還原；成功後服務自動重啟 |

### 已封存

| 工具 | 說明 |
|---|---|
| `wx_auto` | 微信自動陪聊（讀螢幕 + 自動回覆，需授權且對方知情）。含三道安全閘門：辨識不準只記日誌、找不到傳送按鈕就放棄、每小時上限 20 則 |

---

## 技能庫（36 個）

技能是發給 AI 的**領域方法論文件**。做相關任務前它會先讀對應技能，依裡面的規範和踩坑經驗執行。

### 寫作類（11 個）

| 技能 | 用途 |
|---|---|
| `general-writer` | **L1 通用寫作後備。** 公文、週報、方案、郵件、文案、散文、新媒體，7 維品質評分 + 10 種文體適配矩陣 |
| `academic-paper-expert` | 學術論文：結構設計、文獻回顧、摘要、APA/GB-T7714 引用規範、學術潤飾 |
| `tech-blog-expert` | 技術部落格：教學、架構解析、原始碼分析、開源文件、README |
| `business-copy-expert` | 商業文案：品牌文案、行銷郵件、產品描述、Slogan、廣告合規（AIDA 模型） |
| `work-report-expert` | 職場匯報：年終總結、述職報告、競聘演講、週報月報（金字塔原理 + STAR 法則） |
| `science-writing-expert` | 科普寫作：科學解釋、科技評測、深度報導（費曼學習法） |
| `poetry-prose-expert` | 詩歌散文：現代詩、古體詩詞、隨筆、文學評論 |
| `stock-research-report-expert` | **L2 證券研報**：產業深度、個股研究、動態評論、商業計畫書，四檔篇幅 |
| `legal-contract-expert` | **L2 法律合約**：起草與審查，必備條款完整性、權利義務對稱性、高風險點防範 |
| `humanizer-zh` | 去 AI 味（中文）：依維基百科「AI 寫作特徵」指南偵測並修復 |
| `humanizer` | 去 AI 味（英文版） |

### 文件產出類（5 個）

| 技能 | 用途 |
|---|---|
| `doc-typeset` | **排版美化**：消費 design tokens + 內容，輸出精美 HTML。內建 7 種垂直範本（合約／學術論文／公文／商務報告／會議紀錄／研報／年報） |
| `html-to-docx` | HTML 高保真轉 Word。支援 CSS 變數前處理、10+ 種元素精確對映、14 種 CSS 屬性 |
| `format-extract` | .docx 轉語意化 HTML + 擷取內嵌圖片（保留標題階層、表格樣式、縮排、顏色） |
| `generate-fillable-contract-html` | 產生可填寫的中文合約、報價單、授權委託書 HTML |
| `underline-toolkit` | 底線文件：`create` 產生填空範本／`fill` 對既有範本回填資料（合約、申請表、論文封面） |
| `html-review` | **HTML 品質閘門**：對排版輸出做 5 維度檢測（token 合規、結構完整、排版合理、文體契合、裝飾適度），不通過則退回定向修正 |

### 設計類（8 個）

| 技能 | 用途 |
|---|---|
| `design-router` | 設計任務調度器：先判斷該用哪套設計族，再分發 |
| `design-token` | 依文件類型輸出標準化設計 token，驅動 doc-typeset 的所有樣式決策 |
| `design-variables` | 綁定／解除設計變數（design tokens）到節點屬性 |
| `ardot-design-to-code` | 設計稿轉前端程式碼，或從網站擷取設計系統／樣式指南 |
| `ardot-ui-design` | UI／介面設計：網頁、儀表板、著陸頁、行動版介面 |
| `ardot-poster` | 視覺海報：海報、傳單、看板、Banner、活動主視覺 |
| `ardot-slides` | 簡報設計（不是 .pptx 檔案，是設計稿） |
| `component-instance` | 元件實例管理：建立／更新實例、設定元件屬性、切換變體 |
| `shared-styles` | 共享樣式綁定／解除（文字樣式、填色、筆畫、效果） |

### 除錯維運類（5 個）

| 技能 | 用途 |
|---|---|
| `termux-traps` | **Android Termux 環境坑**：這類裝置上的各種陷阱與正確姿勢 |
| `log-debug` | 日誌排查指南：出問題先看哪個檔案 |
| `selfupdate` | 自我修改正規流程：改 `agent.py` 的正確通道與鐵律 |
| `ui-debug` | Web 介面除錯：改 UI 並當場驗證 |
| `pc-debug` | 透過「PC 除錯橋」在那台 Windows 電腦上執行指令、讀寫檔案 |

### 裝置互聯類（2 個）

| 技能 | 用途 |
|---|---|
| `hdcmate` | HDC 手機除錯：協定說明、鴻蒙常用指令、UI 自動化、踩過的坑 |
| `chrome-cdp` | Chrome 除錯抓取：用 DevTools 協定讀網頁資料，不碰螢幕 |

### 技能管理類（4 個）

| 技能 | 用途 |
|---|---|
| `find-skills` | 幫你發現和安裝技能 |
| `skill-creator` | 建立新技能的指南 |
| `marketplace-skill-installer` | 從技能市場搜尋安裝 |
| `underline-toolkit` | 見上方「文件產出類」 |

---

## 目錄結構

```
~/.termux-agent/
├── agent.py          主程式（單檔，純標準函式庫）
├── cdp.py            CDP 客戶端（瀏覽器除錯抓取，ui-debug / chrome-cdp 技能用）
├── chrome_read.py    Chrome 網頁資料讀取（chrome-cdp 技能用）
├── hdc.py            HDC 協定實作（hdcmate 工具用，447 行）
├── wps.py            WPS 文件產生
├── wps_mcp_server.py WPS 本地 MCP 服務
├── mcp_call.py       MCP 呼叫輔助
├── config.json       設定（含 API Key，權限 600）
├── skills/           技能文件（36 個）
├── sessions/         對話記錄
├── memory.md         長期記憶
├── evolve.json       進化日誌（每天自己改了什麼，含原因）
├── evolve.log        進化執行日誌
├── selfheal.json     自癒歷史（發現崩潰 → 修復完成）
├── selfheal.py       自癒與續跑模組
├── uploads/          上傳的圖片
├── outputs/          產生的成品
├── versions/         原始碼版本備份（自更新用）
├── logs/             日誌
├── tmp/              暫存檔
├── supervisor.sh     看門狗（自動產生）
└── start.sh          啟動腳本
```

---

## 安全性說明

- 介面**只監聽 `127.0.0.1`**，不對區域網路開放（介面含指令執行權限）
- `config.json`、Token 檔案權限均為 `600`
- 格式化、直寫區塊裝置、刪根目錄等不可逆操作會被自動攔下
- 沒有 root 權限，也讀不到其他應用程式的私有資料
- 環境自檢：`python3 ~/.termux-agent/agent.py doctor`

---

## 常見問題

**服務起不來？**
```bash
python3 ~/.termux-agent/agent.py selfcheck   # 看它自己怎麼說
tail -30 ~/.termux-agent/supervisor.log      # 看看門狗日誌
```

**它真的會自己進化嗎？進化了什麼？**
看 `~/.termux-agent/evolve.json`，裡面每條都記著改了什麼、為什麼改。指令列快速看：
```bash
python3 -c "import json;print('\n'.join(f'{e[\"time\"]}  {e[\"summary\"].splitlines()[0]}' for e in json.load(open('$HOME/.termux-agent/evolve.json'))['log']))"
```

**進化和遠端更新有什麼差別？**
不連網下載任何更新包，也沒有伺服器在推版本。它是**讀自己的程式碼和日誌，就地改**。所以每台裝置養出來的能力不一樣，取決於你怎麼用它。

**它改自己程式碼，改壞了怎麼辦？**
走 `selfupdate` 通道會**先備份 → 改完做語法檢查 → 失敗立即還原**；即使新程式碼起來了但執行異常，看門狗也會還原到上一版。自癒過程本身也會留下記錄（`selfheal.json`）。

**長任務跑到一半被系統砍了？**
重啟後自動接著跑，不用重新交代。日誌裡會出現「自動續跑」字樣。

**改了程式碼沒生效？**
新程式碼要先被瀏覽器載入一次才生效，重新整理一下頁面。

**想看它踩過哪些坑？**
`skills/termux-traps.md` 記錄了這類裝置上的各種環境陷阱。

**想加新能力？**
用 `skill-creator` 技能建立一個新的 `.md` 放進 `skills/`，或讓 AI 用 `selfupdate` 給自己加工具。

---

## 授權

個人自用專案，隨意取用。
