#!/usr/bin/env python3
"""Check your LLM API keys and list usable models. Stdlib only. Run: python scripts/check_llm.py
Never prints your keys."""
import json, os, urllib.request, urllib.error

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def call(url, headers=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json", "User-Agent": "GraphOneTrialBot/0.1", **(headers or {})}
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=h), timeout=40) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]
    except Exception as e:
        return 0, repr(e)


def main():
    g = os.getenv("GEMINI_API_KEY")
    print("== Gemini:", "key set" if g else "NO KEY")
    if g:
        st, d = call("https://generativelanguage.googleapis.com/v1beta/models?pageSize=100", {"x-goog-api-key": g})
        if isinstance(d, dict):
            names = [m["name"].split("/")[-1] for m in d.get("models", [])
                     if "generateContent" in m.get("supportedGenerationMethods", [])]
            print("  models supporting generateContent (flash/lite shown first):")
            print("  ", [n for n in names if "flash" in n][:15])
        else:
            print("  list failed:", st, d)
        model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        st, d = call(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                     {"x-goog-api-key": g},
                     {"contents": [{"role": "user", "parts": [{"text": 'Reply with JSON {"ok": true}'}]}],
                      "generationConfig": {"responseMimeType": "application/json", "temperature": 0}})
        print(f"  test call with GEMINI_MODEL={model}: HTTP {st}",
              (d["candidates"][0]["content"]["parts"][0]["text"][:80] if isinstance(d, dict) and "candidates" in d else d))
    q = os.getenv("GROQ_API_KEY")
    print("== Groq:", "key set" if q else "NO KEY")
    if q:
        st, d = call("https://api.groq.com/openai/v1/models", {"Authorization": f"Bearer {q}"})
        if isinstance(d, dict):
            print("  models:", [m["id"] for m in d.get("data", [])][:25])
        else:
            print("  list failed:", st, d)
        model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
        st, d = call("https://api.groq.com/openai/v1/chat/completions", {"Authorization": f"Bearer {q}"},
                     {"model": model, "temperature": 0, "response_format": {"type": "json_object"},
                      "messages": [{"role": "user", "content": 'Reply with JSON {"ok": true}'}]})
        print(f"  test call with GROQ_MODEL={model}: HTTP {st}",
              (d["choices"][0]["message"]["content"][:80] if isinstance(d, dict) and "choices" in d else d))
    k = os.getenv("DEEPSEEK_API_KEY")
    print("== DeepSeek:", "key set (optional tier)" if k else "no key (optional, skip)")
    print("\nSet GEMINI_MODEL / GROQ_MODEL in .env to a model id listed above if a test call failed.")


if __name__ == "__main__":
    main()
