"""Custom Claude Code commands for agent activity logging"""

import os
import sys
from pathlib import Path

# Add the claude directory to Python path for imports
claude_dir = Path(__file__).parent.parent
sys.path.insert(0, str(claude_dir))