#!/usr/bin/env python3
"""عميل اللابتوب لتجربة قلب الروبوت الصوتي (Phase 4 / V3.7) — قبل وصول الـ ESP32-S3.

اللابتوب بيلعب دور مايك+سمّاعة الروبوت: بيسجّل من المايك ويبثّه عبر WebSocket
لـ `voice_ws.py` (Gemini Live)، وبيسمّعك رد ساندي. بيختبر كل الحلقة:
محادثة صوتية + تنفيذ أوامر + (لو SANDY_REQUIRE_SPEAKER_AUTH=1) تمييز صوتك.

التنصيب (محلي فقط — مش على السيرفر):
    pip install websockets sounddevice numpy

التشغيل:
    python scripts/voice_laptop_client.py \
        --url wss://<your-app>.herokuapp.com/voice

المتغيّرات (env) — اختيارية:
    SANDY_VOICE_WS_URL    رابط الـ WebSocket (بدل --url)
    SANDY_WS_HMAC_KEY     مفتاح الـ HMAC لو السيرفر مضبوط عليه
    ROBOT_WS_SECRET       السرّ القديم البسيط (بديل عن HMAC)

ملاحظة: دخل Gemini Live صوت 16kHz، وخرجه 24kHz — السكربت بيسجّل 16 ويسمّع 24.
اضغط Ctrl+C للخروج.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import os
import queue
import sys
import time

IN_RATE = 16000   # مايك → السيرفر
OUT_RATE = 24000  # سمّاعة ← Gemini Live
BLOCK = 1600      # ~100ms عند 16kHz


def _build_hello() -> str:
    """frame المصافحة: HMAC لو المفتاح موجود، وإلا hello بسيط (يكفي بوضع dev المفتوح)."""
    device_id = os.getenv("SANDY_VOICE_DEVICE_ID", "laptop-test")
    ts = int(time.time() * 1000)
    key = os.getenv("SANDY_WS_HMAC_KEY", "").encode()
    legacy = os.getenv("ROBOT_WS_SECRET", "")
    if key:
        mac = hmac.new(key, f"{device_id}{ts}".encode(), hashlib.sha256).hexdigest()
        return json.dumps({"type": "hello", "device_id": device_id, "ts": ts, "hmac": mac})
    if legacy:
        return legacy
    return json.dumps({"type": "hello", "device_id": device_id, "ts": ts})


async def _run(url: str) -> None:
    try:
        import sounddevice as sd
        import websockets
    except ImportError as e:
        print(f"ناقص مكتبة: {e}\nنصّب: pip install websockets sounddevice numpy")
        sys.exit(1)

    mic_q: "queue.Queue[bytes]" = queue.Queue()
    spk_buf = bytearray()
    spk_lock = __import__("threading").Lock()
    hd = {"last_audio": 0.0}  # half-duplex: when we last got audio from Sandy

    def _mic_cb(indata, frames, t, status):  # noqa: ANN001
        mic_q.put(bytes(indata))

    def _spk_cb(outdata, frames, t, status):  # noqa: ANN001
        need = len(outdata)
        with spk_lock:
            have = min(need, len(spk_buf))
            outdata[:have] = spk_buf[:have]
            del spk_buf[:have]
        if have < need:
            outdata[have:] = b"\x00" * (need - have)

    print(f"🔌 بتصل بـ {url} ...")
    async with websockets.connect(url, max_size=None) as ws:
        await ws.send(_build_hello())
        # استنى auth_ok (أو تجاهل لو وضع مفتوح)
        try:
            reply = await asyncio.wait_for(ws.recv(), timeout=3)
            if isinstance(reply, str):
                msg = json.loads(reply) if reply.startswith("{") else {"type": reply}
                if msg.get("type") in ("error",):
                    print(f"❌ المصافحة فشلت: {msg}")
                    return
                print(f"✅ متّصل ({msg.get('type', reply)})")
        except asyncio.TimeoutError:
            print("✅ متّصل (وضع مفتوح، بدون auth)")

        in_stream = sd.RawInputStream(
            samplerate=IN_RATE, channels=1, dtype="int16",
            blocksize=BLOCK, callback=_mic_cb,
        )
        out_stream = sd.RawOutputStream(
            samplerate=OUT_RATE, channels=1, dtype="int16", callback=_spk_cb,
        )
        in_stream.start()
        out_stream.start()
        print("🎙️  احكي... (Ctrl+C للخروج)")

        loop = asyncio.get_event_loop()

        async def _send_mic() -> None:
            # Half-duplex: stop sending the mic while Sandy is talking, or the
            # speakers leak into the mic and she replies to her own voice. Use
            # headphones if you want full-duplex / barge-in. We still drain the
            # queue either way.
            while True:
                data = await loop.run_in_executor(None, mic_q.get)
                with spk_lock:
                    playing = len(spk_buf) > 0
                if playing or (time.time() - hd["last_audio"]) < 0.4:
                    continue  # she's speaking, so drop this mic frame
                await ws.send(data)

        async def _recv() -> None:
            async for msg in ws:
                if isinstance(msg, (bytes, bytearray)):
                    hd["last_audio"] = time.time()
                    with spk_lock:
                        spk_buf.extend(msg)
                else:
                    try:
                        evt = json.loads(msg)
                    except Exception:  # noqa: BLE001
                        continue
                    if evt.get("type") == "end_turn":
                        print("⏹️  انتهى دور ساندي")
                    elif evt.get("type") == "error":
                        print(f"⚠️  خطأ من السيرفر: {evt.get('msg')}")

        try:
            await asyncio.gather(_send_mic(), _recv())
        finally:
            in_stream.stop()
            in_stream.close()
            out_stream.stop()
            out_stream.close()


async def _enroll(url: str, clips: int, seconds: float) -> None:
    """يسجّل بصمة المالك من نفس مايك اللابتوب (يحلّ اختلاف القناة عن تيليجرام).

    يسجّل `clips` مقاطع، كل مقطع `seconds` ثانية، ويبعتها لمسار /voice/enroll.
    """
    try:
        import sounddevice as sd
        import websockets
    except ImportError as e:
        print(f"ناقص مكتبة: {e}\nنصّب: pip install websockets sounddevice numpy")
        sys.exit(1)

    enroll_url = url.rstrip("/")
    if enroll_url.endswith("/voice"):
        enroll_url = enroll_url[: -len("/voice")] + "/voice/enroll"
    elif not enroll_url.endswith("/voice/enroll"):
        enroll_url = enroll_url + "/voice/enroll"

    print(f"🔌 بتصل بـ {enroll_url} ...")
    async with websockets.connect(enroll_url, max_size=None) as ws:
        await ws.send(_build_hello())
        try:
            reply = await asyncio.wait_for(ws.recv(), timeout=3)
            if isinstance(reply, str) and reply.startswith("{"):
                msg = json.loads(reply)
                if msg.get("type") == "error":
                    print(f"❌ المصافحة فشلت: {msg}")
                    return
            print("✅ متّصل")
        except asyncio.TimeoutError:
            print("✅ متّصل (وضع مفتوح)")

        loop = asyncio.get_event_loop()

        def _record_clip() -> bytes:
            # نفس طريقة وضع المحادثة (RawInputStream) — أثبت من sd.rec على الماك.
            buf = bytearray()
            stream = sd.RawInputStream(
                samplerate=IN_RATE, channels=1, dtype="int16",
                blocksize=BLOCK, callback=lambda d, f, t, s: buf.extend(bytes(d)),
            )
            stream.start()
            time.sleep(seconds)
            stream.stop()
            stream.close()
            return bytes(buf)

        print(f"\n🎙️  رح نسجّل {clips} مقاطع، كل مقطع {seconds:.0f} ثواني. احكي جملة عادية كل مرة.\n")
        for i in range(1, clips + 1):
            input(f"  مقطع {i}/{clips} — اضغط Enter وابدا تحكي...")
            print("   🔴 بسجّل...", end="", flush=True)
            rec = await loop.run_in_executor(None, _record_clip)
            await ws.send(rec)
            await ws.send(json.dumps({"type": "utterance_end"}))
            try:
                ack = await asyncio.wait_for(ws.recv(), timeout=5)
                got = json.loads(ack).get("n", i) if ack.startswith("{") else i
                print(f" ✅ ({got})")
            except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                print(" ✅")

        await ws.send(json.dumps({"type": "enroll_done"}))
        try:
            res = await asyncio.wait_for(ws.recv(), timeout=30)
            data = json.loads(res)
            mark = "✅" if data.get("ok") else "❌"
            print(f"\n{mark} {data.get('msg', '')}")
        except Exception:  # noqa: BLE001
            print("\n⚠️  ما وصلتني نتيجة التسجيل")


def main() -> None:
    ap = argparse.ArgumentParser(description="Sandy laptop voice test client")
    ap.add_argument(
        "--url",
        default=os.getenv("SANDY_VOICE_WS_URL", ""),
        help="WebSocket URL, e.g. wss://your-app.herokuapp.com/voice",
    )
    ap.add_argument(
        "--enroll", action="store_true",
        help="سجّل بصمة صوتك من هذا المايك (بدل وضع المحادثة)",
    )
    ap.add_argument("--clips", type=int, default=5, help="عدد مقاطع التسجيل (افتراضي 5)")
    ap.add_argument("--seconds", type=float, default=4.0, help="طول كل مقطع بالثواني (افتراضي 4)")
    args = ap.parse_args()
    if not args.url:
        print("لازم --url أو SANDY_VOICE_WS_URL (wss://<app>.herokuapp.com/voice)")
        sys.exit(1)
    try:
        if args.enroll:
            asyncio.run(_enroll(args.url, args.clips, args.seconds))
        else:
            asyncio.run(_run(args.url))
    except KeyboardInterrupt:
        print("\n👋 خروج")


if __name__ == "__main__":
    main()
