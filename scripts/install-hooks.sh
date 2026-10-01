#!/data/data/com.termux/files/usr/bin/bash
# 安装仓库的 git 钩子（目前只有 pre-commit 密钥扫描）
#
# clone 仓库后跑一次：bash scripts/install-hooks.sh
# 因为 .git/hooks/ 不随仓库分发，每个新 clone 都要装一次。

set -euo pipefail

ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || {
  echo "错误：不在 git 仓库中，请在仓库目录下运行"; exit 1
}
cd "$ROOT"

HOOKS_DIR=".git/hooks"
mkdir -p "$HOOKS_DIR"

cat > "$HOOKS_DIR/pre-commit" <<'HOOK'
#!/data/data/com.termux/files/usr/bin/bash
exec bash "$(git rev-parse --show-toplevel)/scripts/check-secrets.sh"
HOOK

chmod +x "$HOOKS_DIR/pre-commit"
echo "✅ 已安装 pre-commit 钩子 → $HOOKS_DIR/pre-commit"
echo "   提交前会自动扫描敏感信息，命中则阻止提交。"
echo "   手动检查：bash scripts/check-secrets.sh"
