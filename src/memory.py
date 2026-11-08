"""長期記憶（RAG）：把重要事件存成向量，對話時撈出最相關的幾則。

對應課程第 7 講：用 embedding 模型把文字轉成向量，再用餘弦相似度找出最接近的內容。
- E5Embedder：multilingual-e5-small，真正的語意 embedding（課堂提到的 query: / passage: 前綴就是 E5 的用法）
- SimpleEmbedder：不需安裝任何套件的字元 n-gram 向量，用於測試，也可作為實驗的對照組
"""
from __future__ import annotations

import hashlib
import math
import os
from dataclasses import dataclass, field


class SimpleEmbedder:
    """把文字拆成單字與雙字（n-gram），雜湊到固定維度。只看字面重疊，不懂語意。"""
    name = "simple-ngram"
    dup_threshold = 0.75    # 相似度超過這個值就視為重複
    min_score = 0.2         # 低於這個值不算相關

    def __init__(self, dim: int = 512):
        self.dim = dim

    def _embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        chars = [c for c in text if not c.isspace()]
        grams = chars + [a + b for a, b in zip(chars, chars[1:])]
        for g in grams:
            h = int(hashlib.md5(g.encode("utf-8")).hexdigest(), 16)
            vec[h % self.dim] += 1.0
        return normalize(vec)

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    def embed_passage(self, text: str) -> list[float]:
        return self._embed(text)


class E5Embedder:
    """multilingual-e5-small：第一次使用會自動下載模型（約 470MB）。"""
    name = "multilingual-e5-small"
    # E5 的相似度普遍偏高（無關的句子也常有 0.75 以上），所以門檻比 simple 高；可依實驗結果調整
    dup_threshold = 0.93
    min_score = 0.8

    def __init__(self, model_name: str = "intfloat/multilingual-e5-small"):
        from sentence_transformers import SentenceTransformer
        # 強制用 CPU：部分 macOS 版本搭配新版 PyTorch 時，Apple GPU（MPS）會直接閃退；這個小模型用 CPU 就夠快
        self.model = SentenceTransformer(model_name, device="cpu")

    def embed_query(self, text: str) -> list[float]:
        return self.model.encode("query: " + text, normalize_embeddings=True).tolist()

    def embed_passage(self, text: str) -> list[float]:
        return self.model.encode("passage: " + text, normalize_embeddings=True).tolist()


def load_embedder():
    """依 .env 的 EMBEDDER 決定用哪一種；e5 套件沒裝時自動退回 simple。"""
    choice = os.getenv("EMBEDDER", "e5")
    if choice == "e5":
        try:
            return E5Embedder()
        except ImportError:
            print("⚠️ 找不到 sentence-transformers，長期記憶暫時改用 simple 模式。請執行 pip install -r requirements.txt")
    return SimpleEmbedder()


def normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


@dataclass
class Memory:
    time: str
    text: str
    importance: int
    vector: list = field(repr=False)


class MemoryStore:
    """一個角色的長期記憶。"""

    def __init__(self, embedder):
        self.embedder = embedder
        self.items: list[Memory] = []

    def add(self, time: str, text: str, importance: int = 1) -> bool:
        """存入一則記憶；和既有記憶意思幾乎一樣時不重複存，只保留較高的重要性。"""
        text = text.strip()
        if not text:
            return False
        vec = self.embedder.embed_passage(text)
        threshold = getattr(self.embedder, "dup_threshold", 0.9)
        for m in self.items:
            if m.text == text or cosine(vec, m.vector) >= threshold:
                m.importance = max(m.importance, importance)
                return False
        self.items.append(Memory(time, text, importance, vec))
        return True

    def search(self, query: str, k: int = 3, min_score: float | None = None) -> list[tuple[Memory, float]]:
        """回傳最相關、且彼此不重複的 k 則記憶。分數 = 相似度 + 重要性加權。"""
        if not self.items:
            return []
        if min_score is None:
            min_score = getattr(self.embedder, "min_score", 0.2)
        threshold = getattr(self.embedder, "dup_threshold", 0.9)
        q = self.embedder.embed_query(query)
        scored = []
        for m in self.items:
            sim = cosine(q, m.vector)
            if sim >= min_score:
                scored.append((m, round(sim + 0.05 * (m.importance - 1), 3)))
        scored.sort(key=lambda x: x[1], reverse=True)
        picked: list[tuple[Memory, float]] = []
        for m, sc in scored:
            if all(cosine(m.vector, p.vector) < threshold for p, _ in picked):
                picked.append((m, sc))
            if len(picked) == k:
                break
        return picked
