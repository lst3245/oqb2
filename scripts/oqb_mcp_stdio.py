#!/usr/bin/env python3
"""Stdio bridge to OQB's agent endpoint, for MCP clients that can only spawn
a local process (docs/modules/agent-mcp.md).

Reads one JSON-RPC message per line on stdin, POSTs it to ``/mcp`` with the
bearer token, and writes each JSON response as one line on stdout. No app
import, no database, standard library only.

    OQB_MCP_TOKEN=<AGENT_API_TOKEN> python scripts/oqb_mcp_stdio.py \
        [--url http://127.0.0.1:5000/mcp] [--agent-name Grok]

The token comes from ``OQB_MCP_TOKEN`` (never a command-line argument, which
other users of the host can see in the process list). Errors go to stderr.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_URL = 'http://127.0.0.1:5000/mcp'
TIMEOUT_SECONDS = 600


def post(url, token, agent_name, line):
    req = urllib.request.Request(url, data=line.encode('utf-8'), method='POST', headers={
        'Content-Type': 'application/json',
        'Accept': 'application/json, text/event-stream',
        'Authorization': f'Bearer {token}',
        **({'X-Agent-Name': agent_name} if agent_name else {}),
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
        if resp.status == 202:
            return None
        return resp.read().decode('utf-8')


def error_reply(line, message):
    try:
        mid = json.loads(line).get('id')
    except (ValueError, AttributeError):
        mid = None
    if mid is None:
        return None
    return json.dumps({'jsonrpc': '2.0', 'id': mid, 'error': {'code': -32603, 'message': message}})


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--url', default=os.environ.get('OQB_MCP_URL', DEFAULT_URL))
    ap.add_argument('--agent-name', default=os.environ.get('OQB_AGENT_NAME', ''))
    args = ap.parse_args(argv)
    token = os.environ.get('OQB_MCP_TOKEN', '').strip()
    if not token:
        print('OQB_MCP_TOKEN is not set', file=sys.stderr)
        return 2
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            out = post(args.url, token, args.agent_name, line)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode('utf-8', 'replace')[:500]
            print(f'HTTP {exc.code}: {detail}', file=sys.stderr)
            out = error_reply(line, f'OQB answered HTTP {exc.code}')
        except (urllib.error.URLError, OSError) as exc:
            print(f'cannot reach {args.url}: {exc}', file=sys.stderr)
            out = error_reply(line, 'OQB is not reachable')
        if out:
            sys.stdout.write(out.strip() + '\n')
            sys.stdout.flush()
    return 0


if __name__ == '__main__':
    sys.exit(main())
