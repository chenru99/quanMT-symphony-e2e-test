#!/usr/bin/env python3
"""
Real Codex agent for Quant-Symphony.
Priority: Anthropic Claude -> StepFun Step -> Local LLM -> Placeholder.
"""

import os
import sys
import json
from pathlib import Path
import requests

API_URL = "https://api.anthropic.com/v1/messages"

SYSTEM_PROMPT = """
You are an expert quantitative developer.
Your task: design a complete trading strategy based on the issue description.

Output format (strict):
1. Put the full strategy code inside a single ```python code block.
2. Include a `strategy()` function that returns a pandas DataFrame with columns: datetime, signal, price.
3. Keep it simple; use mock data generation if needed.
4. After the code block, write a short markdown summary.
"""


def fetch_issue_metadata():
    """Read issue metadata from environment injected by orchestrator."""
    return {
        "id": os.getenv("ISSUE_ID", ""),
        "identifier": os.getenv("ISSUE_IDENTIFIER", ""),
        "title": os.getenv("ISSUE_TITLE", ""),
        "url": os.getenv("ISSUE_URL", ""),
        "repo": os.getenv("GITHUB_REPO", ""),
    }


def generate_placeholder_strategy() -> str:
    """Create a simple dual moving average strategy."""
    return '''
import pandas as pd
import numpy as np

def strategy():
    """Dual MA crossover: buy when short MA crosses above long MA."""
    days = 200
    dates = pd.date_range(end=pd.Timestamp.today(), periods=days, freq="D")
    price = 100 + np.random.randn(days).cumsum()
    df = pd.DataFrame({"datetime": dates, "price": price})
    df["short_ma"] = df["price"].rolling(10).mean()
    df["long_ma"] = df["price"].rolling(30).mean()
    df["signal"] = 0
    df.loc[df["short_ma"] > df["long_ma"], "signal"] = 1
    df.loc[df["short_ma"] < df["long_ma"], "signal"] = -1
    return df
'''


def call_llm(user_prompt: str) -> str:
    """Call LLM with auto fallback: Anthropic -> StepFun -> local."""
    issue = fetch_issue_metadata()

    # 1) Anthropic Claude
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    if anthropic_key:
        try:
            print("[INFO] Using Anthropic Claude")
            headers = {
                "x-api-key": anthropic_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
                "dangerously-allow-browser": "true"
            }
            body = {
                "model": "claude-3-5-sonnet-20241022",
                "system": SYSTEM_PROMPT,
                "messages": [
                    {"role": "user", "content": f"Issue: {issue.get('title','')}\nDescription: {user_prompt}"}
                ],
                "max_tokens": 2000,
                "temperature": 0.2,
            }
            resp = requests.post(API_URL, headers=headers, json=body, timeout=60)
            resp.raise_for_status()
            data = resp.json()
            return data["content"][0]["text"]
        except Exception as e:
            print(f"[WARN] Anthropic call failed: {e}")
            # fall through

    # 2) StepFun Step model
    stepfun_key = os.getenv("STEPFUN_API_KEY")
    if stepfun_key:
        try:
            print("[INFO] Using StepFun Step-3.5-Flash")
            from openai import OpenAI
            client = OpenAI(api_key=stepfun_key, base_url="https://api.stepfun.com/v1")
            response = client.chat.completions.create(
                model="stepfun/step-3.5-flash",
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": f"Issue: {issue.get('title','')}\nDescription: {user_prompt}"}
                ],
                max_tokens=2000,
                temperature=0.2,
            )
            return response.choices[0].message.content
        except Exception as e:
            print(f"[WARN] StepFun call failed: {e}")
            # fall through

    # 3) Local LLM (OpenAI-compatible)
    print("[INFO] Using local LLM")
    base_url = os.getenv("OPENAI_BASE_URL", "http://localhost:11434/v1")
    model = os.getenv("OPENAI_MODEL", "ollama/minimax-m2.7:cloud")  # updated default
    from openai import OpenAI
    import time
    client = OpenAI(api_key="dummy", base_url=base_url)
    for attempt in range(3):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": f"Issue: {issue.get('title','')}\nDescription: {user_prompt}"}
                ],
                max_tokens=2000,
                temperature=0.2,
            )
            return response.choices[0].message.content
        except Exception as e:
            if attempt < 2 and "429" in str(e):
                print(f"[WARN] Rate limited, retrying in 5s...")
                time.sleep(5)
            else:
                raise
    return None


def extract_code_block(text: str) -> str:
    """Find the first ```python ... ``` code block and return its content."""
    start = text.find("```python")
    if start == -1:
        start = text.find("```")
        if start != -1:
            end = text.find("```", start + 3)
            if end != -1:
                return text[start+3:end].strip()
    else:
        end = text.find("```", start + 9)
        if end != -1:
            return text[start+9:end].strip()
    return ""


def main():
    # 1) Read prompt from stdin
    prompt = sys.stdin.read()

    # Workspace root is current directory
    ws = Path(".")

    try:
        # 2) Try to generate code via LLM (Claude or local)
        response = call_llm(prompt)
        code = extract_code_block(response)
        if not code:
            # Fallback if no code block detected
            code = generate_placeholder_strategy()
            summary = response
        else:
            summary = response
    except Exception as e:
        # 3) On any error, use placeholder
        code = generate_placeholder_strategy()
        summary = f"Fallback to placeholder due to: {e}"

    # 4) Write strategy.py
    (ws / "strategy.py").write_text(code, encoding="utf-8")

    # 5) Write output.txt for backward compatibility
    (ws / "output.txt").write_text(
        f"Agent completed.\nSummary:\n{summary[:2000]}",
        encoding="utf-8"
    )

    print("DONE")


if __name__ == "__main__":
    main()
