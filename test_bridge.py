#!/usr/bin/env python3
"""Test the FreeCAD MCP bridge directly via TCP."""
import json
import socket
import sys

def send_command(host, port, tool, args=None, req_id=1):
    """Send a command to the FreeCAD bridge and return the response."""
    if args is None:
        args = {}

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.connect((host, port))
        request = {"id": req_id, "tool": tool, "args": args}
        s.sendall((json.dumps(request) + "\n").encode())

        # Read response
        data = b""
        while b"\n" not in data:
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk

        return json.loads(data.decode())

def main():
    host = "127.0.0.1"
    port = 9876

    print("Testing FreeCAD MCP bridge on %s:%d\n" % (host, port))

    # Test 1: Ping
    print("1. Ping...")
    try:
        resp = send_command(host, port, "ping")
        if "result" in resp:
            print("   OK: %s" % json.dumps(resp["result"]))
        else:
            print("   FAIL: %s" % json.dumps(resp.get("error", "no result")))
    except Exception as e:
        print("   FAIL: %s" % e)
        return

    # Test 2: Get document info
    print("\n2. Get document info...")
    try:
        resp = send_command(host, port, "get_document_info", req_id=2)
        if "result" in resp:
            print("   OK: %s" % json.dumps(resp["result"], indent=4))
        else:
            print("   FAIL: %s" % json.dumps(resp.get("error", "no result")))
    except Exception as e:
        print("   FAIL: %s" % e)

    # Test 3: Create a box
    print("\n3. Create box (50x30x20)...")
    try:
        resp = send_command(host, port, "create_box", {
            "length": 50, "width": 30, "height": 20,
            "name": "TestBox"
        }, req_id=3)
        if "result" in resp:
            print("   OK: %s" % json.dumps(resp["result"], indent=4))
        else:
            print("   FAIL: %s" % json.dumps(resp.get("error", "no result")))
    except Exception as e:
        print("   FAIL: %s" % e)

    # Test 4: List objects
    print("\n4. List objects...")
    try:
        resp = send_command(host, port, "list_objects", req_id=4)
        if "result" in resp:
            objects = resp["result"].get("objects", [])
            print("   OK: %d objects" % len(objects))
            for obj in objects:
                print("     - %s (%s)" % (obj.get("name"), obj.get("type")))
        else:
            print("   FAIL: %s" % json.dumps(resp.get("error", "no result")))
    except Exception as e:
        print("   FAIL: %s" % e)

    print("\nDone!")

if __name__ == "__main__":
    main()
