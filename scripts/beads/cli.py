"""
ABOUTME: Command-line interface for Beads.
ABOUTME: Provides bd-like commands for issue management.
"""
import click
import json
from pathlib import Path
from typing import Optional

from .storage import BeadsDatabase
from .models import IssueStatus, IssueType
from .manager import BeadsManager
from .molecules import MoleculeParser, MoleculeInstantiator
from .migrate import MemoryMigrator


def find_beads_dir() -> Path:
    """Find .beads directory in current or parent directories."""
    cwd = Path.cwd()
    for parent in [cwd] + list(cwd.parents):
        beads_dir = parent / ".beads"
        if beads_dir.exists():
            return beads_dir
    return cwd / ".beads"


def get_config() -> dict:
    """Load config from .beads/config.yaml."""
    beads_dir = find_beads_dir()
    config_file = beads_dir / "config.yaml"
    if config_file.exists():
        # Simple YAML parsing for key: value format
        config = {}
        with open(config_file, 'r') as f:
            for line in f:
                if ':' in line:
                    key, value = line.split(':', 1)
                    config[key.strip()] = value.strip()
        return config
    return {'prefix': 'gz'}


def get_db(use_global: bool = False) -> BeadsDatabase:
    """Get database for current directory or global."""
    if use_global:
        beads_dir = Path.home() / ".claude" / "beads"
        beads_dir.mkdir(parents=True, exist_ok=True)
        return BeadsDatabase(beads_dir, prefix="gzg")
    beads_dir = find_beads_dir()
    config = get_config()
    return BeadsDatabase(beads_dir, prefix=config.get('prefix', 'gz'))


@click.group()
@click.version_option(version="0.1.0")
def cli():
    """Beads: Graph-based knowledge system for Claude Code."""
    pass


@cli.command()
@click.option('--prefix', default='gz', help='ID prefix for issues')
def init(prefix: str):
    """Initialize beads database in current directory."""
    beads_dir = Path.cwd() / ".beads"
    beads_dir.mkdir(exist_ok=True)

    # Create gitignore
    gitignore = beads_dir / ".gitignore"
    gitignore.write_text("*.db\n*.lock\n.sync_state.json\n")

    # Create config
    config = beads_dir / "config.yaml"
    config.write_text(f"prefix: {prefix}\n")

    click.echo(f"Initialized beads database in {beads_dir}")


@cli.command()
@click.argument('title')
@click.option('--type', '-t', 'issue_type', default='task', help='Issue type')
@click.option('--priority', '-p', type=int, default=2, help='Priority (0-4)')
@click.option('--description', '-d', default='', help='Description')
@click.option('--parent', default='', help='Parent issue ID')
@click.option('--ephemeral', is_flag=True, help='Create as wisp (not persisted)')
def create(title: str, issue_type: str, priority: int, description: str, parent: str, ephemeral: bool):
    """Create a new issue."""
    db = get_db()
    issue = db.create(
        title=title,
        type=issue_type,
        priority=priority,
        description=description,
        parent=parent,
        ephemeral=ephemeral
    )
    click.echo(f"Created: {issue.id}")


@cli.command('list')
@click.option('--status', '-s', default=None, help='Filter by status')
@click.option('--type', '-t', 'issue_type', default=None, help='Filter by type')
@click.option('--parent', default=None, help='Filter by parent')
@click.option('--label', '-l', default=None, help='Filter by label')
@click.option('--global', '-g', 'use_global', is_flag=True, help='List from global beads (~/.claude/beads/)')
@click.option('--json', 'as_json', is_flag=True, help='Output as JSON')
def list_issues(status: str, issue_type: str, parent: str, label: str, use_global: bool, as_json: bool):
    """List issues."""
    db = get_db(use_global=use_global)
    issues = db.list(status=status, type=issue_type, parent=parent, label=label)

    if as_json:
        click.echo(json.dumps([i.to_dict() for i in issues], indent=2))
    else:
        if not issues:
            click.echo("No issues found")
            return
        for issue in issues:
            status_icon = {
                'open': '○',
                'in_progress': '◐',
                'done': '●',
                'closed': '✓',
                'hooked': '⚓',
                'pinned': '📌'
            }.get(issue.status.value, '?')
            click.echo(f"{status_icon} {issue.id}: {issue.title}")


