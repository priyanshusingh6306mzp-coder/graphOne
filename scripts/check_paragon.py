import sqlite3, json
conn = sqlite3.connect("data/pipeline.db")
p = conn.execute("SELECT payload FROM records WHERE record_type='STARTUP' AND json_extract(payload, '$.content.entityName')='Parsagon'").fetchall()
if p:
    print("Found Parsagon:", json.loads(p[0][0])["content"])
p2 = conn.execute("SELECT payload FROM records WHERE record_type='STARTUP' AND json_extract(payload, '$.content.entityName')='Paragon'").fetchall()
if p2:
    print("Found Paragon:", json.loads(p2[0][0])["content"])
