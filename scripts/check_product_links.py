import sqlite3
import json

conn = sqlite3.connect("data/pipeline.db")
c = conn.cursor()

print("=== SAMPLE STARTUP_META ===")
for r in c.execute("SELECT dedup_key, payload FROM aux WHERE kind='startup_meta' LIMIT 5").fetchall():
    print("key:", r[0], "payload:", r[1])

print("\n=== SAMPLE PRODUCTS ===")
for r in c.execute("SELECT source_url, payload FROM records WHERE record_type='PRODUCT' LIMIT 5").fetchall():
    p = json.loads(r[1])
    print("product source_url:", r[0])
    print("  source:", p.get("source"))
    print("  content:", p.get("content"))

print("\n=== HOW PRODUCTS ARE COLLECTED (from src/collectors/products.py) ===")
