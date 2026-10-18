#!/usr/bin/env python3
"""Fake Junie script for testing. Prints sample JSON to stdout."""
import json
import sys
import time

# Check for special modes via command-line args
args = sys.argv[1:]

# Support --timeout mode: sleep forever to test timeout killing
if "--timeout" in args:
    time.sleep(9999)
    sys.exit(0)

# Support --rate-limit mode: output 429 error
if "--rate-limit" in args:
    output = {
        "error": "429 Too Many Requests",
        "status": "rate_limited"
    }
    print(json.dumps(output))
    sys.exit(0)

# Default: simulate a realistic Junie JSON output
output = {
    "answer": "This is a sample answer from the fake Junie CLI. It found 3 relevant notes in the vault and summarized them.",
    "status": "success",
    "notes_processed": 3,
    "vault": "/mnt/vault"
}

print(json.dumps(output))
sys.exit(0)
