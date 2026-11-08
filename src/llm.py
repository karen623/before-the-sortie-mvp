"""呼叫 LLM，並確保拿回格式正確的 JSON。"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass

EMOTIONS = ["開心", "興奮", "平靜", "擔憂", "不悅", "悲傷", "警戒"]


@dataclass
class LLMConfig:
    model: str = "openai/gpt-oss-120b"
    base_url: str = "https://api.groq.com/openai/v1"
    api_key: str = ""
    temperature: float = 0.8
    mock: bool = False


def load_config() -> LLMConfig:
    """從 .env 讀取設定。"""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    return LLMConfig(
        model=os.getenv("MODEL", "openai/gpt-oss-120b"),
        base_url=os.getenv("BASE_URL", "https://api.groq.com/openai/v1"),
        api_key=os.getenv("GROQ_API_KEY", ""),
        temperature=float(os.getenv("TEMPERATURE", "0.8")),
        mock=os.getenv("MOCK", "0") == "1",
    )


def extract_json(text: str):
    """從模型輸出中找出 JSON；找不到就回傳 None。"""
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return None


def validate(data) -> dict | None:
    """檢查欄位並修正成遊戲引擎能用的格式。"""
    if not isinstance(data, dict) or not str(data.get("reply", "")).strip():
        return None
    try:
        change = int(data.get("affinity_change", 0))
    except (TypeError, ValueError):
        change = 0
    emotion = data.get("emotion", "平靜")
    try:
        importance = int(data.get("importance", 1))
    except (TypeError, ValueError):
        importance = 1
    return {
        "reply": str(data["reply"]).strip(),
        "emotion": emotion if emotion in EMOTIONS else "平靜",
        "affinity_change": max(-5, min(5, change)),
        "revealed_secret": bool(data.get("revealed_secret", False)),
        "memory": str(data.get("memory") or "").strip()[:80],
        "importance": max(1, min(3, importance)),
        "clues": [str(x) for x in data.get("clues", []) if isinstance(x, (str, int))]
        if isinstance(data.get("clues"), list) else [],
    }


class LLMClient:
    def __init__(self, config: LLMConfig):
        self.config = config
        self.client = None
        self.use_json_mode = True
        self.use_low_reasoning = "gpt-oss" in config.model
        if not config.mock:
            if not config.api_key or "請貼上" in config.api_key:
                raise RuntimeError("找不到 GROQ_API_KEY。請先建立 .env 檔並填入金鑰，或把 MOCK 設成 1。")
            from openai import OpenAI
            self.client = OpenAI(base_url=config.base_url, api_key=config.api_key)

    def _request(self, messages):
        kwargs = {"model": self.config.model, "messages": messages,
                  "temperature": self.config.temperature}
        if self.use_json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if self.use_low_reasoning:
            kwargs["extra_body"] = {"reasoning_effort": "low"}
        try:
            resp = self.client.chat.completions.create(**kwargs)
        except Exception as e:  # 有些模型不支援某些參數：拿掉後重試一次
            msg = str(e).lower()
            if self.use_low_reasoning and "reasoning" in msg:
                self.use_low_reasoning = False
                return self._request(messages)
            if self.use_json_mode and ("response_format" in msg or "json" in msg):
                self.use_json_mode = False
                return self._request(messages)
            raise
        return resp.choices[0].message.content or ""

    def chat_json(self, messages, retries: int = 2):
        """回傳 (整理好的結果, 原始輸出, 統計資訊)。失敗時結果為 None。"""
        raw, start = "", time.time()
        for attempt in range(retries + 1):
            raw = mock_reply(messages) if self.config.mock else self._request(messages)
            data = validate(extract_json(raw))
            if data:
                return data, raw, {"attempts": attempt + 1, "latency": round(time.time() - start, 2)}
            messages = messages + [
                {"role": "assistant", "content": raw},
                {"role": "user", "content": "你的上一則回覆不是正確的 JSON。請只輸出規定格式的 JSON 物件，不要有其他文字。"},
            ]
        return None, raw, {"attempts": retries + 1, "latency": round(time.time() - start, 2)}


def mock_reply(messages) -> str:
    """不連網的假 AI，用來測試遊戲流程：會依關鍵字加減好感、說出被允許的線索與秘密。"""
    system = messages[0]["content"]
    user = messages[-1]["content"]
    text = user.split("〕", 1)[-1]
    change = 0
    if any(w in text for w in ["謝謝", "辛苦", "幫", "相信", "擔心你", "沒事吧"]):
        change = 3
    if any(w in text for w in ["笨", "閉嘴", "快說", "是不是你", "騙"]):
        change = -4
    if "【你的秘密】" in system and any(w in text for w in ["筆記", "隱瞞", "秘密", "猶豫", "煩惱"]):
        return json.dumps({"reply": "……好吧，我只跟你說。", "emotion": "擔憂", "affinity_change": 1,
                           "revealed_secret": True, "memory": "我把秘密告訴了對方", "importance": 3,
                           "clues": []}, ensure_ascii=False)
    section = re.search(r"【你可以透露的線索】（被問到.*?\n((?:- .*\n?)+)", system)
    if section and any(w in text for w in ["線索", "看到", "聽到", "知道", "會議", "開會", "箱子", "奇怪"]):
        cid = re.findall(r"- (C\d+)：", section.group(1))[0]
        return json.dumps({"reply": f"（模擬回應）好吧，我告訴你 {cid} 的事。", "emotion": "平靜",
                           "affinity_change": 1, "revealed_secret": False,
                           "memory": f"我把線索 {cid} 告訴了對方", "importance": 2, "clues": [cid]},
                          ensure_ascii=False)
    return json.dumps({"reply": "（模擬回應）嗯？你想問什麼都可以喔！",
                       "emotion": "開心" if change >= 0 else "不悅",
                       "affinity_change": change, "revealed_secret": False,
                       "memory": f"對方說：{text}" if len(text) > 6 else "", "importance": 1, "clues": []},
                      ensure_ascii=False)
