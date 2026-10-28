"""Single OpenAI-compatible interface. Routes chat through the Hermes agent (AGENT_URL) if set, else the LLM directly."""
import json, os, re
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
_clients = {}


def _client(kind):
    if kind not in _clients:
        if kind == "agent":
            _clients[kind] = OpenAI(base_url=os.environ["AGENT_URL"], api_key=os.getenv("AGENT_API_KEY") or "none")
        else:
            _clients[kind] = OpenAI(base_url=os.getenv("LLM_BASE_URL"), api_key=os.getenv("LLM_API_KEY") or "none")
    return _clients[kind]


def chat(system, user, as_json=False):
    msgs = [{"role": "system", "content": "detailed thinking off\n" + system},  # Nemotron: disable reasoning trace
            {"role": "user", "content": user}]
    routes = (["agent"] if os.getenv("AGENT_URL") else []) + ["direct"]
    err = None
    for r in routes:
        try:
            out = _client(r).chat.completions.create(model=os.getenv("LLM_MODEL"), messages=msgs, temperature=0)
            text = re.sub(r"<think>.*?</think>", "", out.choices[0].message.content or "", flags=re.S).strip()
            return parse_json(text) if as_json else text
        except Exception as e:  # fall back from agent to direct LLM
            err = e
    raise RuntimeError(f"LLM call failed: {err}")


def parse_json(text):
    m = re.search(r"\{.*\}|\[.*\]", text, re.S)
    return json.loads(m.group(0) if m else text)


def embed(texts):
    out = []
    for i in range(0, len(texts), 64):
        r = _client("direct").embeddings.create(model=os.getenv("EMBED_MODEL"), input=texts[i:i + 64])
        out += [d.embedding for d in r.data]
    return out
