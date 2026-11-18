#!/usr/bin/env bash
# ============================================================================
# Hermes Agent Setup Script — THE dev-environment entry point.
# ============================================================================
# Sets up the pm-managed development environment from a fresh clone:
#   1. Locate system uv on PATH.
#   2. Use uv to install and locate bootstrap Python.
#      PM owns the final interpreter, tool store, and dependency generation.
#   3. Point you at `source ./activate` — the venv-style way to put the pm
#      env (PATH + tool vars) into your current shell.
# There is no pip fallback tier here on purpose.
# ============================================================================

set -e

# Setup and activation prepare the isolated test environment. Installers call
# pm.cli directly and never select it.
runtime_only=false
test_environment="--test-environment"
for option in "$@"; do
    case "$option" in
        --runtime-only) runtime_only=true ;;
        --test-environment|--test-environment=*) test_environment="$option" ;;
        *) printf 'Unknown setup option: %s\n' "$option" >&2; exit 2 ;;
    esac
done
# Colors
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Prevent uv from discovering config files (uv.toml, pyproject.toml) from the
# wrong user's home directory when running under sudo -u <user>.  See #21269.
export UV_NO_CONFIG=1

echo ""
echo -e "${CYAN}☤ Hermes Agent Setup${NC}"
echo ""

# ============================================================================
# Locate system uv.
# ============================================================================

echo -e "${CYAN}→${NC} Checking for uv..."

lock="$SCRIPT_DIR/pm/lock.json"
[ -f "$lock" ] || { echo -e "${RED}✗${NC} pm/lock.json not found" >&2; exit 1; }


case "$(uname -s)" in
  Linux) os=linux ;;
  *) echo -e "${RED}✗${NC} unsupported OS $(uname -s); Linux is required" >&2; exit 1 ;;
esac
case "$(uname -m)" in
  arm64|aarch64) arch=arm64 ;;
  x86_64|amd64) arch=x64 ;;
  *) echo -e "${RED}✗${NC} unsupported arch $(uname -m)" >&2; exit 1 ;;
esac
target="$os-$arch"

# The machine-written lock has one member per line. Follow object names and
# braces, not indentation, to read pins before Python is available.
pin() { # $1 = field, $2 = package
  awk -F '"' -v package="$2" -v target="$target" -v field="$1" '
    /^[[:space:]]*("[^"]+"[[:space:]]*:[[:space:]]*)?\{[[:space:]]*$/ {
      path[++depth] = $2; next
    }
    /^[[:space:]]*}[[:space:]]*,?[[:space:]]*$/ {
      delete path[depth--]; next
    }
    path[2] == "packages" && path[3] == package && $2 == field &&
      ((field == "version" && depth == 3) ||
       (depth == 5 && path[4] == "artifacts" && path[5] == target)) {
      print $4; exit
    }' "$lock"
}
py_version="$(pin version python | cut -d+ -f1 | cut -d. -f1,2)"
uv="$(command -v uv)" || { echo -e "${RED}✗${NC} uv is required on PATH; install it with your system package manager" >&2; exit 1; }
"$uv" --version >/dev/null || { echo -e "${RED}✗${NC} uv on PATH cannot run" >&2; exit 1; }
echo -e "${GREEN}✓${NC} system uv found ($("$uv" --version))"

# ============================================================================
# Delegate to pm: python + venv + tool store + hash-verified venv sync
# ============================================================================

echo -e "${CYAN}→${NC} Installing python + tools + dependencies via pm (hash-verified via uv.lock)..."
echo -e "${CYAN}→${NC} (first run on a fresh checkout can take 1-5 minutes)"

py_request="$py_version"
"$uv" python install --no-bin --no-registry "$py_request"
boot_py="$("$uv" python find --managed-python "$py_request")"
boot_py="${boot_py%$'\r'}"
# Activation trusts the recorded tool digest; a direct setup re-checks it
pm_args=("$test_environment")
[ "$runtime_only" = true ] && pm_args+=(--trust-recorded)
if ! "$boot_py" -m pm.cli install "${pm_args[@]}"; then
    echo -e "${RED}✗${NC} pm install failed — see output above."
    exit 1
fi
echo -e "${GREEN}✓${NC} Tools + dependencies installed (hash-verified via pm + uv.lock)"

if [ "$runtime_only" = true ]; then
    exit 0
fi

# ============================================================================
# Environment file
# ============================================================================

if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        cp .env.example .env
        # .env holds API keys — restrict to owner-only access (matches
        # scripts/install.sh which already chmods 600 after creation).
        chmod 600 .env 2>/dev/null || true
        echo -e "${GREEN}✓${NC} Created .env from template"
    fi
