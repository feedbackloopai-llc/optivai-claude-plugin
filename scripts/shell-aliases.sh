# Shell aliases and helper functions for the OptivAI Claude Plugin.
#
# Install (one-time per shell config):
#
#   echo 'source $HOME/Documents/optivai-claude-plugin/scripts/shell-aliases.sh' >> ~/.zshrc
#
# Or copy the function bodies below into ~/.zshrc / ~/.bashrc directly.
#
# What this file provides:
#   * beads-create  — create a bead AND auto-apply a `repo:<basename>` label
#                     when invoked from inside a git working tree.
#   * bc            — short alias for `beads-create`.
#
# Convention reference: the labeling rule is documented in AGENTS.md and in
# .claude/commands/bead-create.md ("Repo-label convention" section). See
# also the reflexive enforcement path: scripts/hooks/beads_writer.py.

beads-create() {
    local repo_label=""
    local repo_root
    if repo_root=$(git rev-parse --show-toplevel 2>/dev/null); then
        repo_label="repo:$(basename "$repo_root")"
    fi

    local out
    out=$(beads create "$@" 2>&1)
    local rc=$?
    if [ $rc -ne 0 ]; then
        echo "$out" >&2
        return $rc
    fi

    # Extract the newly-created bead ID from `beads create` output.
    # Beads currently emits IDs with three known prefixes: gz / fblai / optivai.
    local id
    id=$(echo "$out" | grep -oE '\b(gz|fblai|optivai)-[a-z0-9]+\b' | head -1)

    if [ -n "$id" ] && [ -n "$repo_label" ]; then
        # `beads label` is idempotent: re-applying the same label exits 0 with
        # an informational message. Suppress noise but keep rc visible.
        if beads label "$id" "$repo_label" >/dev/null 2>&1; then
            echo "Created $id (labeled $repo_label)"
        else
            # Fail-open: bead creation succeeded; surface the label failure
            # without rolling back the bead.
            echo "Created $id (warning: failed to apply $repo_label label)" >&2
        fi
    else
        # Either we are not in a git repo, or the bead ID could not be parsed.
        # Either way, pass through the original `beads create` output.
        echo "$out"
    fi
}

alias bc='beads-create'
