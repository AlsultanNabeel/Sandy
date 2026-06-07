"""Quick test for the /voice WebSocket endpoint (echo mode).

Usage:
    python scripts/test_voice_ws.py
    python scripts/test_voice_ws.py wss://your-app.herokuapp.com/voice
"""

import asyncio
import sys
import time
import websockets

URL = sys.argv[1] if len(sys.argv) > 1 else "wss://sandy-robot-3da0693d32f7.herokuapp.com/voice"
CHUNK = bytes(range(256)) * 4  # 1 KB fake PCM chunk


async def main():
    print(f"Connecting to {URL} ...")
    async with websockets.connect(URL) as ws:
        print("Connected.")

        for i in range(3):
            t = time.perf_counter()
            await ws.send(CHUNK)
            echo = await asyncio.wait_for(ws.recv(), timeout=5)
            ms = (time.perf_counter() - t) * 1000
            match = echo == CHUNK
            print(f"  chunk {i+1}: {'OK' if match else 'MISMATCH'}, {ms:.0f}ms round-trip")

    print("Done.")


asyncio.run(main())
