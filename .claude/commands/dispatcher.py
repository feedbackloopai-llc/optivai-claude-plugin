#!/usr/bin/env python3
"""
Command dispatcher for Claude Code custom commands
"""

import sys
import json
import argparse
from pathlib import Path
from typing import Dict, Any

from activity_commands import execute_command, CommandResult

def parse_command_args(command_config: Dict, args: list) -> Dict[str, Any]:
    """Parse command arguments based on configuration"""
    parser = argparse.ArgumentParser(
        description=command_config.get("description", ""),
        add_help=False
    )
    
    # Add parameters from configuration
    parameters = command_config.get("parameters", {})
    for param_name, param_config in parameters.items():
        param_type = param_config.get("type", "string")
        required = param_config.get("required", False)
        default = param_config.get("default")
        description = param_config.get("description", "")
        
        # Convert type string to actual type
        if param_type == "int":
            param_type = int
        elif param_type == "float":
            param_type = float
        elif param_type == "bool":
            param_type = bool
        else:
            param_type = str
        
        # Add argument
        if required:
            parser.add_argument(
                f"--{param_name}",
                type=param_type,
                required=True,
                help=description
            )
        else:
            parser.add_argument(
                f"--{param_name}",
                type=param_type,
                default=default,
                help=description
            )
    
    # Parse arguments
    parsed_args, unknown = parser.parse_known_args(args)
    
    # Handle positional arguments for some commands
    kwargs = vars(parsed_args)
    
    # Add any remaining args as 'extra_args'
    if unknown:
        kwargs['extra_args'] = unknown
    
    return kwargs

def load_commands_config() -> Dict:
    """Load commands configuration"""
    config_path = Path(".claude/commands.json")
    if config_path.exists():
        with open(config_path, 'r') as f:
            return json.load(f)
    return {"commands": {}}

def main():
    """Main dispatcher function"""
    if len(sys.argv) < 2:
        print("Usage: python dispatcher.py <command> [args...]")
        sys.exit(1)
    
    command_name = sys.argv[1]
    command_args = sys.argv[2:]
    
    # Load command configuration
    config = load_commands_config()
    commands_config = config.get("commands", {})
    
    # Check if command exists
    if command_name not in commands_config:
        # Check aliases
        for cmd, cmd_config in commands_config.items():
            aliases = cmd_config.get("aliases", [])
            if command_name in aliases:
                command_name = cmd
                break
        else:
            print(f"Error: Unknown command '{command_name}'")
            print("Available commands:")
            for cmd in commands_config.keys():
                print(f"  /{cmd}")
            sys.exit(1)
    
    command_config = commands_config[command_name]
    
    try:
        # Parse arguments
        kwargs = parse_command_args(command_config, command_args)
        
        # Execute command
        result = execute_command(command_name, **kwargs)
        
        # Output result
        if result.success:
            print(result.message)
            if result.data and "--json" in command_args:
                print("\n" + json.dumps(result.data, indent=2))
        else:
            print(f"Error: {result.message}", file=sys.stderr)
            sys.exit(1)
            
    except Exception as e:
        print(f"Error executing command: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()