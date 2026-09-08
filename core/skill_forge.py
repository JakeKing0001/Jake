"""Fucina di skill (v3.0): Jake si scrive da solo nuove capacita'.

Quando l'utente chiede qualcosa che nessuna skill copre ("impara a fare X"), Jake chiede a
un modello locale orientato al codice (qwen2.5-coder, o il modello generico) di scrivere un
plugin nel formato di plugins/ (register(registry) + classe con metadata/execute), lo
controlla staticamente, lo importa in un processo separato per verificare che non esploda,
e solo dopo la conferma dell'utente lo salva in plugins/ e lo carica a caldo. Gli esempi
di frasi dichiarati dal plugin (EXAMPLES) entrano subito nell'indice del classificatore.

Sicurezza, in ordine: (1) niente esecuzione senza conferma esplicita; (2) lista nera di
costrutti distruttivi o di evasione (cancellazioni ricorsive, eval/exec, registro di sistema,
rete grezza); (3) solo import risolvibili nell'ambiente corrente; (4) import di prova in
sandbox con timeout; (5) la skill generata resta un file leggibile in plugins/, che l'utente
puo' aprire, modificare o cancellare ("elimina la skill ...")."""
import ast
import importlib.util
import json
import re
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from core.ollama_client import OllamaClient

PLUGINS_DIR = Path(__file__).resolve().parent.parent / "plugins"
FORGE_PREFIX = "learned_"
DEFAULT_CODER_MODEL = "qwen2.5-coder:7b"

FORBIDDEN_PATTERNS = [
    (re.compile(r"\bshutil\.rmtree\b"), "cancellazione ricorsiva di cartelle"),
    (re.compile(r"\bos\.(remove|unlink|rmdir|removedirs)\b"), "cancellazione di file"),
    (re.compile(r"\.unlink\("), "cancellazione di file"),
    (re.compile(r"\bos\.system\b"), "esecuzione di comandi shell grezzi"),
    (re.compile(r"\b(eval|exec|compile)\s*\("), "esecuzione di codice dinamico"),
    (re.compile(r"__import__"), "import dinamico"),
    (re.compile(r"\bwinreg\b"), "modifica del registro di sistema"),
    (re.compile(r"\bctypes\b"), "chiamate native"),
    (re.compile(r"\bsocket\b"), "rete a basso livello"),
    (re.compile(r"\bshutdown\b"), "spegnimento del sistema"),
    (re.compile(r"\bformat\s*[a-z]:"), "formattazione dischi"),
    (re.compile(r"\bsubprocess\.(Popen|run|call|check_output)\s*\([^)]*shell\s*=\s*True"), "shell=True"),
    (re.compile(r"\bopen\s*\([^)]*['\"][wa]"), "scrittura di file (usa solo lettura)"),
    (re.compile(r"\bpathlib[^\n]*write_(text|bytes)|\.write_(text|bytes)\("), "scrittura di file"),
    (re.compile(r"\bkeyboard\.|pyautogui\."), "controllo di tastiera/mouse (usa le skill esistenti)"),
]

ALLOWED_THIRD_PARTY = {"psutil", "PIL", "numpy", "win32gui", "win32con", "win32api", "win32clipboard", "pyperclip", "requests"}

# Controlli statici indipendenti da FORBIDDEN_PATTERNS (v5.3, Self-Improvement controllato):
# quella lista nera e' testuale (regex sul sorgente), quindi aggirabile con l'indirezione,
# es. getattr(os, "system")(...) invece di os.system(...) non contiene mai la sottostringa
# "os.system". Questi controlli lavorano sull'AST e coprono due tecniche note: accesso
# indiretto a un nome pericoloso via getattr con stringa letterale, e la catena classica di
# sandbox-escape di Python (un oggetto qualsiasi -> __class__ -> __bases__/__mro__ ->
# __subclasses__() per risalire a classi non ristrette, es. subprocess.Popen, a partire da un
# valore del tutto innocuo come "".__class__...).
DANGEROUS_ATTR_NAMES = {
    "__globals__", "__builtins__", "__subclasses__", "__base__", "__bases__",
    "__mro__", "__code__", "__closure__", "__loader__",
}
DANGEROUS_INDIRECT_NAMES = {
    "eval", "exec", "compile", "system", "popen", "rmtree", "remove", "unlink",
    "rmdir", "removedirs", "__import__", "chmod", "kill", "startfile",
}

