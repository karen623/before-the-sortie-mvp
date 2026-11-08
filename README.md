# 出擊前夜 MVP：單頁網頁版

《進擊的巨人》AU 二創推理遊戲的最小可玩版本：一個網頁畫面玩完整局。玩家在 21:00～05:00 的 16 次行動內，和 LLM 扮演的角色對話、搜索營地，找出偷走脊髓液的犯人。

> **二創聲明**：本作品為《進擊的巨人》的非商業二次創作，僅作為課堂作業使用，劇情為改編（AU），不使用官方圖片與音樂。

## 安裝

需要 Python 3.9 以上。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env               # 再打開 .env 填入 GROQ_API_KEY
```

API 金鑰可在 [Groq Console](https://console.groq.com/keys) 免費申請。`.env` 不會被上傳。沒有金鑰時把 `.env` 的 `MOCK` 設成 `1`，可以用假的 AI 回應試玩。

## 執行

```bash
python -m src.web
```

打開瀏覽器到 http://localhost:8000。

## 畫面

- **左欄**：營地 8 個地點，點一下就移動（不花時間）
- **中間**：目前地點，可以和在場的人談話（30 分鐘，最多 5 句）或搜索（30 分鐘）；下方是對話與事件紀錄
- **右欄**：已取得的線索、指認犯人（選嫌疑人＋勾選證據）

## 專案規則

開發規則寫在 [CLAUDE.md](CLAUDE.md)，用 Claude Code 開發時會自動讀取。
