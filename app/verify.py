#!/usr/bin/env python3
"""
Quick verification script to test core functionality.
Run this after starting the application to verify everything works.
"""

import asyncio
import json
import sys
import time
import requests
import websockets


async def verify_application():
    """Run verification tests."""
    print("🔍 Django WebSocket Application Verification")
    print("=" * 50)
    
    results = []
    
    # Test 1: HTTP Health Check
    print("\n1️⃣ Testing HTTP endpoints...")
    try:
        response = requests.get("http://localhost:8000/healthz", timeout=5)
        if response.status_code == 200:
            print("   ✅ Health check: PASS")
            results.append(True)
        else:
            print(f"   ❌ Health check: FAIL (status {response.status_code})")
            results.append(False)
    except Exception as e:
        print(f"   ❌ Health check: FAIL ({e})")
        results.append(False)
    
    # Test 2: Metrics Endpoint
    try:
        response = requests.get("http://localhost:8000/metrics", timeout=5)
        if response.status_code == 200 and "websocket_connections_active" in response.text:
            print("   ✅ Metrics endpoint: PASS")
            results.append(True)
        else:
            print("   ❌ Metrics endpoint: FAIL")
            results.append(False)
    except Exception as e:
        print(f"   ❌ Metrics endpoint: FAIL ({e})")
        results.append(False)
    
    # Test 3: WebSocket Connection
    print("\n2️⃣ Testing WebSocket functionality...")
    try:
        async with websockets.connect("ws://localhost:8000/ws/chat/") as ws:
            print("   ✅ WebSocket connection: PASS")
            results.append(True)
            
            # Test 4: Message Counting
            for i in range(1, 4):
                await ws.send(json.dumps({"message": f"Test {i}"}))
                response = await ws.recv()
                data = json.loads(response)
                if data.get("count") == i:
                    print(f"   ✅ Message {i} count: PASS")
                    results.append(True)
                else:
                    print(f"   ❌ Message {i} count: FAIL (expected {i}, got {data.get('count')})")
                    results.append(False)
                    
    except Exception as e:
        print(f"   ❌ WebSocket connection: FAIL ({e})")
        results.append(False)
    
    # Test 5: Concurrent Connections
    print("\n3️⃣ Testing concurrent connections...")
    try:
        connections = []
        for i in range(10):
            ws = await websockets.connect("ws://localhost:8000/ws/chat/")
            connections.append(ws)
        
        print(f"   ✅ Created {len(connections)} concurrent connections: PASS")
        results.append(True)
        
        # Clean up
        for ws in connections:
            await ws.close()
            
    except Exception as e:
        print(f"   ❌ Concurrent connections: FAIL ({e})")
        results.append(False)
    
    # Summary
    print("\n" + "=" * 50)
    passed = sum(results)
    total = len(results)
    
    if passed == total:
        print(f"✅ ALL TESTS PASSED ({passed}/{total})")
        print("\n🎉 Your Django WebSocket application is working correctly!")
        print("\n📝 Next steps:")
        print("   1. Run load tests: make loadtest")
        print("   2. Test blue-green deployment: make promote")
        print("   3. Monitor metrics: http://localhost:3000")
        return 0
    else:
        print(f"❌ SOME TESTS FAILED ({passed}/{total} passed)")
        print("\n🔧 Please check:")
        print("   1. All services are running: docker-compose ps")
        print("   2. Check logs: docker-compose logs app_blue")
        print("   3. Ensure ports 8000, 6379, 5432 are not in use")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(verify_application())
    sys.exit(exit_code)
