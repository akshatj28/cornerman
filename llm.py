"""OpenRouter client with tool calling. The model never does arithmetic."""
import json
import requests
import secrets_cm as creds
import tools

BASE = "https://openrouter.ai/api/v1"


def free_models():
    r = requests.get(BASE + "/models", timeout=30)
    r.raise_for_status()
    out = []
    for m in r.json()["data"]:
        p = m.get("pricing") or {}
        if str(p.get("prompt")) in ("0", "0.0"):
            if str(p.get("completion")) in ("0", "0.0"):
                out.append((m["id"], m.get("context_length") or 0))
    out.sort(key=lambda x: -x[1])
    return out


def _tool(name, desc, props, required):
    f = {}
    f["name"] = name
    f["description"] = desc
    f["parameters"] = {"type": "object", "properties": props, "required": required}
    return {"type": "function", "function": f}


def _str(d):
    return {"type": "string", "description": d}


def _num(d):
    return {"type": "number", "description": d}


TOOLS = [
    _tool("run_sql",
          "Run a read-only SELECT against the training database. "
          "Tables: workouts(id,title,start_time), sets(workout_id,exercise,"
          "set_index,kind,weight_kg,reps,duration_s), goals, coach_notes. "
          "Use for any question not covered by the trusted functions.",
          {"query": _str("A single SELECT statement")}, ["query"]),
    _tool("trajectory",
          "Trusted estimated-1RM trend for one exercise. Use this for strength "
          "progress, never compute it yourself.",
          {"exercise": _str("Exact exercise name")}, ["exercise"]),
    _tool("project_goal",
          "Trusted goal projection: required vs observed rate of progress.",
          {"exercise": _str("Exact exercise name"),
           "target_kg": _num("Target estimated 1RM in kg"),
           "deadline": _str("Deadline as YYYY-MM-DD")},
          ["exercise", "target_kg", "deadline"]),
    _tool("remember",
          "Save a durable note: goals, injuries, preferences, observations.",
          {"note": _str("What to remember")}, ["note"]),
    _tool("recall",
          "Read back previously saved notes.",
          {"limit": _num("How many notes")}, []),
]


def _dispatch(name, args, log):
    log.append((name, args))
    if name == "run_sql":
        q = args.get("query") or ""
        if not q.strip():
            return {"error": "No query provided."}
        return tools.run_sql(q)
    if name == "trajectory":
        return tools.trajectory(args.get("exercise", ""))
    if name == "project_goal":
        return tools.project_goal(args.get("exercise", ""),
                                  args.get("target_kg", 0),
                                  args.get("deadline", ""))
    if name == "remember":
        return tools.remember(args.get("note", ""))
    if name == "recall":
        return tools.recall(int(args.get("limit", 20)))
    return {"error": "unknown tool " + name}


def _post(body):
    h = {"Authorization": "Bearer " + creds.OPENROUTER_KEY,
         "Content-Type": "application/json"}
    r = requests.post(BASE + "/chat/completions", json=body, headers=h, timeout=180)
    if r.status_code != 200:
        raise RuntimeError("OpenRouter " + str(r.status_code) + ": " + r.text[:400])
    return r.json()


def ask(system, user, model=None, max_tokens=4000, max_rounds=8, verbose=False):
    model = model or creds.OPENROUTER_MODEL
    msgs = [{"role": "system", "content": system},
            {"role": "user", "content": user}]
    log = []
    for _ in range(max_rounds):
        body = {"model": model, "messages": msgs,
                "max_tokens": max_tokens, "tools": TOOLS}
        m = _post_safe(body)["choices"][0]["message"]
        msgs.append(m)
        calls = m.get("tool_calls") or []
        if not calls:
            return {"text": m.get("content") or "", "tool_log": log}
        for c in calls:
            fn = c["function"]
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            res = _dispatch(fn["name"], args, log)
            if verbose:
                print("  ->", fn["name"], args)
            msgs.append({"role": "tool", "tool_call_id": c["id"],
                         "content": json.dumps(res, default=str)[:6000]})
    return {"text": "(stopped: too many tool rounds)", "tool_log": log}




def _post_safe(body):
    h = {"Authorization": "Bearer " + creds.OPENROUTER_KEY,
         "Content-Type": "application/json"}
    r = requests.post(BASE + "/chat/completions", json=body, headers=h, timeout=180)
    try:
        data = r.json()
    except Exception:
        raise RuntimeError("Non-JSON " + str(r.status_code) + ": " + r.text[:300])
    if "choices" not in data:
        raise RuntimeError("No choices: " + json.dumps(data)[:400])
    return data


if __name__ == "__main__":
    import sys
    if "--models" in sys.argv:
        for mid, ctx in free_models()[:30]:
            print("  " + mid.ljust(56) + str(ctx))
    else:
        q = " ".join(sys.argv[1:]) or "How many workouts are in the database?"
        s = ("You are a strength coach with access to the athlete's training "
             "database. Use tools to get real numbers. Never guess a number. "
             "Answer in one or two short sentences.")
        out = ask(s, q, verbose=True)
        print()
        print(out["text"])
        print()
        print("Tools used:", [t[0] for t in out["tool_log"]])


def ask_simple(system, user, max_tokens=4000, verbose=False):
    chain = [creds.OPENROUTER_MODEL]
    chain = chain + list(getattr(creds, "OPENROUTER_FALLBACKS", []))
    for m in chain:
        if verbose:
            print("  [model] " + m)
        try:
            r = _one_shot(m, system, user, max_tokens)
            return {"text": r, "tool_log": []}
        except RuntimeError as e:
            if verbose:
                print("  [failed] " + str(e)[:140])
    return {"text": "All models failed.", "tool_log": []}


def _one_shot(model, system, user, max_tokens):
    msgs = [{"role": "system", "content": system},
            {"role": "user", "content": user}]
    body = {"model": model, "messages": msgs, "max_tokens": max_tokens}
    d = _post_safe(body)
    return d["choices"][0]["message"]["content"] or ""


def ask_persistent(system, user, max_tokens=4000, verbose=False, rounds=5):
    import time
    chain = [creds.OPENROUTER_MODEL]
    chain = chain + list(getattr(creds, "OPENROUTER_FALLBACKS", []))
    wait = 60
    for attempt in range(rounds):
        for m in chain:
            if verbose:
                print("  [model] " + m)
            try:
                r = _one_shot(m, system, user, max_tokens)
                if r.strip():
                    return {"text": r, "tool_log": []}
                if verbose:
                    print("  [empty reply]")
            except RuntimeError as e:
                if verbose:
                    print("  [failed] " + str(e)[:120])
        if attempt < rounds - 1:
            if verbose:
                print("  all models down, waiting " + str(wait) + "s")
            time.sleep(wait)
            wait = wait * 2
    return None
