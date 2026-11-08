"""最小網頁版（MVP）：一個畫面玩完整局。只用 Python 內建模組，不需另外安裝。

執行：python -m src.web    然後打開 http://localhost:8000
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .engine import load_json, talk
from .game import accuse, decide_fate, fate_options, give_items, new_game, npcs_at, search, time_up
from .llm import LLMClient, load_config
from .memory import load_embedder
from .player import load_saved_player

PORT = 8000
TALK_TURNS = 5
INDEX = Path(__file__).resolve().parent.parent / "web" / "index.html"


class Session:
    """單人遊戲：整個伺服器只有一局，按「重新開始」就重開。"""

    def __init__(self):
        self.lock = threading.Lock()
        self.llm = LLMClient(load_config())
        print("載入長期記憶模型中……")
        self.embedder = load_embedder()
        self.reset()

    def reset(self):
        player = load_saved_player() or load_json("data/player_example.json")
        self.state = new_game(load_json("data/world.json"), player, embedder=self.embedder)
        self.location = "medical"
        self.talking = None          # 正在對話的角色 id
        self.turns_left = 0
        self.awaiting_fate = False

    # ---- 回傳給畫面的狀態 ----
    def snapshot(self, events=None) -> dict:
        s = self.state
        return {
            "player": s.player,
            "clock": s.clock(),
            "actions_left": s.actions_left,
            "location": self.location,
            "locations": [{"id": k, "name": v["name"]} for k, v in s.locations.items()],
            "location_desc": s.locations[self.location]["description"],
            "people": [{"id": n, "name": s.npcs[n].card["name"], "affinity": s.npcs[n].affinity}
                       for n in npcs_at(s, self.location)],
            "talking": self.talking and {"id": self.talking, "name": s.npcs[self.talking].card["name"],
                                         "turns_left": self.turns_left},
            "clues": [{"id": k, "text": v} for k, v in sorted(s.clues.items())],
            "items": s.items,
            "fluid_found": "fluid_found" in s.flags,
            "suspects": [{"id": n, "name": s.npcs[n].card["name"]} for n in s.npcs if n != "hange"],
            "fate_options": [{"id": k, "label": v} for k, v in fate_options(s)] if self.awaiting_fate else None,
            "ending": s.ending,
            "events": events or [],
        }

    def _after_action(self, events: list):
        """每次花掉行動後檢查時間是否用完。"""
        s = self.state
        if s.ending or self.awaiting_fate or s.actions_left > 0:
            return
        if self.talking and self.turns_left > 0:
            return
        events.append({"kind": "system", "text": "⏰ 05:00，飛艇的引擎聲響起……"})
        if time_up(s) is None:
            self.awaiting_fate = True
            events.append({"kind": "system", "text": "💉 脊髓液找回來了。出發前，你要怎麼處置它？"})

    # ---- 各種動作 ----
    def move(self, loc: str):
        if loc in self.state.locations and not self.talking:
            self.location = loc
        return []

    def start_talk(self, npc_id: str):
        s = self.state
        if self.talking or npc_id not in npcs_at(s, self.location):
            return [{"kind": "system", "text": "現在不能和這個人談話。"}]
        if not s.spend_action():
            return [{"kind": "system", "text": "時間已經不夠了。"}]
        self.talking, self.turns_left = npc_id, TALK_TURNS
        name = s.npcs[npc_id].card["name"]
        events = [{"kind": "system", "text": f"—— 你走向{name}。（最多 {TALK_TURNS} 句，30 分鐘）"}]
        events += [{"kind": "gift", "text": m} for m in give_items(s, npc_id)]
        return events

    def say(self, text: str, tone: str | None):
        if not self.talking or not text.strip():
            return []
        s = self.state
        r = talk(s, self.llm, self.talking, text.strip(), tone or s.player.get("personality"))
        self.turns_left -= 1
        name = s.npcs[self.talking].card["name"]
        events = [{"kind": "player", "text": text.strip()},
                  {"kind": "npc", "name": name, "emotion": r["emotion"], "text": r["reply"],
                   "affinity": [r["affinity_before"], r["affinity_after"]]}]
        events += [{"kind": "clue", "text": f"🔎 取得新線索 {cid}：{s.clues[cid]}"} for cid in r["new_clues"]]
        if self.turns_left <= 0:
            events += self.end_talk()
        return events

    def end_talk(self):
        if not self.talking:
            return []
        name = self.state.npcs[self.talking].card["name"]
        self.talking, self.turns_left = None, 0
        events = [{"kind": "system", "text": f"—— 你離開了{name}身邊。"}]
        self._after_action(events)
        return events

    def do_search(self):
        if self.talking:
            return []
        if not self.state.spend_action():
            return [{"kind": "system", "text": "時間已經不夠了。"}]
        events = [{"kind": "clue" if "線索" in line or "🎒" in line else "system", "text": line}
                  for line in search(self.state, self.location)]
        self._after_action(events)
        return events

    def do_accuse(self, suspect: str, evidence: list[str]):
        s = self.state
        if self.talking or s.ending or self.awaiting_fate:
            return []
        if not s.spend_action():
            return [{"kind": "system", "text": "時間已經不夠了。"}]
        result = accuse(s, suspect, evidence)
        events = [{"kind": "system", "text": result["text"]}]
        if result["status"] == "correct":
            self.awaiting_fate = True
            events.append({"kind": "system", "text": "💉 脊髓液找回來了。出發前，你要怎麼處置它？"})
        else:
            self._after_action(events)
        return events

    def choose_fate(self, choice: str):
        if not self.awaiting_fate:
            return []
        decide_fate(self.state, choice)
        self.awaiting_fate = False
        return []


SESSION: Session | None = None


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data: dict):
        self._send(200, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, INDEX.read_bytes(), "text/html; charset=utf-8")
        elif self.path == "/api/state":
            with SESSION.lock:
                self._json(SESSION.snapshot())
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        s = SESSION
        routes = {
            "/api/new": lambda: (s.reset(), [])[1],
            "/api/move": lambda: s.move(body.get("location", "")),
            "/api/talk/start": lambda: s.start_talk(body.get("npc", "")),
            "/api/talk": lambda: s.say(body.get("text", ""), body.get("tone")),
            "/api/talk/end": lambda: s.end_talk(),
            "/api/search": lambda: s.do_search(),
            "/api/accuse": lambda: s.do_accuse(body.get("suspect", ""), body.get("evidence", [])),
            "/api/fate": lambda: s.choose_fate(body.get("choice", "")),
        }
        if self.path not in routes:
            self._send(404, b"not found", "text/plain")
            return
        try:
            with s.lock:
                events = routes[self.path]()
                self._json(s.snapshot(events))
        except Exception as e:  # 不讓單一錯誤把伺服器弄掛，畫面上顯示錯誤訊息
            self._json({"error": f"{type(e).__name__}: {e}"})

    def log_message(self, *args):  # 終端機不要每個請求都印一行
        pass


def main():
    global SESSION
    SESSION = Session()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"✅ 遊戲已啟動：打開瀏覽器到 http://localhost:{PORT}　（按 Ctrl+C 結束）")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已關閉。")


if __name__ == "__main__":
    main()
