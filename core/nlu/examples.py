"""Archivio degli esempi frase -> intent (v3.0).

Due sorgenti:
- training/intents.jsonl: esempi curati, versionati col codice (il "dataset di addestramento").
- data/learned_examples.jsonl: esempi imparati a runtime (comandi insegnati dall'utente,
  correzioni, e comandi risolti con successo dal modello), che sopravvivono ai riavvii.

Gli esempi alimentano il recupero semantico del classificatore (few-shot + potatura delle
capacita') e la corsia veloce a corrispondenza esatta, che evita del tutto la chiamata al
modello per le frasi gia' viste."""
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
BUILTIN_PATH = ROOT / "training" / "intents.jsonl"
LEARNED_PATH = ROOT / "data" / "learned_examples.jsonl"


def normalize_key(text: str) -> str:
    text = (text or "").strip().lower().replace("’", "'")
    text = re.sub(r"[.!?…]+$", "", text)
    return re.sub(r"\s+", " ", text).strip()


@dataclass
class Example:
    text: str
    intent: str
    parameters: dict = field(default_factory=dict)
    source: str = "builtin"  # builtin | taught | corrected | auto | forge

    @property
    def key(self) -> str:
        return normalize_key(self.text)

    def to_json(self) -> str:
        return json.dumps(
            {"text": self.text, "intent": self.intent, "parameters": self.parameters, "source": self.source},
            ensure_ascii=False,
        )


class ExampleStore:
    MAX_AUTO_EXAMPLES = 3000

    def __init__(self, builtin_path: Path | None = None, learned_path: Path | None = None):
        self.builtin_path = Path(builtin_path) if builtin_path else BUILTIN_PATH
        self.learned_path = Path(learned_path) if learned_path else LEARNED_PATH
        self._builtin: list[Example] = []
        self._learned: list[Example] = []
        self._by_key: dict[str, Example] = {}
        self.load()

    # ---- caricamento -------------------------------------------------------------------

    @staticmethod
    def _read_jsonl(path: Path, default_source: str) -> list[Example]:
        examples: list[Example] = []
        if not path.is_file():
            return examples
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            text = record.get("text")
            intent = record.get("intent")
            parameters = record.get("parameters") or {}
            if not isinstance(text, str) or not isinstance(intent, str) or not isinstance(parameters, dict):
                continue
            examples.append(Example(text=text, intent=intent, parameters=parameters, source=record.get("source") or default_source))
        return examples

    def load(self) -> None:
        self._builtin = self._read_jsonl(self.builtin_path, "builtin")
        self._learned = self._read_jsonl(self.learned_path, "taught")
        self._rebuild_index()

    def _rebuild_index(self) -> None:
        self._by_key = {}
        for example in self._builtin:
            self._by_key.setdefault(example.key, example)
        # Gli esempi imparati vincono sui builtin a parita' di frase: sono piu' recenti e
        # riflettono cosa intende davvero QUESTO utente.
        for example in self._learned:
            self._by_key[example.key] = example

    def _save_learned(self) -> None:
        self.learned_path.parent.mkdir(parents=True, exist_ok=True)
        self.learned_path.write_text(
            "\n".join(example.to_json() for example in self._learned) + ("\n" if self._learned else ""),
            encoding="utf-8",
        )

    # ---- lettura -----------------------------------------------------------------------

    def all(self) -> list[Example]:
        return list(self._builtin) + list(self._learned)

    def learned(self) -> list[Example]:
        return list(self._learned)

    def intents(self) -> set[str]:
        return {example.intent for example in self.all()}

    def find_exact(self, text: str) -> Example | None:
        return self._by_key.get(normalize_key(text))

    def by_intent(self, intent: str) -> list[Example]:
        return [example for example in self.all() if example.intent == intent]

    # ---- scrittura ---------------------------------------------------------------------

    def add_learned(self, text: str, intent: str, parameters: dict | None = None, source: str = "taught") -> Example:
        key = normalize_key(text)
        parameters = dict(parameters or {})
        self._learned = [example for example in self._learned if example.key != key]
        example = Example(text=key, intent=intent, parameters=parameters, source=source)
        self._learned.append(example)
        # Gli esempi automatici sono i meno affidabili: se si accumulano troppo, si scartano
        # i piu' vecchi (quelli insegnati o corretti esplicitamente restano sempre).
        auto = [e for e in self._learned if e.source == "auto"]
        if len(auto) > self.MAX_AUTO_EXAMPLES:
            to_drop = set(id(e) for e in auto[: len(auto) - self.MAX_AUTO_EXAMPLES])
            self._learned = [e for e in self._learned if id(e) not in to_drop]
        self._rebuild_index()
        self._save_learned()
        return example

    def remove_learned(self, text: str) -> bool:
        key = normalize_key(text)
        before = len(self._learned)
        self._learned = [example for example in self._learned if example.key != key]
        if len(self._learned) == before:
            return False
        self._rebuild_index()
        self._save_learned()
        return True

    def remove_learned_by_intent(self, intent: str) -> int:
        before = len(self._learned)
        self._learned = [example for example in self._learned if example.intent != intent]
        removed = before - len(self._learned)
        if removed:
            self._rebuild_index()
            self._save_learned()
        return removed
