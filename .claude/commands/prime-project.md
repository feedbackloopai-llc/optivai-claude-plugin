# Prime Project - Initialize Project Configuration

**Purpose**: Prime context for a new or existing project by reading its configuration and structure
**Usage**: `/prime-project [project_path]`

## How It Works

This skill reads the project structure and configuration files to establish context for working on a specific project.

## Usage Examples

```bash
# Prime current directory
/prime-project

# Prime a specific project
/prime-project ~/Documents/optivai/my-project
```

## Discover Project Context

```bash
TARGET_DIR="${ARGUMENTS:-$(pwd)}"
TARGET_DIR="${TARGET_DIR/#\~/$HOME}"

if [ ! -d "$TARGET_DIR" ]; then
    echo "Error: Directory '$TARGET_DIR' not found."
    exit 1
fi

echo "=== Project Context: $TARGET_DIR ==="
echo ""

# Check for project config files
echo "--- Configuration Files ---"
for f in README.md CLAUDE.md .env.example package.json pyproject.toml requirements.txt Makefile; do
    if [ -f "$TARGET_DIR/$f" ]; then
        echo "  ✓ $f"
    fi
done
echo ""

# Git info
echo "--- Git Info ---"
cd "$TARGET_DIR" && git remote get-url origin 2>/dev/null || echo "  (not a git repo)"
cd "$TARGET_DIR" && git log --oneline -5 2>/dev/null || true
echo ""

# Project structure
echo "--- Project Structure ---"
cd "$TARGET_DIR" && find . -maxdepth 2 -type f -name "*.py" -o -name "*.ts" -o -name "*.sql" -o -name "*.md" 2>/dev/null | head -30 || ls -la "$TARGET_DIR" | head -20
echo ""

# Check for database config
echo "--- Database Config ---"
if [ -f "$TARGET_DIR/.env" ] || [ -f "$TARGET_DIR/.env.example" ]; then
    echo "  Environment file found"
fi

# Check PostgreSQL connection
if [ -n "$DATABASE_URL" ]; then
    echo "  ✓ DATABASE_URL configured"
else
    echo "  ⚠ DATABASE_URL not set"
fi
echo ""

echo "=== Project Context Complete ==="
```

## Checklist After Priming

After running `/prime-project`, verify you have:

- [ ] Project README reviewed
- [ ] CLAUDE.md standards understood (if present)
- [ ] Git history reviewed
- [ ] Database connections confirmed
- [ ] Project structure understood

## Related Commands

- `/db-connect` - Test database connections
- `/prime-agent` - Full agent context priming
