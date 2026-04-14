#!/usr/bin/env python3
"""Test steer mechanism via WebSocket."""
import asyncio
import json
import websockets

async def test_steer():
    project_id = "7e889a90"
    uri = f"ws://localhost:8001/ws/{project_id}"

    async with websockets.connect(uri) as ws:
        print(f"Connected to WebSocket for project {project_id}")

        # Send a pause command
        pause_msg = json.dumps({"type": "pause"})
        await ws.send(pause_msg)
        print("Sent PAUSE command via WebSocket")

        # Wait for response
        try:
            response = await asyncio.wait_for(ws.recv(), timeout=2.0)
            print(f"Received: {response[:200]}")
        except asyncio.TimeoutError:
            print("No immediate response (expected for pause)")

        # Give it a moment to process
        await asyncio.sleep(1)

        print("\nTest complete! Check the web UI to see if pipeline paused.")

if __name__ == "__main__":
    asyncio.run(test_steer())
