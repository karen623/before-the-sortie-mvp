"""遊戲引擎（harness）：管時間、好感度、秘密門檻與線索。LLM 只負責說話。"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime

from .memory import MemoryStore, SimpleEmbedder
from .prompts import available_clues, build_system_prompt

START_MINUTE = 21 * 60          # 21:00
END_MINUTE = 29 * 60            # 隔天 05:00
ACTION_MINUTES = 30             # 每次行動消耗 30 分鐘
MAX_HISTORY = 6                 # 短期記憶：保留最近 6 則訊息（3 輪對話）
MEMORY_TOP_K = 3                # 長期記憶：每次撈出最相關的 3 則
NEGOTIATOR_BONUS = 1.5          # 交涉兵的好感度上升加成


def load_json(path: str):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@dataclass
class NPCState:
    card: dict
    affinity: int
    history: list = field(default_factory=list)
    secret_revealed: bool = False
    memory: MemoryStore | None = None
    items_received: set = field(default_factory=set)

    @property
    def secret_unlocked(self) -> bool:
        return bool(self.card.get("secret")) and self.affinity >= self.card.get("secret_threshold", 999)


@dataclass
class GameState:
    world: dict
    player: dict
    npcs: dict = field(default_factory=dict)
    minutes_used: int = 0
    clues: dict = field(default_factory=dict)
    log: list = field(default_factory=list)
    embedder: object = None
    log_path: str | None = None
    flags: set = field(default_factory=set)
    items: list = field(default_factory=list)
    searches: dict = field(default_factory=dict)
    locations: dict = field(default_factory=dict)
    bonus_actions: int = 0
    endings: dict = field(default_factory=dict)
    ending: dict | None = None

    @property
    def actions_left(self) -> int:
        total = END_MINUTE - START_MINUTE + self.bonus_actions * ACTION_MINUTES
        return (total - self.minutes_used) // ACTION_MINUTES

    def clock(self) -> str:
        m = (START_MINUTE + self.minutes_used) % (24 * 60)
        return f"{m // 60:02d}:{m % 60:02d}"

    def add_npc(self, card: dict):
        bonus = card.get("origin_modifiers", {}).get(self.player["origin"], 0)
        affinity = max(0, min(100, card["base_affinity"] + bonus))
        if self.embedder is None:
            self.embedder = SimpleEmbedder()
        self.npcs[card["id"]] = NPCState(card=card, affinity=affinity, memory=MemoryStore(self.embedder))

    def spend_action(self) -> bool:
        """開始一次行動。時間不夠就回傳 False。"""
        if self.actions_left <= 0:
            return False
        self.minutes_used += ACTION_MINUTES
        return True


def apply_affinity(state: GameState, npc: NPCState, change: int) -> int:
    if change > 0 and state.player.get("class") == "交涉":
        change = round(change * NEGOTIATOR_BONUS)
    npc.affinity = max(0, min(100, npc.affinity + change))
    return change


def talk(state: GameState, llm, npc_id: str, text: str, tone: str | None = None) -> dict:
    """處理玩家對某個角色說的一句話，回傳這一輪的結果。"""
    npc = state.npcs[npc_id]
    card = npc.card
    tone = tone or state.player.get("personality", "冷靜")
    unlocked = npc.secret_unlocked
    before = npc.affinity
    location = state.locations.get(card.get("location"), {}).get("name")

    recalled = npc.memory.search(text, k=MEMORY_TOP_K) if npc.memory else []
    system = build_system_prompt(state.world, card, state.player, npc.affinity, unlocked,
                                 clock=state.clock(), memories=[m for m, _ in recalled],
                                 flags=state.flags, player_clues=state.clues, location_name=location)
    user_msg = f"〔語氣：{tone}〕{text}"
    messages = [{"role": "system", "content": system}] + npc.history[-MAX_HISTORY:] + [
        {"role": "user", "content": user_msg}]

    data, raw, info = llm.chat_json(messages)
    if data is None:  # 重試後仍失敗：用預設回應，遊戲不中斷
        data = {"reply": f"（{card['name']}似乎在想別的事，沒有回答。）", "emotion": "平靜",
                "affinity_change": 0, "revealed_secret": False, "memory": "", "importance": 1, "clues": []}
        info["fallback"] = True

    applied = apply_affinity(state, npc, data["affinity_change"])

    # 線索只在這個角色「現在」被允許說的範圍內登記，模型亂填的編號會被忽略
    new_clues = []
    allowed = {c["id"]: c["text"] for c in available_clues(card, before, state.flags)}
    for cid in data["clues"]:
        if cid in allowed and cid not in state.clues:
            state.clues[cid] = allowed[cid]
            new_clues.append(cid)

    secret_told = data["revealed_secret"] and unlocked and not npc.secret_revealed
    new_clue = None
    if secret_told:
        npc.secret_revealed = True
        clue = card.get("secret_clue")
        if clue and clue["id"] not in state.clues:
            state.clues[clue["id"]] = clue["text"]
            new_clue = clue
            new_clues.append(clue["id"])

    # 防爆雷檢查：秘密還沒解鎖，回覆卻出現秘密的關鍵字
    leak = (not unlocked) and any(k in data["reply"] for k in card.get("secret_keywords", []))

    if npc.memory and data["memory"]:
        npc.memory.add(state.clock(), data["memory"], data["importance"])
    if secret_told and npc.memory:
        npc.memory.add(state.clock(), f"我把心裡的秘密告訴了{state.player['name']}", 3)

    npc.history += [{"role": "user", "content": user_msg},
                    {"role": "assistant", "content": data["reply"]}]

    result = {
        "type": "talk", "time": state.clock(), "npc": npc_id, "player_text": text, "tone": tone,
        "reply": data["reply"], "emotion": data["emotion"],
        "affinity_before": before, "affinity_after": npc.affinity, "affinity_change": applied,
        "secret_unlocked": unlocked, "revealed_secret": data["revealed_secret"],
        "new_clue": new_clue["id"] if new_clue else None, "new_clues": new_clues, "leak_flag": leak,
        "memory_saved": data["memory"], "importance": data["importance"],
        "recalled": [{"time": m.time, "text": m.text, "score": sc} for m, sc in recalled],
        "embedder": getattr(state.embedder, "name", None),
        "model": llm.config.model, "temperature": llm.config.temperature,
        "raw": raw, **info,
    }
    state.log.append(result)
    append_log(state, result)
    return result


def append_log(state: GameState, row: dict, folder: str = "logs"):
    """每發生一件事就立刻寫進紀錄檔，程式中途閃退或關掉視窗也不會遺失。"""
    if state.log_path is None:
        os.makedirs(folder, exist_ok=True)
        state.log_path = os.path.join(folder, f"session_{datetime.now():%Y%m%d_%H%M%S}.jsonl")
    with open(state.log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def save_log(state: GameState) -> str | None:
    """紀錄已經逐筆寫入，這裡只回傳檔案位置。"""
    return state.log_path
