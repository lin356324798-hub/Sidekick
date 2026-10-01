#!/data/data/com.termux/files/usr/bin/bash
# 提交前密钥扫描 —— 拦截不小心把 token/密钥/私密信息提交进仓库的情况
#
# 安装：bash scripts/install-hooks.sh
# 手动跑：bash scripts/check-secrets.sh
#
# 命中时会阻止提交。若确认是误报（如文档里的示例串），
# 用 git commit --no-verify 跳过。

set -uo pipefail

# 仅在 git 仓库里工作
ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || { echo "不在 git 仓库中"; exit 0; }
cd "$ROOT" || exit 0

# 取本次「将要提交」的文件列表（已暂存的），这正是钩子要检查的范围
FILES=$(git diff --cached --name-only --diff-filter=ACM 2>/dev/null)
[ -z "$FILES" ] && exit 0

FAIL=0

# 逐条规则：[名称, 正则]
RULES=(
  "GitHub Token (ghp_/gho_/ghu_/ghs_/ghr_)|gh[pousr]_[A-Za-z0-9]{36,}"
  "GitHub Fine-grained PAT|github_pat_[A-Za-z0-9_]{60,}"
  "OpenAI Key|sk-[A-Za-z0-9]{32,}"
  "Anthropic Key|sk-ant-[A-Za-z0-9_-]{32,}"
  "AWS Access Key|AKIA[0-9A-Z]{16}"
  "Google API Key|AIza[0-9A-Za-z_-]{35}"
  "Slack Token|xox[baprs]-[A-Za-z0-9-]{10,}"
  "私钥文件头|-----BEGIN [A-Z ]*PRIVATE KEY-----"
  "通用 bearer/secret 赋值|(secret|token|password|passwd|api_?key)[\"'\"'\''[:space:]]*[:=][\"'\"'\''[:space:]]*[A-Za-z0-9_/+-]{20,}"
  "中国大陆手机号|\b1[3-9][0-9]{9}\b"
  "内网 IP (192.168/10.x)|(192\.168|10)\.[0-9]{1,3}\.[0-9]{1,3}"
)

echo "[check-secrets] 扫描 $(echo "$FILES" | wc -l | tr -d ' ') 个待提交文件…"

while IFS= read -r f; do
  [ -f "$f" ] || continue
  # 跳过二进制文件
  if ! grep -qI . "$f" 2>/dev/null; then continue; fi

  for rule in "${RULES[@]}"; do
    name="${rule%%|*}"
    pat="${rule#*|}"
    # 用 -E 扩展正则；只取行号避免把密钥内容打印出来
    if hits=$(grep -nE "$pat" "$f" 2>/dev/null | cut -d: -f1 | head -5); then
      if [ -n "$hits" ]; then
        echo "  🚨 [$name] $f 第 $(echo $hits | tr '\n' ',' | sed 's/,$//') 行"
        FAIL=1
      fi
    fi
  done
done <<< "$FILES"

if [ $FAIL -ne 0 ]; then
  cat <<'MSG'

──────────────────────────────────────────────
提交被拦截：检测到疑似敏感信息（见上）。

处理办法：
  1) 若是真密钥 → 从文件里删掉，改用环境变量/配置文件（并加进 .gitignore）
  2) 若只是文档中的示例串 → 改成明显占位符，如 sk-xxxx、192.168.x.x
  3) 确认是误报、非要提交 → git commit --no-verify

注意：密钥一旦 push 出去，即使随后删除，历史里仍有记录，
      必须立刻去服务商后台吊销该密钥。
──────────────────────────────────────────────
MSG
  exit 1
fi

echo "[check-secrets] ✅ 未发现敏感信息"
exit 0