_PLUGIN_TEMPLATE = '''"""Plugin di esempio, formato richiesto."""
import random

from core.skill_result import SkillResult

EXAMPLES = ["lancia una moneta", "testa o croce", "tira una monetina"]


class CoinFlipSkill:
    metadata = {
        "intent": "COIN_FLIP",
        "description": "Lancia una moneta virtuale: testa o croce.",
        "parameters": {},
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        result = random.choice(["testa", "croce"])
        return SkillResult(success=True, data={"result": result})

    def format_result(self, result: SkillResult) -> str:
        return f"E' uscito {result.data['result']}!"


def register(registry) -> None:
    registry.register_skill("COIN_FLIP", CoinFlipSkill())
'''

_SYSTEM_PROMPT = """Sei un programmatore Python esperto che scrive plugin per Jake, un assistente vocale italiano per Windows.
Scrivi UN SOLO file Python completo, e nient'altro (niente spiegazioni, niente markdown fuori dal blocco di codice).

Contratto del plugin (obbligatorio):
- import `from core.skill_result import SkillResult`.
- una costante `EXAMPLES` a livello di modulo: lista di 4-6 frasi italiane, naturali e diverse tra loro, con cui l'utente chiederebbe a voce questa funzione (con valori concreti di esempio se servono parametri).
- una classe con attributo `metadata` = {"intent": "NOME_MAIUSCOLO_CON_UNDERSCORE", "description": "descrizione breve in italiano di cosa fa e quando usarla", "parameters": {nome: {"type": "string|integer|number|boolean|array", "required": true|false, "description": "..."}}}.
- metodo `execute(self, parameters: dict = None)` che legge i parametri (parameters = parameters or {}), valida, e ritorna `SkillResult(success=True, data={...})` oppure `SkillResult(success=False, data={...}, error="MISSING_PARAMETERS"|"NOT_FOUND"|"OPERATION_FAILED"|"INVALID_VALUE")`. Mai eccezioni non gestite.
- metodo `format_result(self, result: SkillResult) -> str` che restituisce una frase italiana breve da leggere a voce (niente markdown).
- funzione `register(registry)` che chiama `registry.register_skill(INTENT, Classe())`.

Vincoli di sicurezza (il codice viene rifiutato se li viola):
- solo libreria standard Python (piu' psutil, PIL, numpy, win32gui, win32clipboard, requests se davvero necessari);
- nessuna cancellazione o scrittura di file, nessun eval/exec, nessun os.system, nessuna shell=True, nessun winreg/ctypes/socket, nessun controllo di tastiera o mouse;
- solo lettura del filesystem, calcoli, conversioni, testo, date, informazioni di sistema, richieste HTTP GET a servizi pubblici senza chiave.
- Il file deve essere autonomo e funzionare al primo colpo. Nomi di intent che NON puoi usare perche' esistono gia': {existing_intents}.

Esempio di plugin nel formato corretto:
```python
{template}
```
"""


@dataclass
class ForgeDraft:
    draft_id: str
    request: str
    intent: str
    description: str
    code: str
    examples: list = field(default_factory=list)
    model: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    @property
    def filename(self) -> str:
        slug = re.sub(r"[^a-z0-9]+", "_", self.intent.lower()).strip("_") or "skill"
        return f"{FORGE_PREFIX}{slug}.py"


class ForgeError(Exception):
    pass


