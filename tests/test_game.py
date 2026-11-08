"""用模擬 AI 測試整局遊戲流程，不需要金鑰。執行方式：python -m tests.test_game"""
import os

from src.engine import load_json, talk
from src.game import (accuse, decide_fate, fate_options, give_items, new_game, npcs_at,
                      search, time_up)
from src.llm import LLMClient, LLMConfig


def setup(player_class="醫療", origin="帕拉迪島"):
    player = load_json("data/player_example.json") | {"class": player_class, "origin": origin}
    state = new_game(load_json("data/world.json"), player)
    state.log_path = os.devnull
    return state, LLMClient(LLMConfig(mock=True))


def test_all_characters_loaded():
    state, _ = setup()
    assert set(state.npcs) >= {"hange", "levi", "jean", "pieck", "gabi_falco", "onyankopon",
                               "magath", "annie", "armin"}
    for nid, npc in state.npcs.items():
        assert npc.card.get("location") in state.locations, nid
    assert "C01" in state.clues


def test_medic_sees_paint_immediately():
    state, _ = setup("醫療")
    assert any("C02" in line for line in search(state, "medical"))


def test_others_need_second_look():
    state, _ = setup("偵查")
    assert not any("C02" in line for line in search(state, "medical"))
    assert any("C02" in line for line in search(state, "medical"))


def test_scout_bonus_action():
    assert setup("偵查")[0].actions_left == 17
    assert setup("醫療")[0].actions_left == 16


def test_kids_appear_after_forest_search():
    state, _ = setup()
    assert "gabi_falco" not in npcs_at(state, "forest")
    search(state, "forest")
    assert "gabi_falco" in npcs_at(state, "forest")


def test_tea_unlocks_levi_clue():
    state, llm = setup()
    search(state, "port")
    assert "紅茶茶葉" in state.items
    r = talk(state, llm, "levi", "你知道補給船的事嗎？")
    assert r["new_clues"] == []                    # 還沒送茶，不會說
    msgs = give_items(state, "levi")
    assert msgs and "紅茶茶葉" not in state.items and "levi_got_tea" in state.flags
    r = talk(state, llm, "levi", "你知道補給船的事嗎？")
    assert r["new_clues"] == ["C03"]


def test_affinity_gated_clue():
    state, llm = setup()
    r = talk(state, llm, "jean", "你站哨時有看到什麼嗎？")
    assert r["new_clues"] == []                    # 好感度 50 < 52
    talk(state, llm, "jean", "辛苦了，謝謝你")
    r = talk(state, llm, "jean", "你站哨時有看到什麼嗎？")
    assert r["new_clues"] == ["C04"]


def test_supply_ship_needs_leads():
    state, _ = setup()
    search(state, "port")
    assert "C07" not in state.clues
    state.clues.update({"C11": "x", "C10": "x"})
    assert not any("C07" in line for line in search(state, "port"))   # 兩條還不夠
    state.clues["C04"] = "x"
    assert any("C07" in line for line in search(state, "port"))
    assert "fluid_found" in state.flags


def test_engineer_needs_one_lead():
    state, _ = setup("技術")
    state.clues.update({"C11": "x", "C10": "x"})
    assert any("C07" in line for line in search(state, "port"))


def test_accuse_paths():
    state, _ = setup()
    assert accuse(state, "magath", ["C01"])["status"] == "not_enough"
    state.clues.update({"C02": "x", "C05": "x"})
    assert accuse(state, "magath", ["C02", "C05"])["status"] == "correct"
    assert "fluid_found" in state.flags

    state, _ = setup()
    assert accuse(state, "annie", [])["status"] == "wrong"
    assert state.ending["key"] == "infighting" and "亞妮" in state.ending["text"]


def test_fate_and_endings():
    state, _ = setup()
    assert [k for k, _ in fate_options(state)] == ["inject", "volunteers"]
    state.clues["C08"] = "x"
    assert "destroy" in [k for k, _ in fate_options(state)]
    assert decide_fate(state, "destroy")["key"] == "human"
    assert decide_fate(state, "inject")["key"] == "controlled"
    assert decide_fate(state, "volunteers")["key"] == "bitter"


def test_time_up():
    state, _ = setup()
    assert time_up(state)["key"] == "empty_handed"
    state, _ = setup()
    state.flags.add("fluid_found")
    assert time_up(state) is None                  # 有脊髓液就進入處置抉擇


def test_magath_prompt_hides_truth():
    from src.prompts import build_system_prompt
    state, _ = setup()
    npc = state.npcs["magath"]
    prompt = build_system_prompt(state.world, npc.card, state.player, 100, npc.secret_unlocked)
    assert "絕不承認" in prompt and "貨艙" not in prompt


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"✅ {name}")
    print("全部通過")
