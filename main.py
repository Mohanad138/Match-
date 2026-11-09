# -*- coding: utf-8 -*-
import os
import re
import json
import time
import asyncio
import subprocess
import difflib
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import (MessageMediaPhoto, MessageMediaDocument, MessageMediaWebPage)
import requests

TG_API_ID = int(os.getenv("TG_API_ID"))
TG_API_HASH = os.getenv("TG_API_HASH")
TG_SESSION = os.getenv("TG_SESSION")
GEMINI_KEY_MATCH = os.getenv("GEMINI_KEY_MATCH")

GEMINI_MODEL = "gemini-flash-lite-latest"
STATE_FILE = "relay_state.json"
os.makedirs("downloads", exist_ok=True)

CHANNELS = {
    -1009917651232: {
        "name": "بثوث المباريات",
        "target": "@FabriAr2",
        "source_username": "@Arena8x", # 👈 تم التغيير إلى اليوزر القديم للتجربة
    }
}

client = TelegramClient(StringSession(TG_SESSION), TG_API_ID, TG_API_HASH)

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f: return json.load(f)
        except Exception: return {}
    return {}

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f: json.dump(state, f, ensure_ascii=False, indent=2)

state = load_state()

def is_duplicate_content(source_id, text):
    if not text or not text.strip(): return False
    key = str(source_id)
    recent = state.setdefault("recent_texts", {}).get(key, [])
    norm = re.sub(r'\s+', ' ', text).strip().lower()
    for prev in recent:
        if difflib.SequenceMatcher(None, norm, prev).ratio() >= 0.85: return True
    return False

def record_published_text(source_id, text):
    if not text or not text.strip(): return
    key = str(source_id)
    norm = re.sub(r'\s+', ' ', text).strip().lower()
    bucket = state.setdefault("recent_texts", {}).setdefault(key, [])
    bucket.append(norm)
    state["recent_texts"][key] = bucket[-15:]
    save_state(state)

def commit_state():
    try:
        subprocess.run(["git", "add", STATE_FILE], check=False)
        subprocess.run(["git", "commit", "-m", "update state"], check=False, capture_output=True)
        subprocess.run(["git", "pull", "--rebase", "origin", "main"], check=False, capture_output=True)
        subprocess.run(["git", "push"], check=False, capture_output=True)
    except Exception: pass

def call_gemini(prompt, api_key, max_retries=3):
    if not api_key: raise RuntimeError("no gemini key")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    body = {"contents": [{"parts": [{"text": prompt}]}]}
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.post(url, json=body, headers=headers, timeout=30)
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < max_retries:
                time.sleep(2 ** attempt); continue
            resp.raise_for_status()
            raw = resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            if raw.startswith("```"):
                raw = raw.strip("`").strip()
                if raw.lower().startswith("json"): raw = raw[4:].strip()
            return json.loads(raw)
        except Exception as e:
            if attempt == max_retries: raise e
            time.sleep(2 ** attempt)