@cli.command()
@click.argument('issue_id')
@click.option('--global', '-g', 'use_global', is_flag=True, help='Look in global beads (~/.claude/beads/)')
@click.option('--json', 'as_json', is_flag=True, help='Output as JSON')
def show(issue_id: str, use_global: bool, as_json: bool):
    """Show issue details."""
    db = get_db(use_global=use_global)
    issue = db.get(issue_id)

    if not issue:
        click.echo(f"Issue not found: {issue_id}", err=True)
        raise SystemExit(1)

    if as_json:
        click.echo(json.dumps(issue.to_dict(), indent=2))
    else:
        click.echo(f"ID: {issue.id}")
        click.echo(f"Title: {issue.title}")
        click.echo(f"Status: {issue.status.value}")
        click.echo(f"Type: {issue.type.value}")
        click.echo(f"Priority: {issue.priority}")
        if issue.description:
            click.echo(f"\nDescription:\n{issue.description}")
        if issue.depends_on:
            click.echo(f"\nDepends on: {', '.join(issue.depends_on)}")
        if issue.blocks:
            click.echo(f"Blocks: {', '.join(issue.blocks)}")
        if issue.parent:
            click.echo(f"Parent: {issue.parent}")
        if issue.children:
            click.echo(f"Children: {', '.join(issue.children)}")


@cli.command()
@click.argument('issue_id')
@click.option('--title', default=None, help='New title')
@click.option('--status', '-s', default=None, help='New status')
@click.option('--priority', '-p', type=int, default=None, help='New priority')
@click.option('--assignee', default=None, help='Assignee')
def update(issue_id: str, title: str, status: str, priority: int, assignee: str):
    """Update an issue."""
    db = get_db()
    kwargs = {}
    if title:
        kwargs['title'] = title
    if status:
        kwargs['status'] = status
    if priority is not None:
        kwargs['priority'] = priority
    if assignee:
        kwargs['assignee'] = assignee

    if not kwargs:
        click.echo("No updates specified", err=True)
        raise SystemExit(1)

    issue = db.update(issue_id, **kwargs)
    if issue:
        click.echo(f"Updated: {issue.id}")
    else:
        click.echo(f"Issue not found: {issue_id}", err=True)
        raise SystemExit(1)


@cli.command()
@click.argument('issue_id')
def close(issue_id: str):
    """Close an issue."""
    db = get_db()
    issue = db.close(issue_id)
    if issue:
        click.echo(f"Closed: {issue.id}")
    else:
        click.echo(f"Issue not found: {issue_id}", err=True)
        raise SystemExit(1)


@cli.command()
@click.argument('issue_id')
@click.argument('depends_on_id')
def depend(issue_id: str, depends_on_id: str):
    """Add dependency: ISSUE_ID depends on DEPENDS_ON_ID."""
    db = get_db()
    if db.add_dependency(issue_id, depends_on_id):
        click.echo(f"Added: {issue_id} depends on {depends_on_id}")
    else:
        click.echo("Failed to add dependency", err=True)
        raise SystemExit(1)


@cli.command()
def ready():
    """Show issues ready to work (open, not blocked)."""
    db = get_db()
    issues = db.ready()

    if not issues:
        click.echo("No issues ready")
        return

    for issue in issues:
        click.echo(f"○ {issue.id}: {issue.title}")