else
    # Tighten an existing .env's perms in case it was created elsewhere
    # under a permissive umask.
    chmod 600 .env 2>/dev/null || true
    echo -e "${GREEN}✓${NC} .env exists"
fi

# ============================================================================
# Publish user-facing launchers
# ============================================================================

echo -e "${CYAN}→${NC} Setting up hermes command..."

# Reuse the bootstrap interpreter only to run the shared launcher writer.
bin_dir="$HOME/.local/bin"
if [ "$os" = win32 ]; then
    bin_dir="$(cygpath -am "${HERMES_HOME:-${LOCALAPPDATA:-$HOME/AppData/Local}/hermes}/bin")"
fi
if ! "$boot_py" -I -X utf8 hermes_cli/_launchers.py "$bin_dir"; then
    echo -e "${RED}✗${NC} launcher publication failed" >&2
    exit 1
fi
echo -e "${GREEN}✓${NC} Published Hermes commands in $bin_dir"

if [ "$os" != win32 ]; then
    # Determine the appropriate shell config file
    SHELL_CONFIG=""
    if [[ "$SHELL" == *"zsh"* ]]; then
            SHELL_CONFIG="$HOME/.zshrc"
        elif [[ "$SHELL" == *"bash"* ]]; then
            SHELL_CONFIG="$HOME/.bashrc"
            [ ! -f "$SHELL_CONFIG" ] && SHELL_CONFIG="$HOME/.bash_profile"
        else
            # Fallback to checking existing files
            if [ -f "$HOME/.zshrc" ]; then
                SHELL_CONFIG="$HOME/.zshrc"
            elif [ -f "$HOME/.bashrc" ]; then
                SHELL_CONFIG="$HOME/.bashrc"
            elif [ -f "$HOME/.bash_profile" ]; then
                SHELL_CONFIG="$HOME/.bash_profile"
            fi
        fi

        if [ -n "$SHELL_CONFIG" ]; then
            # Touch the file just in case it doesn't exist yet but was selected
            touch "$SHELL_CONFIG" 2>/dev/null || true

            if ! echo "$PATH" | tr ':' '\n' | grep -q "^$HOME/.local/bin$"; then
                if ! grep -q '\.local/bin' "$SHELL_CONFIG" 2>/dev/null; then
                    echo "" >> "$SHELL_CONFIG"
                    echo "# Hermes Agent — ensure ~/.local/bin is on PATH" >> "$SHELL_CONFIG"
                    echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$SHELL_CONFIG"
                    echo -e "${GREEN}✓${NC} Added ~/.local/bin to PATH in $SHELL_CONFIG"
                else
                    echo -e "${GREEN}✓${NC} ~/.local/bin already in $SHELL_CONFIG"
                fi
            else
                echo -e "${GREEN}✓${NC} ~/.local/bin already on PATH"
            fi
        fi
fi

# ============================================================================
# Seed bundled skills into ~/.hermes/skills/
# ============================================================================

HERMES_SKILLS_DIR="${HERMES_HOME:-$HOME/.hermes}/skills"
mkdir -p "$HERMES_SKILLS_DIR"

echo ""
echo "Syncing bundled skills to ~/.hermes/skills/ ..."
if "$boot_py" -m tools.skills_sync 2>/dev/null; then
    echo -e "${GREEN}✓${NC} Skills synced"
else
    # Fallback: copy if sync script fails (missing deps, etc.)
    if [ -d "$SCRIPT_DIR/skills" ]; then
        cp -rn "$SCRIPT_DIR/skills/"* "$HERMES_SKILLS_DIR/" 2>/dev/null || true
        echo -e "${GREEN}✓${NC} Skills copied"
    fi
fi

# ============================================================================
# Done
# ============================================================================

echo ""
echo -e "${GREEN}✓ Setup complete!${NC}"
echo ""
echo "Next steps:"
echo ""
echo "  1. Activate the dev environment (venv-style, in THIS shell):"
echo "     source ./activate"
echo ""
echo "  2. Run the setup wizard to configure API keys:"
echo "     hermes setup"
echo ""
echo "  3. Start chatting:"
echo "     hermes"
echo ""
echo "Other commands:"
echo "  hermes pm install     # Re-run the tool + dependency install"
echo "  hermes status         # Check configuration"
echo "  hermes doctor         # Diagnose issues"
echo "  deactivate            # Undo the activation (restore PATH etc.)"
echo ""
