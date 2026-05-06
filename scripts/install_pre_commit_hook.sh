#!/bin/bash
# One-shot installer: copies the pre-commit hook into .git/hooks/pre-commit.
# Run once per clone of the repo. Idempotent (overwrites previous version).
set -e
cd "$(dirname "$0")/.."

HOOK=".git/hooks/pre-commit"
mkdir -p "$(dirname "$HOOK")"
cat > "$HOOK" <<'EOF'
#!/bin/bash
# Profoundd pre-commit: catch wrong-host port bindings in docker-compose files.
# Skip when no compose file is staged.
STAGED=$(git diff --cached --name-only --diff-filter=ACM | grep -E '^docker-compose\.[a-z0-9_-]+\.yml$' || true)
if [ -z "$STAGED" ]; then
    exit 0
fi
echo "Running compose host-binding lint…"
python3 scripts/validate_compose_hosts.py -f $STAGED
EOF
chmod +x "$HOOK"
echo "Installed pre-commit hook at $HOOK"
echo "Test: edit a docker-compose.<host>.yml with a wrong-host IP and try to commit."
