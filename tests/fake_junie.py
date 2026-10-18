#!/usr/bin/env python3
"""Fake Junie script for testing. Prints sample JSON to stdout."""
import json
import sys

# Simulate a realistic Junie JSON output
output = {
    "answer": "This is a sample answer from the fake Junie CLI. It found 3 relevant notes in the vault and summarized them.",
    "status": "success",
    "notes_processed": 3,
    "vault": "/mnt/vault"
}

print(json.dumps(output))
sys.exit(0)
