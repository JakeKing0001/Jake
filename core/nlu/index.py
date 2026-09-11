"""Indice semantico minimale (v3.0): embedding via Ollama con cache su disco, ricerca per
similarita' coseno in numpy, e fallback lessicale quando gli embedding non sono disponibili
(Ollama spento o modello di embedding non scaricato), cosi' il recupero degli esempi funziona
comunque, solo un po' peggio."""
import hashlib
import json
import re
import threading
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np


def _token_set(text: str) -> set[str]:
    return set(re.findall(r"[a-zàèéìòù0-9]+", (text or "").lower()))


def lexical_similarity(a: str, b: str) -> float:
    tokens_a, tokens_b = _token_set(a), _token_set(b)
    if not tokens_a or not tokens_b:
        return 0.0
    jaccard = len(tokens_a & tokens_b) / len(tokens_a | tokens_b)
    ratio = SequenceMatcher(None, a.lower(), b.lower()).ratio()
    return 0.55 * jaccard + 0.45 * ratio


class SemanticIndex:
    """Mappa chiave -> testo, con vettori normalizzati. Thread-safe per aggiunte a runtime."""

    EMBED_BATCH = 64

    def __init__(self, embedder, cache_path: Path | None = None, model_name: str = "nomic-embed-text"):
        # embedder: callable(list[str]) -> list[list[float]] | None
        self.embedder = embedder
        self.model_name = model_name
        self.cache_path = Path(cache_path) if cache_path else None
        self._keys: list[str] = []
        self._texts: list[str] = []
        self._matrix: np.ndarray | None = None  # (n, dim) righe normalizzate
        self._vectors: dict[str, list[float]] = {}  # sha1(text) -> vettore (cache persistente)
        self._query_cache: dict[str, np.ndarray | None] = {}
        self._lock = threading.RLock()
        self.embeddings_available = True
        self._dirty_cache = False
        self._load_cache()

    # ---- cache -------------------------------------------------------------------------

    def _digest(self, text: str) -> str:
        return hashlib.sha1(f"{self.model_name}\n{text}".encode("utf-8")).hexdigest()

    def _load_cache(self) -> None:
        if not self.cache_path or not self.cache_path.is_file():
            return
        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
            if payload.get("model") == self.model_name and isinstance(payload.get("vectors"), dict):
                self._vectors = payload["vectors"]
        except (OSError, json.JSONDecodeError, ValueError):
            self._vectors = {}

    def save_cache(self) -> None:
        if not self.cache_path or not self._dirty_cache:
            return
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(
                json.dumps({"model": self.model_name, "vectors": self._vectors}), encoding="utf-8"
            )
            self._dirty_cache = False
        except OSError:
            pass

    # ---- costruzione -------------------------------------------------------------------

    def _embed_missing(self, texts: list[str]) -> bool:
        missing = [text for text in dict.fromkeys(texts) if self._digest(text) not in self._vectors]
        if not missing:
            return True
        if self.embedder is None:
            return False
        for start in range(0, len(missing), self.EMBED_BATCH):
            batch = missing[start:start + self.EMBED_BATCH]
            vectors = self.embedder(batch)
            if vectors is None:
                return False
            for text, vector in zip(batch, vectors):
                self._vectors[self._digest(text)] = vector
            self._dirty_cache = True
        return True

    def build(self, items: list[tuple[str, str]]) -> None:
        """items: [(chiave, testo)]. Ricostruisce l'indice da zero."""
        with self._lock:
            self._keys = [key for key, _ in items]
            self._texts = [text for _, text in items]
            self._query_cache.clear()
            self.embeddings_available = self._embed_missing(self._texts)
            self._rebuild_matrix()
            self.save_cache()

    def add(self, key: str, text: str) -> None:
        with self._lock:
            if key in self._keys:
                index = self._keys.index(key)
                self._texts[index] = text
            else:
                self._keys.append(key)
                self._texts.append(text)
            if self.embeddings_available:
                self.embeddings_available = self._embed_missing([text])
            self._rebuild_matrix()
            self.save_cache()

    def remove(self, key: str) -> None:
        with self._lock:
            if key not in self._keys:
                return
            index = self._keys.index(key)
            del self._keys[index]
            del self._texts[index]
            self._rebuild_matrix()

    def _rebuild_matrix(self) -> None:
        if not self.embeddings_available or not self._texts:
            self._matrix = None
            return
        rows = []
        for text in self._texts:
            vector = self._vectors.get(self._digest(text))
            if vector is None:
                self.embeddings_available = False
                self._matrix = None
                return
            rows.append(vector)
        matrix = np.asarray(rows, dtype=np.float32)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        self._matrix = matrix / norms

    def __len__(self) -> int:
        return len(self._keys)

    # ---- ricerca -----------------------------------------------------------------------

    def _query_vector(self, query: str) -> np.ndarray | None:
        if query in self._query_cache:
            return self._query_cache[query]
        vector = None
        if self.embedder is not None:
            cached = self._vectors.get(self._digest(query))
            if cached is None:
                result = self.embedder([query])
                if result:
                    cached = result[0]
            if cached is not None:
                array = np.asarray(cached, dtype=np.float32)
                norm = np.linalg.norm(array)
                vector = array / norm if norm else None
        if len(self._query_cache) > 256:
            self._query_cache.clear()
        self._query_cache[query] = vector
        return vector

    def search(self, query: str, k: int = 10) -> list[tuple[str, float]]:
        """Restituisce [(chiave, punteggio)] ordinati per punteggio decrescente (0..1)."""
        with self._lock:
            if not self._keys:
                return []
            if self._matrix is not None:
                vector = self._query_vector(query)
                if vector is not None and vector.shape[0] == self._matrix.shape[1]:
                    scores = self._matrix @ vector
                    top = np.argsort(-scores)[:k]
                    return [(self._keys[i], float(scores[i])) for i in top]
            # fallback lessicale
            scored = [(key, lexical_similarity(query, text)) for key, text in zip(self._keys, self._texts)]
            scored.sort(key=lambda item: item[1], reverse=True)
            return scored[:k]

    def using_embeddings(self) -> bool:
        return self._matrix is not None