def call_match_links(text, api_key):
    prompt = f"""أنت تنسّق منشورات قناة تبث روابط مشاهدة مباريات كرة القدم.
المطلوب: أعد ترتيب النص التالي وحسّنه ليكون جاهزاً للنشر، مع تنفيذ الشروط التالية:
1. احذف أي "تمديد" أو تكرار حروف (اجعل "ليــ ــفــ ــر بــ ــو ل" تصبح "ليفربول").
2. ضع أسماء الفرق بالشكل التالي: "🏴󠁧󠁢󠁥󠁮󠁧󠁿 اسم_الفريق 🆚 اسم_الفريق 🏴󠁧󠁢󠁥󠁮󠁧󠁿" (بدون تمديد).
3. سطر الوقت: "⏰ | 05:00 مساء 🇸🇦 / 04:00 مساء 🇪🇬".
4. سطر المعلق: "🎤 | اسم المعلق".
5. ابدأ المنشور بعبارة: "تــ تــ ـــا بـــ ــع 🔴🔴 👇🏼" (كما هي بالتمديد).
6. **احذف نهائياً**: الرابط الثالث (يوتيوب)، وإعلان المتجر (tvip-live)، والتحذير الأخير (🚨 لتجربة أفضل...).
7. **احتفظ فقط** بالرابط الأول (تيليجرام) والرابط الثاني (الموقع) مع العبارات التي تسبقهما.
8. اجعل المسافات بين المباريات والروابط طبيعية جداً (سطر فاضي واحد بين كل فقرة).

النص الأصلي المطلوب تنسيقه:
{text}

أعد النتيجة بصيغة JSON فقط: {{"text": "النص الجديد المنسق"}}"""
    try:
        result = call_gemini(prompt, api_key)
        return (result.get("text") or "").strip()
    except Exception as e:
        print(f"❌ خطأ التنسيق: {e}")
        return ""

async def process_batch(source_id, messages):
    config = CHANNELS.get(source_id)
    if not config: return

    raw_text = next((m.message for m in messages if m.message), "")
    
    # إذا كان المنشور لا يحتوي على نص ولا صورة، تجاهله
    if not raw_text and not any(msg.media for msg in messages): 
        return

    # إذا كان هناك نص، قم بتنسيقه
    final_text = ""
    if raw_text:
        if is_duplicate_content(source_id, raw_text): 
            print("⏭️ تجاهل: منشور مكرر")
            return
        final_text = call_match_links(raw_text, GEMINI_KEY_MATCH)
        if final_text:
            record_published_text(source_id, raw_text)
        else:
            final_text = raw_text # إذا فشل التنسيق، انشر النص الأصلي

    media_msgs = [m for m in messages if m.media and not m.video]

    try:
        # إذا كان هناك صورة أو فيديو، انشره مع النص
        if media_msgs:
            to_send = media_msgs[0].photo if isinstance(media_msgs[0].media, MessageMediaWebPage) else media_msgs[0].media
            if to_send:
                await client.send_file(config["target"], to_send, caption=final_text or None, parse_mode="html")
                print(f"✅ نُشر (وسائط + نص) — {config['name']}")
                return
        
        # إذا كان نصاً فقط، انشر النص
        if final_text:
            await client.send_message(config["target"], final_text, parse_mode="html", link_preview=False)
            print(f"✅ نُشر (نص فقط) — {config['name']}")
            
    except Exception as e:
        print(f"❌ خطأ أثناء النشر: {e}")

async def catch_up_channel(source_id):
    config = CHANNELS.get(source_id, {})
    entity_ref = config.get("source_username") or source_id
    key = str(source_id)
    last_id = state.get(key, 0)

    if last_id == 0:
        latest = await client.get_messages(entity_ref, limit=1)
        if latest: last_id = max(latest[0].id - 5, 0)
        state[key] = last_id
        save_state(state)

    messages = await client.get_messages(entity_ref, min_id=last_id, limit=100)
    if not messages: return

    messages = list(reversed(messages))
    processed_ids = set()

    for msg in messages:
        if msg.id in processed_ids: continue
        if msg.grouped_id:
            group = [m for m in messages if m.grouped_id == msg.grouped_id and m.id not in processed_ids]
            group.sort(key=lambda m: m.id)
            await process_batch(source_id, group)
            processed_ids.update(m.id for m in group)
            new_last = group[-1].id
        else:
            await process_batch(source_id, [msg])
            processed_ids.add(msg.id)
            new_last = msg.id
        state[key] = new_last
        save_state(state)

async def main():
    await client.start()
    print("🚀 بدء فحص بثوث المباريات...")
    await catch_up_channel(-1009917651232)
    await client.disconnect()
    commit_state()
    print("✅ انتهت التشغيلة.")

if __name__ == "__main__":
    asyncio.run(main())
