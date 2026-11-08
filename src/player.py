"""玩家角色建立：選項、輸入檢查、存檔。"""
from __future__ import annotations

import json
import os
import re

ORIGINS = ["帕拉迪島", "馬萊", "希茲尼"]
CLASSES = {
    "偵查": "多一次行動機會",
    "醫療": "調查醫療帳篷時，能直接看出顏料是馬萊軍用油漆",
    "技術": "能直接檢查補給船貨艙",
    "交涉": "每次對話的好感度上升量 +50%",
}
PERSONALITIES = ["冷靜", "熱血", "溫柔", "毒舌"]
PRONOUNS = ["他", "她", "祂"]
SAVE_PATH = "saves/player.json"


def sanitize(text: str, limit: int) -> str:
    """玩家自填內容會被放進 prompt：限制長度，並去掉可能用來改寫 prompt 結構的符號。"""
    text = re.sub(r"[\r\n\t]+", " ", text)
    text = re.sub(r"[{}<>【】〔〕\[\]#`]", "", text)
    return text.strip()[:limit]


def choose(title: str, options: list[str], default: int = 0) -> str:
    print(f"\n{title}")
    for i, opt in enumerate(options, 1):
        print(f"  {i}. {opt}")
    raw = input(f"請輸入編號（直接按 Enter = {options[default]}）> ").strip()
    if raw.isdigit() and 1 <= int(raw) <= len(options):
        return options[int(raw) - 1]
    return options[default]


def create_player() -> dict:
    print("\n=== 建立你的志願兵 ===")
    name = ""
    while not name:
        name = sanitize(input("姓名（10 字以內）> "), 10)
    pronoun = choose("稱呼方式", PRONOUNS)
    appearance = sanitize(input("\n外觀描述（之後用來生成頭像，可留空）> "), 80)
    origin = choose("出身（會影響每個角色一開始對你的態度）", ORIGINS)
    cls = choose("兵科", [f"{k}：{v}" for k, v in CLASSES.items()]).split("：")[0]
    personality = choose("性格（決定你預設的說話語氣）", PERSONALITIES)
    reason = sanitize(input("\n加入兵團的理由（一句話，30 字以內，可留空）> "), 30) or "想守護重要的人"
    return {"name": name, "pronoun": pronoun, "appearance": appearance, "origin": origin,
            "class": cls, "personality": personality, "reason": reason}


def save_player(player: dict, path: str = SAVE_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(player, f, ensure_ascii=False, indent=2)


def load_saved_player(path: str = SAVE_PATH) -> dict | None:
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def describe(player: dict) -> str:
    return (f"{player['name']}（{player['pronoun']}）｜出身 {player['origin']}｜兵科 {player['class']}"
            f"｜性格 {player['personality']}｜理由：{player['reason']}")
