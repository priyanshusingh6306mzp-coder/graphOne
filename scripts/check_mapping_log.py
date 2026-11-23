import sqlite3
import json

conn = sqlite3.connect("data/pipeline.db")
c = conn.cursor()

# Check raw_name frequencies from mapping_log and records
rows = c.execute("SELECT raw_name, count(*) FROM mapping_log GROUP BY raw_name ORDER BY count(*) DESC LIMIT 50").fetchall()
print("Top 30 raw_names in mapping_log:")
for r in rows[:30]:
    print(f"  {r[0]}: {r[1]}")
