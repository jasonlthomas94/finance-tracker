"""Loads the household's personal settings.

Reads household.toml (private, gitignored), or the file named by the
HOUSEHOLD_CONFIG environment variable. If neither exists it falls back to
household.example.toml, which has made-up values and matches the demo data,
so a fresh clone still runs.
"""
import os
import sys
import tomllib

HERE = os.path.dirname(os.path.abspath(__file__))


def _path():
    explicit = os.getenv('HOUSEHOLD_CONFIG')
    if explicit:
        return explicit if os.path.isabs(explicit) else os.path.join(HERE, explicit)
    private = os.path.join(HERE, 'household.toml')
    if os.path.exists(private):
        return private
    print("household.toml not found; using household.example.toml (made-up settings)", file=sys.stderr)
    return os.path.join(HERE, 'household.example.toml')


CONFIG_PATH = _path()
with open(CONFIG_PATH, 'rb') as f:
    CONFIG = tomllib.load(f)