class SkillForge:
    def __init__(self, registry, client: OllamaClient = None, model_provider=None, plugins_dir: Path = None,
                 logger=None, on_skill_installed=None, coder_model: str = None):
        self.registry = registry
        self.client = client or OllamaClient(timeout=180)
        # callable -> nome del modello generico configurato (per il fallback se manca il coder)
        self.model_provider = model_provider or (lambda: "qwen2.5:7b")
        self.plugins_dir = Path(plugins_dir) if plugins_dir else PLUGINS_DIR
        self.logger = logger
        self.on_skill_installed = on_skill_installed
        self.preferred_coder_model = coder_model or DEFAULT_CODER_MODEL
        self.drafts: dict[str, ForgeDraft] = {}
        self.last_error = None

    # ---- disponibilita' ----------------------------------------------------------------

    def is_available(self) -> bool:
        return self.client.is_available()

    def coder_model(self) -> str:
        return self.client.pick_model(self.preferred_coder_model, self.model_provider())

    # ---- generazione -------------------------------------------------------------------

    def _existing_intents(self) -> list[str]:
        return sorted(capability["intent"] for capability in self.registry.list_capabilities())

    def _build_prompt(self) -> str:
        existing = self._existing_intents()
        # replace() e non format(): il prompt contiene graffe letterali (i dizionari del contratto).
        return _SYSTEM_PROMPT.replace("{existing_intents}", ", ".join(existing)).replace("{template}", _PLUGIN_TEMPLATE)

    @staticmethod
    def _extract_code(text: str) -> str:
        match = re.search(r"```(?:python)?\s*\n(.*?)```", text, flags=re.DOTALL)
        code = match.group(1) if match else text
        return code.strip() + "\n"

    def propose(self, request: str, feedback: str = None, attempts: int = 2) -> ForgeDraft:
        """Genera e valida un plugin per la richiesta. Solleva ForgeError con un messaggio
        comprensibile se dopo 'attempts' tentativi il codice non passa i controlli."""
        request = (request or "").strip()
        if not request:
            raise ForgeError("Non ho capito cosa dovrei imparare a fare.")
        model = self.coder_model()
        messages = [
            {"role": "system", "content": self._build_prompt()},
            {"role": "user", "content": f"Scrivi il plugin per questa richiesta dell'utente: \"{request}\"."
                                         + (f" Nota: {feedback}" if feedback else "")},
        ]
        last_problem = None
        for attempt in range(attempts):
            answer = self.client.chat_text(model, messages, options={"temperature": 0.2, "num_ctx": 8192, "num_predict": 2500}, timeout=240)
            if not answer:
                raise ForgeError("Il modello non ha risposto: verifica che Ollama sia attivo.")
            code = self._extract_code(answer)
            try:
                intent, description, examples = self._validate(code)
                draft = ForgeDraft(
                    draft_id=uuid.uuid4().hex[:8], request=request, intent=intent,
                    description=description, code=code, examples=examples, model=model,
                )
                self.drafts[draft.draft_id] = draft
                return draft
            except ForgeError as exc:
                last_problem = str(exc)
                if self.logger:
                    self.logger.warning("Skill generata rifiutata (tentativo %d): %s", attempt + 1, last_problem)
                messages.append({"role": "assistant", "content": answer})
                messages.append({"role": "user", "content": f"Il file e' stato rifiutato: {last_problem}. Riscrivilo completo, corretto, rispettando tutti i vincoli."})
        raise ForgeError(f"Non sono riuscito a scrivere una skill valida: {last_problem}")

    # ---- validazione -------------------------------------------------------------------

    @staticmethod
    def _check_ast_escapes(tree: ast.AST) -> None:
        """Vedi il commento su DANGEROUS_ATTR_NAMES/DANGEROUS_INDIRECT_NAMES: controlli
        sull'AST per le tecniche note di evasione di FORBIDDEN_PATTERNS (che e' solo testuale)."""
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in DANGEROUS_ATTR_NAMES:
                raise ForgeError(
                    f"accede all'attributo '{node.attr}', una tecnica nota per aggirare i controlli di sicurezza"
                )
            if isinstance(node, ast.Call):
                func_name = None
                if isinstance(node.func, ast.Name):
                    func_name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    func_name = node.func.attr
                if func_name == "getattr":
                    for arg in node.args:
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value in DANGEROUS_INDIRECT_NAMES:
                            raise ForgeError(f"usa getattr per raggiungere '{arg.value}' indirettamente, non ammesso")
                if func_name == "import_module":
                    raise ForgeError("importa moduli dinamicamente (importlib.import_module), non ammesso")

    def _validate(self, code: str) -> tuple[str, str, list]:
        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            raise ForgeError(f"errore di sintassi alla riga {exc.lineno}")

        for pattern, why in FORBIDDEN_PATTERNS:
            if pattern.search(code):
                raise ForgeError(f"contiene {why}, non ammesso")
        self._check_ast_escapes(tree)

        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                for name in names:
                    root = (name or "").split(".")[0]
                    if not root or root in ("core",):
                        continue
                    if root in sys.stdlib_module_names or root in ALLOWED_THIRD_PARTY:
                        if importlib.util.find_spec(root) is None:
                            raise ForgeError(f"il modulo '{root}' non e' installato")
                        continue
                    raise ForgeError(f"usa il modulo non ammesso '{root}'")

        has_register = any(isinstance(node, ast.FunctionDef) and node.name == "register" for node in tree.body)
        if not has_register:
            raise ForgeError("manca la funzione register(registry)")

        examples = []
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "EXAMPLES" for t in node.targets):
                try:
                    value = ast.literal_eval(node.value)
                    if isinstance(value, list):
                        examples = [str(item) for item in value if isinstance(item, str) and item.strip()]
                except (ValueError, SyntaxError):
                    pass
        if len(examples) < 2:
            raise ForgeError("manca la lista EXAMPLES con almeno 2 frasi di esempio")

        intent, description = None, None
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for item in node.body:
                    if isinstance(item, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "metadata" for t in item.targets):
                        try:
                            metadata = ast.literal_eval(item.value)
                        except (ValueError, SyntaxError):
                            raise ForgeError("metadata non e' un dizionario letterale")
                        if not isinstance(metadata, dict):
                            raise ForgeError("metadata deve essere un dizionario")
                        intent = metadata.get("intent")
                        description = metadata.get("description")
                        parameters = metadata.get("parameters", {})
                        if not isinstance(parameters, dict):
                            raise ForgeError("metadata['parameters'] deve essere un dizionario")
                method_names = {item.name for item in node.body if isinstance(item, ast.FunctionDef)}
                if intent and "execute" not in method_names:
                    raise ForgeError("la classe della skill non ha il metodo execute")
        if not intent or not isinstance(intent, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{2,40}", intent):
            raise ForgeError("metadata['intent'] deve essere un nome MAIUSCOLO_CON_UNDERSCORE")
        if intent in self._existing_intents():
            raise ForgeError(f"l'intent {intent} esiste gia': scegline un altro")
        if not description or not isinstance(description, str):
            raise ForgeError("manca metadata['description']")

        self._sandbox_import(code)
        return intent, description, examples

    def _sandbox_import(self, code: str) -> None:
        """Importa il plugin in un interprete separato (timeout 20s): un errore all'import o
        un execute() che esplode non devono mai toccare il processo di Jake."""
        root = str(self.plugins_dir.parent)
        probe = (
            "import sys, json, importlib.util\n"
            f"sys.path.insert(0, {root!r})\n"
            "code = sys.stdin.read()\n"
            "spec = importlib.util.spec_from_loader('jake_forge_probe', loader=None)\n"
            "module = importlib.util.module_from_spec(spec)\n"
            "exec(compile(code, 'forge_probe.py', 'exec'), module.__dict__)\n"
            "registered = {}\n"
            "class R:\n"
            "    def register_skill(self, intent, skill): registered[intent] = skill\n"
            "module.register(R())\n"
            "assert registered, 'register() non ha registrato nulla'\n"
            "for intent, skill in registered.items():\n"
            "    result = skill.execute({})\n"
            "    assert hasattr(result, 'success'), 'execute non ritorna SkillResult'\n"
            "    if result.success and hasattr(skill, 'format_result'):\n"
            "        text = skill.format_result(result)\n"
            "        assert isinstance(text, str), 'format_result non ritorna una stringa'\n"
            "print(json.dumps(list(registered)))\n"
        )
        try:
            completed = subprocess.run(
                [sys.executable, "-c", probe], input=code, capture_output=True, text=True,
                timeout=20, encoding="utf-8", errors="replace", cwd=root,
            )
        except subprocess.TimeoutExpired:
            raise ForgeError("l'import del plugin non termina (loop infinito?)")
        except OSError as exc:
            raise ForgeError(f"impossibile avviare la sandbox: {exc}")
        if completed.returncode != 0:
            tail = (completed.stderr or completed.stdout or "").strip().splitlines()
            raise ForgeError("errore in esecuzione: " + (tail[-1] if tail else "sconosciuto"))

    # ---- installazione -----------------------------------------------------------------

    def install(self, draft_id: str):
        draft = self.drafts.get(draft_id)
        if draft is None:
            raise ForgeError("la bozza della skill non esiste piu': riprova a chiedermelo.")
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        path = self.plugins_dir / draft.filename
        header = (
            f'"""Skill creata da Jake ({draft.created_at}) per la richiesta: {draft.request!r}.\n'
            f"Modello: {draft.model}. Puoi modificarla o cancellarla: e' un normale plugin.\n"
            f'REQUEST: {json.dumps(draft.request, ensure_ascii=False)}\n"""\n'
        )
        path.write_text(header + draft.code, encoding="utf-8")

        from core.plugin_loader import load_plugin_file
        loaded = load_plugin_file(self.registry, path, logger=self.logger)
        if not loaded:
            path.unlink(missing_ok=True)
            raise ForgeError("il plugin non si e' caricato: l'ho rimosso.")

        self.drafts.pop(draft_id, None)
        if self.on_skill_installed is not None:
            try:
                self.on_skill_installed(draft)
            except Exception:
                if self.logger:
                    self.logger.exception("Errore nel callback post-installazione della skill")
        if self.logger:
            self.logger.info("Skill installata: %s (%s)", draft.intent, path.name)
        return draft, path

    # ---- gestione ----------------------------------------------------------------------

    def list_created(self) -> list[dict]:
        created = []
        for path in sorted(self.plugins_dir.glob(f"{FORGE_PREFIX}*.py")):
            info = {"file": path.name, "intent": None, "description": "", "request": ""}
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
                doc = ast.get_docstring(tree) or ""
                match = re.search(r"REQUEST: (.*)", doc)
                if match:
                    try:
                        info["request"] = json.loads(match.group(1))
                    except json.JSONDecodeError:
                        info["request"] = match.group(1)
                for node in ast.walk(tree):
                    if isinstance(node, ast.ClassDef):
                        for item in node.body:
                            if isinstance(item, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "metadata" for t in item.targets):
                                metadata = ast.literal_eval(item.value)
                                info["intent"] = metadata.get("intent")
                                info["description"] = metadata.get("description", "")
            except (OSError, SyntaxError, ValueError):
                pass
            created.append(info)
        return created

    _STOPWORDS = {"che", "per", "una", "uno", "del", "della", "dei", "delle", "gli", "les", "con", "come", "alla", "allo", "nel", "nella", "hai", "creato", "skill", "capacita", "capacità", "quella", "quello", "sul", "sulla", "mia", "mio"}

    def delete(self, name: str) -> dict | None:
        """Elimina una skill creata, cercandola per intent, nome file, descrizione o richiesta
        originale: basta che le parole significative (anche flesse: 'contare' ~ 'conta')
        corrispondano."""
        words = [w for w in re.findall(r"[a-zàèéìòù0-9_]+", (name or "").lower()) if len(w) >= 3 and w not in self._STOPWORDS]
        if not words:
            return None
        best, best_score = None, 0
        for info in self.list_created():
            haystack = " ".join(filter(None, [info["file"], (info["intent"] or "").lower(), info["description"], info["request"]])).lower()
            haystack = haystack.replace("_", " ")
            score = sum(1 for w in words if w[:4] in haystack)
            if score > best_score:
                best, best_score = info, score
        if best is None or best_score == 0:
            return None
        path = self.plugins_dir / best["file"]
        path.unlink(missing_ok=True)
        if best["intent"] and hasattr(self.registry, "unregister_skill"):
            self.registry.unregister_skill(best["intent"])
        return best
