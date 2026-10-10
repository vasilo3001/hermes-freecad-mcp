#!/usr/bin/env python3
"""Test the FreeCAD MCP server end-to-end with proper async handling."""
import asyncio
import json
import sys
import os


async def test_mcp():
    """Run the MCP server and send test commands."""
    env = os.environ.copy()
    env["FREECAD_HOST"] = "127.0.0.1"

    proc = await asyncio.create_subprocess_exec(
        "python3", "mcp_server.py",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )

    # Test messages
    msgs = [
        {"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}},
        {"jsonrpc":"2.0","method":"notifications/initialized"},
        {"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}},
        {"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"freecad_ping","arguments":{}}},
        {"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"freecad_create_box","arguments":{"length":50,"width":30,"height":20,"name":"TestBox"}}},
        {"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"freecad_list_objects","arguments":{}}},
    ]

    # Send all messages
    for msg in msgs:
        line = json.dumps(msg) + "\n"
        proc.stdin.write(line.encode())
        await proc.stdin.drain()

    # Close stdin to signal we're done
    await proc.stdin.wait_closed()

    # Read all output
    output = await proc.stdout.read()
    stderr = await proc.stderr.read()

    print("=== STDERR ===")
    print(stderr.decode()[:500] if stderr else "(empty)")
    print()
    print("=== RESULTS ===")

    for line in output.decode().strip().split("\n"):
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
            if "id" not in msg:
                continue
            print(f"--- id={msg['id']} ---")
            if "result" in msg:
                r = msg["result"]
                if "tools" in r:
                    print(f"  tools/list: {len(r['tools'])} tools")
                elif "content" in r:
                    text = r["content"][0]["text"]
                    try:
                        parsed = json.loads(text)
                        print(f"  {json.dumps(parsed, indent=2)[:500]}")
                    except:
                        print(f"  {text[:500]}")
                    if r.get("is_error"):
                        print("  (ERROR)")
            if "error" in msg:
                print(f"  ERROR: {json.dumps(msg['error'])[:300]}")
        except json.JSONDecodeError:
            pass

    await proc.wait()


if __name__ == "__main__":
    asyncio.run(test_mcp())