@cli.command()
@click.argument('molecule_id')
@click.argument('parent_id')
@click.option('--var', '-v', multiple=True, help='Context variables in key=value format')
def pour(molecule_id: str, parent_id: str, var: tuple):
    """Instantiate a molecule template onto a parent issue."""
    db = get_db()

    mol = db.get(molecule_id)
    if not mol:
        click.echo(f"Molecule not found: {molecule_id}", err=True)
        raise SystemExit(1)

    parent = db.get(parent_id)
    if not parent:
        click.echo(f"Parent not found: {parent_id}", err=True)
        raise SystemExit(1)

    # Parse context variables
    context = {}
    for v in var:
        if '=' in v:
            key, value = v.split('=', 1)
            context[key] = value

    instantiator = MoleculeInstantiator(db)
    try:
        children = instantiator.instantiate(mol, parent, context=context if context else None)
        click.echo(f"Created {len(children)} steps:")
        for child in children:
            click.echo(f"  {child.id}: {child.title}")
    except ValueError as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)


@cli.command()
@click.option('--dry-run', is_flag=True, help='Preview what would be migrated')
@click.option('--work-log-only', is_flag=True, help='Migrate only work log')
@click.option('--tasks-only', is_flag=True, help='Migrate only planned tasks')
@click.option('--include-logs', is_flag=True, help='Also migrate hook logs (JSONL)')
@click.option('--report', is_flag=True, help='Show migration status report')
@click.option('--scan-path', multiple=True, help='Additional paths to scan for .claude folders')
def migrate(dry_run: bool, work_log_only: bool, tasks_only: bool, include_logs: bool,
            report: bool, scan_path: tuple):
    """Migrate old YAML memory system to Beads.

    Migrates work_log.yaml, planned_tasks.yaml, handoff_context.yaml,
    and project context files to the new Beads system.

    Examples:

        beads migrate --dry-run       # Preview migration

        beads migrate                 # Run full migration

        beads migrate --work-log-only # Migrate only work log

        beads migrate --report        # Show current status
    """
    extra_paths = [Path(p) for p in scan_path] if scan_path else None
    migrator = MemoryMigrator()

    if report:
        # Just show current status
        result = migrator.migrate_all(dry_run=True)
        click.echo(migrator.generate_report(result))
        return

    if work_log_only:
        count, details = migrator.migrate_work_log(dry_run=dry_run)
        action = "Would migrate" if dry_run else "Migrated"
        click.echo(f"{action} {count} work log entries")
        if dry_run and count > 0:
            click.echo("\nPreview (first 5):")
            for d in details[:5]:
                click.echo(f"  - [{d.get('operation', '')}] {d.get('title', '')[:60]}")
        return

    if tasks_only:
        count, details = migrator.migrate_planned_tasks(dry_run=dry_run)
        action = "Would migrate" if dry_run else "Migrated"
        click.echo(f"{action} {count} planned tasks")
        if dry_run and count > 0:
            click.echo("\nPreview:")
            for d in details:
                click.echo(f"  - [{d.get('original_status', '')}] {d.get('title', '')[:60]}")
        return

    # Full migration
    result = migrator.migrate_all(dry_run=dry_run, include_logs=include_logs,
                                   extra_paths=extra_paths)

    if dry_run:
        click.echo(migrator.generate_report(result))
        click.echo("\nRun without --dry-run to perform migration.")
    else:
        click.echo(migrator.generate_report(result))
        click.echo("\nMigration complete!")
        click.echo("Old YAML files remain in place for reference.")
        click.echo("Use 'beads list --label migrated' to see migrated beads.")


@cli.command('label')
@click.argument('issue_id')
@click.argument('label')
def add_label(issue_id: str, label: str):
    """Add a label to an issue."""
    db = get_db()
    issue = db.get(issue_id)

    if not issue:
        click.echo(f"Issue not found: {issue_id}", err=True)
        raise SystemExit(1)

    if label not in issue.labels:
        issue.labels.append(label)
        db._rewrite_jsonl()
        click.echo(f"Added label '{label}' to {issue_id}")
    else:
        click.echo(f"Issue {issue_id} already has label '{label}'")


def main():
    """Entry point for console script."""
    cli()


if __name__ == '__main__':
    main()
