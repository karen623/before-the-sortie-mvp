"""遊戲流程：地點、搜索、道具、指認犯人與結局。全部由程式規則決定，不交給 LLM。"""
from __future__ import annotations

import os

from .engine import GameState, append_log, load_json

DATA = "data"
OPENING_CLUE = ("C01", "被撬開的藥品箱，旁邊畫有耶格爾派的標記")
SCOUT_BONUS = 1   # 偵查兵多一次行動


def new_game(world: dict, player: dict, embedder=None, data_dir: str = DATA) -> GameState:
    state = GameState(world=world, player=player, embedder=embedder)
    for loc in load_json(os.path.join(data_dir, "locations.json")):
        state.locations[loc["id"]] = loc
    char_dir = os.path.join(data_dir, "characters")
    for name in sorted(os.listdir(char_dir)):
        if name.endswith(".json"):
            state.add_npc(load_json(os.path.join(char_dir, name)))
    state.endings = load_json(os.path.join(data_dir, "endings.json"))
    state.clues[OPENING_CLUE[0]] = OPENING_CLUE[1]
    if player.get("class") == "偵查":
        state.bonus_actions = SCOUT_BONUS
    return state


def npcs_at(state: GameState, loc_id: str) -> list[str]:
    """目前在這個地點、而且已經出現的角色。"""
    return [nid for nid, npc in state.npcs.items()
            if npc.card.get("location") == loc_id
            and (not npc.card.get("requires_flag") or npc.card["requires_flag"] in state.flags)]


def give_items(state: GameState, npc_id: str) -> list[str]:
    """開始對話時，如果玩家帶著對方想要的東西，就自動交給他。"""
    npc = state.npcs[npc_id]
    messages = []
    for item, effect in npc.card.get("item_reactions", {}).items():
        if item in state.items and item not in npc.items_received:
            state.items.remove(item)
            npc.items_received.add(item)
            npc.affinity = max(0, min(100, npc.affinity + effect.get("affinity", 0)))
            if effect.get("flag"):
                state.flags.add(effect["flag"])
            if effect.get("memory") and npc.memory:
                npc.memory.add(state.clock(), effect["memory"], 2)
            messages.append(f"{effect.get('message', f'你把{item}交給了對方。')}（好感度 +{effect.get('affinity', 0)}）")
            append_log(state, {"type": "item", "time": state.clock(), "npc": npc_id, "item": item})
    return messages


def _check(state: GameState, cond: dict) -> bool:
    if "class" in cond and state.player.get("class") != cond["class"]:
        return False
    if "searched" in cond and state.searches.get(cond["_loc"], 0) < cond["searched"]:
        return False
    if "clues_at_least" in cond:
        have = sum(1 for c in cond["of"] if c in state.clues)
        if have < cond["clues_at_least"]:
            return False
    return True


def _rule_met(state: GameState, rule: dict, loc_id: str) -> bool:
    conds = rule.get("if_any")
    if not conds:
        return True
    return any(_check(state, dict(c, _loc=loc_id)) for c in conds)


def search(state: GameState, loc_id: str) -> list[str]:
    """搜索一個地點，回傳這次的發現。搜索規則寫在 data/locations.json。"""
    loc = state.locations[loc_id]
    found = []
    for rule in loc.get("search", []):
        if rule.get("clue") and rule["clue"] in state.clues:
            continue
        if rule.get("item") and (rule["item"] in state.items or f"got:{rule['item']}" in state.flags):
            continue
        if rule.get("flag") and not rule.get("clue") and rule["flag"] in state.flags:
            continue
        if not _rule_met(state, rule, loc_id):
            continue
        if rule.get("clue"):
            state.clues[rule["clue"]] = rule["text"]
        if rule.get("item"):
            state.items.append(rule["item"])
            state.flags.add(f"got:{rule['item']}")
        if rule.get("flag"):
            state.flags.add(rule["flag"])
        found.append(rule)
    state.searches[loc_id] = state.searches.get(loc_id, 0) + 1
    append_log(state, {"type": "search", "time": state.clock(), "location": loc_id,
                       "found": [r.get("clue") or r.get("item") or r.get("flag") for r in found]})
    if not found:
        return [loc.get("nothing", "沒有新發現。")]
    return [(f"🔎 線索 {r['clue']}：" if r.get("clue") else "🎒 " if r.get("item") else "") + r["text"] for r in found]


def accuse(state: GameState, suspect_id: str, evidence: list[str]) -> dict:
    """指認犯人。正確且證據足夠 → 找回脊髓液；證據不足 → 被反駁；指錯人 → 內鬨結局。"""
    cfg = state.endings
    valid = [c for c in evidence if c in state.clues and c in cfg["evidence_clues"]]
    row = {"type": "accuse", "time": state.clock(), "suspect": suspect_id, "evidence": evidence, "valid": valid}
    if suspect_id != cfg["culprit"]:
        name = state.npcs[suspect_id].card["name"] if suspect_id in state.npcs else suspect_id
        state.ending = _ending(state, "infighting", suspect=name)
        row["result"] = "wrong"
        append_log(state, row)
        return {"status": "wrong", "text": state.ending["text"]}
    if len(valid) < cfg["min_evidence"]:
        row["result"] = "not_enough"
        append_log(state, row)
        return {"status": "not_enough", "text": cfg["not_enough"]}
    state.flags.update({"culprit_found", "fluid_found"})
    row["result"] = "correct"
    append_log(state, row)
    return {"status": "correct", "text": cfg["confession"]}


def fate_options(state: GameState) -> list[tuple[str, str]]:
    """找回脊髓液後的處置選項；知道韓吉的秘密（C08）才會想到要銷毀。"""
    options = [("inject", "照原計畫讓大家注射"), ("volunteers", "只留給自願的人")]
    if "C08" in state.clues:
        options.append(("destroy", "銷毀脊髓液"))
    return options


def decide_fate(state: GameState, choice: str) -> dict:
    if choice == "inject":
        state.ending = _ending(state, "controlled")
    elif choice == "destroy" and "C08" in state.clues:
        state.ending = _ending(state, "human")
    else:
        allies = [n.affinity for nid, n in state.npcs.items() if nid != state.endings["culprit"]]
        avg = sum(allies) / len(allies)
        if avg >= 55:
            text = "大家都信任你的判斷，只有少數知道風險的人自願注射，撐住了最危險的前線；其他人以凡人之身並肩作戰。代價很沉重，但兵團沒有失去自己。"
        else:
            text = "願意承擔風險的人很少，大家彼此還不夠信任。前線很快就被突破，你們帶著遺憾出擊了。"
        state.ending = _ending(state, "bitter", volunteer_text=text)
        state.ending["avg_affinity"] = round(avg, 1)
    append_log(state, {"type": "ending", "time": state.clock(), "choice": choice, **state.ending})
    return state.ending


def time_up(state: GameState) -> dict | None:
    """時間用完：沒找回脊髓液就是空手出擊；有找回就進入處置抉擇（回傳 None）。"""
    if "fluid_found" in state.flags:
        return None
    state.ending = _ending(state, "empty_handed")
    append_log(state, {"type": "ending", "time": state.clock(), "choice": "time_up", **state.ending})
    return state.ending


def _ending(state: GameState, key: str, **fmt) -> dict:
    e = state.endings["endings"][key]
    return {"key": key, "title": e["title"], "text": e["text"].format(**fmt)}
