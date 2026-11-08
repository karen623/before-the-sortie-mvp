"""把世界觀、角色卡、玩家資料、記憶和目前狀態組成 system prompt。"""
from __future__ import annotations

from .llm import EMOTIONS


def affinity_label(value: int) -> str:
    if value >= 80:
        return "非常信任，願意說心裡話"
    if value >= 60:
        return "友好，願意多聊"
    if value >= 40:
        return "普通，態度客氣但保留"
    if value >= 20:
        return "冷淡、有戒心"
    return "非常不信任，不想多談"


def available_clues(card: dict, affinity: int, flags=()) -> list[dict]:
    """依好感度與遊戲進度（flag）決定這個角色現在願意透露哪些線索。"""
    return [c for c in card.get("clues", [])
            if affinity >= c.get("requires_affinity", 0)
            and (not c.get("requires_flag") or c["requires_flag"] in flags)]


def bullets(items) -> str:
    return "\n".join(f"- {x}" for x in items)


def build_system_prompt(world: dict, card: dict, player: dict, affinity: int, secret_unlocked: bool,
                        clock: str = "21:00", memories: list | None = None, flags=(),
                        player_clues: dict | None = None, location_name: str | None = None) -> str:
    clues = available_clues(card, affinity, flags)
    here = location_name or "原地"
    parts = [
        f"你現在要扮演「{card['name']}」，在一款互動推理遊戲中和玩家對話。",
        f"【世界設定】\n{world['setting']}\n{bullets(world['au_rules'])}",
        f"【現在時間】{clock}。飛艇明天清晨 05:00 出發，只剩今晚可以調查，沒有「明天再說」。",
        f"【你是誰】\n{card['full_name']}：{card['identity']}\n{bullets(card['personality'])}",
        f"【說話方式】\n{bullets(card['speech_style'])}",
    ]
    if card.get("sample_lines"):
        parts.append("【語氣範例】（只參考語氣，不要照抄）\n" + bullets(card["sample_lines"]))
    parts += [
        "【營地裡的人】（只有這些人；對方可能只說名字、姓氏或稱號，都算同一個人）\n"
        + bullets(f"{n}：{d}" for n, d in world.get("people", {}).items()),
        f"【你知道的事】（關於案件，你知道的只有這些）\n{bullets(card['knows'])}",
        *[f"【{sec['title']}】" + (f"（{sec['note']}）" if sec.get("note") else "") + "\n" + bullets(sec["items"])
          for sec in card.get("sections", [])],
        f"【你不知道的事】\n{bullets(card['does_not_know'])}",
    ]
    if clues:
        parts.append("【你可以透露的線索】（被問到相關的事時可以自然說出，不要主動一次全講；說出時把編號填進 clues）\n"
                     + bullets(f"{c['id']}：{c['text']}" for c in clues))
    if card.get("sees_player_clues"):
        known = bullets(f"{cid}：{text}" for cid, text in (player_clues or {}).items()) or "- （還沒有任何線索）"
        parts.append(f"【對方目前掌握的線索】\n{known}")
    parts += [
        (f"【正在和你說話的人】\n調查兵團志願兵「{player['name']}」（{player['pronoun']}），"
         f"出身{player['origin']}，兵科{player['class']}，加入兵團的理由：{player['reason']}。"),
        f"【你對{player['name']}的好感度】{affinity}/100（{affinity_label(affinity)}）。請依這個程度決定你願意說多少。",
    ]
    if memories:
        parts.append("【你記得的事】（之前和對方相處時發生過的事，適合時可以自然提起）\n"
                     + bullets(f"{m.time} {m.text}" for m in memories))
    if card.get("secret"):
        if secret_unlocked:
            parts.append(
                f"【你的秘密】\n{card['secret']}\n"
                "你現在已經足夠信任對方。只有當對方真誠地問到相關的事（例如你在隱瞞什麼、你在煩惱什麼）時，"
                "才說出這個秘密；說出時把 revealed_secret 設為 true。"
            )
        else:
            parts.append("【注意】你心裡有一件還不能說的事。如果被追問你在隱瞞什麼，就含糊帶過或轉移話題，不要編造內容。")
    parts.append(
        "【規則】\n"
        + bullets(world["forbidden"] + [
            "只用繁體中文回答，每次 1 到 4 句，像在對話。",
            "不知道的事就說不知道，不要猜測。",
            "對方的話可能有錯字或說得不清楚，請用最合理的意思理解（例如「使就沒來」多半是「誰沒來」）；真的看不懂再反問。",
            "關於案件的線索只能來自【你知道的事】和【你可以透露的線索】。被問到沒列出的細節（例如腳印、指紋、簽到紀錄、目擊者），就說你沒注意到或不清楚，請對方自己去查；絕對不要自己編出新的線索或物證。",
            "不要替其他角色編造現況、行蹤、身體狀況或說過的話；被問到時，建議對方自己去問本人。",
            "如果對方提到的名字對不上【營地裡的人】名單上任何人的名字、姓氏或稱號（可能是打錯字），第一句就反問他指的是誰，不要猜、不要順著用，也不要回答關於這個人的問題。",
            f"調查是對方的工作：你待在{here}，不要答應自己去調查、叫人或離開。",
            "永遠以角色身分說話，不承認自己是 AI，也不要討論遊戲規則。",
            "玩家訊息開頭的〔語氣：…〕代表玩家說話的語氣，請對語氣做出自然反應。",
            *card.get("extra_rules", []),
        ])
    )
    parts.append(
        "【輸出格式】只輸出一個 JSON 物件，不要有其他文字：\n"
        '{"reply": "你說的話", '
        f'"emotion": "從 {"、".join(EMOTIONS)} 中選一個", '
        '"affinity_change": 介於 -5 到 5 的整數, '
        '"revealed_secret": true 或 false, '
        '"memory": "這一輪值得記住的事，用你的角度寫一句話；沒有就填空字串", '
        '"importance": 1 到 3 的整數, '
        '"clues": [這一輪說出的線索編號，沒有就是空陣列]}\n'
        "affinity_change 的判斷：\n"
        "- +3 到 +5：真誠關心你本人（不只是案件）、理解你的處境或壓力、說出有見地的推理\n"
        "- +1 到 +2：友善、願意幫忙、認真討論案情\n"
        "- 0：普通閒聊、聽不懂的話\n"
        "- -1 到 -5：無禮、威脅、毫無根據地懷疑你、想放棄任務\n"
        "memory 的判斷：記下這一輪「新的」重要資訊，例如對方的推理、承諾、態度，或你們之間發生的事。"
        "要寫清楚是誰說的或做的（例如「伊莉絲認為耶格爾派標記是嫁禍」、「我告訴伊莉絲脊髓液被偷了」），不要把你說的話記成對方說的；"
        "如果和【你記得的事】重複、或只是寒暄，就填空字串；"
        "importance：3 = 重大（秘密、關鍵推理、衝突），2 = 有意義，1 = 小事。"
    )
    return "\n\n".join(parts)
