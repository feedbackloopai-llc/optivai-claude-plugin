"""Setup file for Beads package."""
from setuptools import setup, find_packages

setup(
    name="beads",
    version="0.1.0",
    description="Graph-based knowledge system for Claude Code",
    author="FeedbackLoopAI",
    packages=find_packages(where="scripts"),
    package_dir={"": "scripts"},
    install_requires=[
        "click>=8.0",
        "filelock>=3.0",
        "pyyaml>=6.0",
        "psycopg2-binary>=2.9.0",
        "pgvector>=0.2.0",
        "sentence-transformers>=2.2.0",
        "anthropic>=0.30.0",
    ],
    extras_require={
        "dev": [
            "pytest>=7.0",
        ],
    },
    entry_points={
        "console_scripts": [
            "beads=beads.cli:main",
        ],
    },
    python_requires=">=3.8",
)
