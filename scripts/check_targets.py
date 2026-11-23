import sqlite3
import json

conn = sqlite3.connect("data/pipeline.db")
names = [r[0] for r in conn.execute("SELECT json_extract(payload, '$.content.entityName') FROM records WHERE record_type='STARTUP'").fetchall() if r[0]]

targets = ["anthropic", "openai", "scale", "databricks", "cohere", "perplexity", "mistral", "hugging", "palantir", "glean", "celonis", "gitlab", "jasper", "copy.ai", "runway", "stability", "midjourney", "adept", "character", "eleven", "inflection", "synthesia", "replicate", "deepgram", "assemblyai", "together", "anyscale", "modal", "langchain", "llamaindex", "weights", "pinecone", "weaviate", "qdrant", "chroma", "cursor", "cognition", "poolside", "harvey", "glean", "writer", "cresta", "tome", "gamma", "pika", "suno", "udio", "baseten", "octoai", "predibase", "cohere", "voyage"]

found = {}
for target in targets:
    matches = [n for n in names if target in n.lower()]
    if matches:
        found[target] = matches[:5]

print("Found AI targets in STARTUP:")
for k, v in found.items():
    print(f"  {k}: {v}")
