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
puo' aprire, modificare o cancellare ("elimina la skill ...").

F8.3 (Skill Forge 2.0): con il catalogo firmato (`core/skill_package.SkillStore`) la skill generata non e' piu' un
file sciolto in plugins/ ma un pacchetto come quelli degli editori, nella stessa catena di F8.1/F8.2:
1. le esecuzioni di prova dichiarate dal plugin (FIXTURES) girano nella sandbox e il loro esito REALE diventa le
   fixture del manifest - nessuna fixture inventata;
2. il manifest lo scrive la Forge, non il modello: capability dagli import (tabella chiusa, conservativa), rischio
   = il minimo coerente con quelle capability (`skill_manifest.minimum_risk_for`), provenienza "forge";
3. pacchetto deterministico firmato con la chiave locale della Forge, fidata SOLO per gli id `jakeforge.*`;
4. prima del "si'" l'utente legge permessi e rischio generati dal manifest (`permission_summary`), e il "si'" vale
   per quel digest: `SkillStore.plan_install` -> `approve` -> `install`, poi il caricamento con ricontrollo di firma
   e hash, il rischio dichiarato a `risk_of` e l'esecuzione nel worker isolato (F1.6), come ogni pacchetto;
5. "elimina la skill" la toglie dal catalogo e dal disco.
Senza catalogo (non leggibile all'avvio) resta il percorso storico del file in plugins/."""
import ast
import hashlib
import importlib.util
import json
import re
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from core.ollama_client import OllamaClient
from core.process_sandbox import run_probe_with_reduced_privileges
from core.skill_manifest import MANIFEST_FILENAME, minimum_risk_for, parse_manifest, permission_summary

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PLUGINS_DIR = PROJECT_ROOT / "plugins"
FORGE_PROBE_PATH = Path(__file__).resolve().parent / "forge_probe.py"
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
    # F1: buco reale trovato e corretto - il controllo originale bloccava SOLO
    # subprocess.Popen/run/call/check_output con shell=True esplicito, ma nessuno di questi ha
    # davvero bisogno di shell=True per lanciare un programma arbitrario (shell=True serve solo
    # per l'interpretazione di pipe/redirezioni, non per l'esecuzione in se'): subprocess.run(
    # ["cmd", "/c", "del", "qualsiasi.txt"]) - SENZA shell=True - passava indenne. Verificato per
    # davvero, non ipotizzato: nessun pattern di questa lista intercettava quella riga prima
    # della correzione. Ora blocca la chiamata a prescindere da come e' invocata la shell -
    # subprocess non ha nessun uso legittimo in una skill generata (RUN_COMMAND/
    # RUN_PYTHON_SCRIPT sono gia' il percorso sorvegliato e con conferma per eseguire qualcosa).
    (re.compile(r"\bsubprocess\.(Popen|run|call|check_call|check_output|getoutput|getstatusoutput)\b"), "avvio di un processo esterno (subprocess)"),
    (re.compile(r"\bos\.(popen[0-9]?|spawn[lv]e?p?|exec[lv]e?p?)\b"), "avvio di un processo esterno (os.popen/spawn/exec)"),
    (re.compile(r"\bmultiprocessing\b"), "avvio di processi (multiprocessing)"),
    (re.compile(r"\bopen\s*\([^)]*['\"][wa]"), "scrittura di file (usa solo lettura)"),
    (re.compile(r"\bpathlib[^\n]*write_(text|bytes)|\.write_(text|bytes)\("), "scrittura di file"),
    (re.compile(r"\bkeyboard\.|pyautogui\."), "controllo di tastiera/mouse (usa le skill esistenti)"),
]

# F8.3.4 (secret scan): una skill generata non deve mai avere credenziali scritte nel codice - finirebbero in un file
# in chiaro in plugins/ e nei backup. Chiavi private, token noti per formato e assegnazioni letterali a nomi da
# segreto (api_key/token/secret/password). Un plugin che ne ha bisogno le legge dal vault, non dal sorgente.
SECRET_PATTERNS = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "una chiave privata"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "una chiave di accesso AWS"),
    (re.compile(r"\b(?:ghp|gho|ghs|ghu)_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{30,}"), "un token GitHub"),
    (re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}"), "una chiave API"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), "un token Slack"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), "una chiave API Google"),
    (re.compile(r"""(?i)\b\w*(?:api_?key|secret|token|passw(?:or)?d)\w*["']?\s*[:=]\s*["'][^"'\s]{8,}["']"""),
     "una credenziale scritta nel codice"),
]

ALLOWED_THIRD_PARTY = {"psutil", "PIL", "numpy", "win32gui", "win32con", "win32api", "win32clipboard", "pyperclip", "requests"}

# F8.3: il manifest di una skill forgiata. Le capability le dichiara la Forge dagli import, con una tabella chiusa e
# prudente (un modulo che PUO' fare qualcosa conta come se lo facesse); il modello non sceglie ne' permessi ne' rischio.
FORGE_ID_PREFIX = "jakeforge."
FORGE_PUBLISHER = "Jake Skill Forge"
FORGE_KEY_FILENAME = "forge_signing_key.pem"
IMPORT_CAPABILITIES = {
    "requests": ("network",), "urllib": ("network",), "http": ("network",), "ftplib": ("network",),
    "smtplib": ("network",), "webbrowser": ("web",),
    "psutil": ("process",), "win32api": ("system",), "win32gui": ("apps",),
    "pyperclip": ("clipboard.read", "clipboard.write"), "win32clipboard": ("clipboard.read", "clipboard.write"),
    "PIL": ("screen",),
}
_PARAMETER_TYPES = {"string", "integer", "number", "boolean", "array", "object"}

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
FIXTURES = [{"input": {}}]


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
- una costante `FIXTURES` a livello di modulo: lista di 1-4 esecuzioni di prova {"input": {parametri}} con valori concreti; almeno una deve riuscire (verranno eseguite davvero in una sandbox e il loro esito diventa il test della skill).
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


# F8.3: il test incluso in ogni pacchetto forgiato. Riesegue le fixture del manifest (registrate nella sandbox) contro
# la skill: chiunque puo' lanciarlo sul pacchetto installato per verificare che si comporti ancora come allora.
_FIXTURE_TEST = '''"""Test generato dalla Skill Forge: le fixture del manifest sono l'esito reale delle prove nella sandbox."""
import importlib.util
import json
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


class FixtureTests(unittest.TestCase):
    def test_the_skill_still_behaves_like_in_the_sandbox(self):
        spec = importlib.util.spec_from_file_location("forged_skill", HERE / "skill.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        skills = {}

        class Registry:
            def register_skill(self, intent, skill):
                skills[intent] = skill

        module.register(Registry())
        manifest = json.loads((HERE / "skill.json").read_text(encoding="utf-8"))
        for declared in manifest["intents"]:
            for fixture in declared["fixtures"]:
                result = skills[declared["intent"]].execute(dict(fixture["input"]))
                self.assertEqual(bool(result.success), "output" in fixture, fixture["name"])


if __name__ == "__main__":
    unittest.main()
'''


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
    # F8.3: il pacchetto firmato che il "si'" installa (vuoti senza catalogo: percorso storico in plugins/)
    package: bytes = b""
    signature: dict = field(default_factory=dict)
    digest: str = ""
    permissions: str = ""
    risk: str = ""

    @property
    def filename(self) -> str:
        slug = re.sub(r"[^a-z0-9]+", "_", self.intent.lower()).strip("_") or "skill"
        return f"{FORGE_PREFIX}{slug}.py"


class ForgeError(Exception):
    pass


class SkillForge:
    def __init__(self, registry, client: OllamaClient | None = None, model_provider=None, plugins_dir: Path | None = None,
                 logger=None, on_skill_installed=None, coder_model: str | None = None, skill_store=None,
                 on_package_installed=None):
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
        # F8.3: catalogo firmato in cui installare (None = percorso storico) e chi attiva il pacchetto installato
        # (JakeCore._activate_skill_package: caricamento verificato + rischio dichiarato a risk_of)
        self.skill_store = skill_store
        self.on_package_installed = on_package_installed
        self._signing_key = None

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

    def propose(self, request: str, feedback: str | None = None, attempts: int = 2) -> ForgeDraft:
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
                checked = self._check(code)
                draft = ForgeDraft(
                    draft_id=uuid.uuid4().hex[:8], request=request, intent=checked["intent"],
                    description=checked["description"], code=code, examples=checked["examples"], model=model,
                )
                if self.skill_store is not None:
                    self._package(draft, checked)
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
        checked = self._check(code)
        return checked["intent"], checked["description"], checked["examples"]

    def _check(self, code: str) -> dict:
        """Controlli statici, poi le esecuzioni di prova nella sandbox. Ritorna intent, descrizione, esempi, parametri
        dichiarati, moduli importati, se legge file e l'esito reale di ogni esecuzione di prova."""
        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            raise ForgeError(f"errore di sintassi alla riga {exc.lineno}") from exc

        for pattern, why in FORBIDDEN_PATTERNS:
            if pattern.search(code):
                raise ForgeError(f"contiene {why}, non ammesso")
        for pattern, what in SECRET_PATTERNS:
            if pattern.search(code):
                raise ForgeError(f"contiene quello che sembra {what}: una skill non deve avere credenziali nel codice")
        self._check_ast_escapes(tree)

        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                for name in names:
                    root = (name or "").split(".")[0]
                    imported.add(root)
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

        intent, description, parameters = None, None, {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for item in node.body:
                    if isinstance(item, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "metadata" for t in item.targets):
                        try:
                            metadata = ast.literal_eval(item.value)
                        except (ValueError, SyntaxError) as exc:
                            raise ForgeError("metadata non e' un dizionario letterale") from exc
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

        runs = self._sandbox_import(code)
        reads_files = any(isinstance(node, ast.Call) and (
            (isinstance(node.func, ast.Name) and node.func.id == "open")
            or (isinstance(node.func, ast.Attribute) and node.func.attr in ("read_text", "read_bytes", "open", "iterdir", "glob")))
            for node in ast.walk(tree))
        return {"intent": intent, "description": description, "examples": examples, "parameters": parameters,
                "imported": imported, "reads_files": reads_files, "runs": runs}

    def _sandbox_import(self, code: str) -> list:
        """Importa il plugin ed esegue execute()/format_result() in un processo separato
        (timeout 20s): un errore all'import o un execute() che esplode non devono mai toccare
        il processo di Jake. Quando le API di Windows lo permettono, il processo di prova gira
        anche a integrita' 'Low' (Mandatory Integrity Control) - vedi core/process_sandbox.py:
        un secondo strato indipendente dal blocklist testuale/AST di FORBIDDEN_PATTERNS, che
        blocca a livello di sistema operativo le scritture su file/registro anche per tecniche
        di evasione non ancora previste dal blocklist. Se quelle API non sono disponibili, si
        ripiega sull'esecuzione normale (solo isolamento dai crash) con un avviso nel log."""
        # la radice di Jake (dove sta `core`), non la cartella sopra plugins_dir: con una cartella dei plugin
        # personalizzata la sonda non trovava piu' core.skill_result e ogni skill risultava rotta
        root = str(PROJECT_ROOT)
        outcome = run_probe_with_reduced_privileges(
            FORGE_PROBE_PATH, code, cwd=root, timeout=20, extra_args=[root],
        )
        if outcome.launch_error:
            raise ForgeError(outcome.launch_error)
        if outcome.timed_out:
            raise ForgeError("l'import del plugin non termina (loop infinito?)")
        if not outcome.integrity_restricted and self.logger:
            self.logger.warning(
                "Skill Forge: sandbox a integrita' ridotta non disponibile, "
                "il plugin di prova gira con i privilegi normali (solo isolamento dai crash)."
            )
        if not outcome.ok:
            raise ForgeError("errore in esecuzione: " + (outcome.error or "sconosciuto"))
        runs = outcome.payload.get("fixtures")
        return runs if isinstance(runs, list) else []

    # ---- F8.3: pacchetto firmato ---------------------------------------------------------

    def _forge_key(self):
        """La chiave con cui la Forge firma cio' che ha generato e controllato. Sta accanto al catalogo e nel
        TrustStore e' fidata solo per gli id `jakeforge.*`: non puo' firmare una skill che finge di essere di un
        editore. Chi ha gia' accesso in scrittura a quella cartella puo' comunque scrivere un plugin: la firma qui
        attesta origine e integrita' a riposo (ricontrollate a ogni caricamento), non un segreto remoto."""
        from core.skill_package import PublisherKey

        if self._signing_key is not None:
            return self._signing_key
        path = Path(self.skill_store.root) / FORGE_KEY_FILENAME
        if path.exists():
            key = PublisherKey.from_pem(path.read_bytes())
        else:
            key = PublisherKey.generate()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(key.to_pem())
        trust = self.skill_store.trust
        if trust.get(key.key_id) is None:
            trust.add(FORGE_PUBLISHER, key.public_b64(), FORGE_ID_PREFIX)
        self._signing_key = key
        return key

    @staticmethod
    def _manifest_for(draft: ForgeDraft, checked: dict) -> dict:
        capabilities = sorted({cap for module in checked["imported"] for cap in IMPORT_CAPABILITIES.get(module, ())}
                              | ({"filesystem.read"} if checked["reads_files"] else set()))
        risk = minimum_risk_for(capabilities)
        read_only = risk.value == "read_only"
        properties, required = {}, []
        for name, spec in (checked["parameters"] or {}).items():
            spec = spec if isinstance(spec, dict) else {}
            kind = spec.get("type") if spec.get("type") in _PARAMETER_TYPES else "string"
            properties[str(name)] = {"type": kind}
            if spec.get("required"):
                required.append(str(name))
        input_schema: dict = {"type": "object", "properties": properties}
        if required:
            input_schema["required"] = required
        errors: dict = {}
        fixtures = []
        for index, run in enumerate(checked["runs"], start=1):
            name = f"prova {index} nella sandbox"
            if run.get("success"):
                fixtures.append({"name": name, "input": run.get("input") or {}, "output": run.get("data") or {}})
            else:
                code = re.sub(r"[^a-z0-9_]+", "_", str(run.get("error") or "failed").lower()).strip("_")[:47] or "failed"
                code = code if code[0].isalpha() else f"e_{code}"[:47]
                errors.setdefault(code, f"errore della skill: {run.get('error') or 'sconosciuto'}")
                fixtures.append({"name": name, "input": run.get("input") or {}, "error_code": code})
        if not any("output" in fixture for fixture in fixtures):
            raise ForgeError("nessuna esecuzione di prova e' riuscita: aggiungi in FIXTURES un input con cui la skill funziona")
        slug = re.sub(r"[^a-z0-9_]+", "_", draft.intent.lower()).strip("_")
        return {
            "schema_version": 1, "id": f"{FORGE_ID_PREFIX}{slug}", "name": draft.intent.replace("_", " ").capitalize()[:80],
            "version": "1.0.0", "description": draft.description[:500],
            "author": {"name": FORGE_PUBLISHER},
            "provenance": {"kind": "forge", "source": f"Skill Forge ({draft.model or 'modello locale'}): {draft.request}"[:300],
                           "created_at": draft.created_at[:10], "sha256": hashlib.sha256(draft.code.encode("utf-8")).hexdigest()},
            "compatibility": {"jake": ">=5.9,<7", "python": ">=3.11", "windows": ">=10"},
            "dependencies": [], "entry": "skill.py", "tests": {"files": ["test_skill.py"]},
            "intents": [{
                "intent": draft.intent, "description": draft.description[:300], "risk": risk.value,
                "effect_class": "read" if read_only else ("external" if risk.value == "external_action" else "modify"),
                "capabilities": capabilities,
                "verifier": ({"kind": "none", "reason": "sola lettura: nessun effetto da verificare"} if read_only
                             else {"kind": "declarative", "expect": "l'esito dichiarato dalla skill (SkillResult.success)"}),
                "undo": {"supported": False, "reason": "skill generata: nessuna funzione di annullamento dichiarata"},
                "input_schema": input_schema, "output_schema": {"type": "object"},
                "errors": [{"code": code, "description": text} for code, text in errors.items()],
                "fixtures": fixtures,
            }],
        }

    def _package(self, draft: ForgeDraft, checked: dict) -> None:
        """Manifest, pacchetto deterministico e firma: il "si'" dell'utente installera' esattamente questi byte."""
        from core.skill_manifest import ManifestError
        from core.skill_package import build_package, sha256_hex

        data = self._manifest_for(draft, checked)
        try:
            manifest = parse_manifest(data, self.skill_store.env, self._existing_intents())
        except ManifestError as exc:
            raise ForgeError("il manifest generato non e' valido: " + "; ".join(exc.errors[:3])) from exc
        with tempfile.TemporaryDirectory(prefix="jake_forge_") as tmp:
            folder = Path(tmp)
            (folder / MANIFEST_FILENAME).write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True),
                                                    encoding="utf-8")
            (folder / "skill.py").write_text(draft.code, encoding="utf-8")
            (folder / "test_skill.py").write_text(_FIXTURE_TEST, encoding="utf-8")
            package = build_package(folder)
        draft.package = package
        draft.signature = self._forge_key().sign_package(package)
        draft.digest = sha256_hex(package)
        draft.permissions = permission_summary(manifest)
        draft.risk = manifest.highest_risk().value

    # ---- installazione -----------------------------------------------------------------

    def install(self, draft_id: str, digest: str | None = None):
        draft = self.drafts.get(draft_id)
        if draft is None:
            raise ForgeError("la bozza della skill non esiste piu': riprova a chiedermelo.")
        if draft.package and self.skill_store is not None:
            return self._install_package(draft, digest)
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

    def _install_package(self, draft: ForgeDraft, digest: str | None):
        """Il "si'" vale per il pacchetto mostrato: stesso digest, poi piano -> approvazione -> installazione nel
        catalogo e attivazione con i controlli di ogni pacchetto (firma, hash, manifest, worker isolato)."""
        from core.skill_package import PackageError, approve

        if digest != draft.digest:
            # anche una conferma senza digest: il "si'" vale per il pacchetto mostrato, non per l'id di una bozza
            raise ForgeError("la skill da attivare non e' piu' quella che ti ho mostrato: non la installo.")
        store = self.skill_store
        try:
            plan = store.plan_install(draft.package, draft.signature)
            if plan.verified.digest != draft.digest:
                raise ForgeError("il pacchetto e' cambiato dopo i controlli: non lo installo.")
            if plan.blockers:
                raise ForgeError("non installabile: " + "; ".join(plan.blockers))
            store.install(plan, approve(plan, "utente (conferma di CREATE_SKILL)"))
        except PackageError as exc:
            raise ForgeError(f"installazione rifiutata dal catalogo: {exc}") from exc
        skill_id = plan.verified.manifest.id
        activate = self.on_package_installed
        loaded = activate(skill_id) if activate is not None else bool(store.load(self.registry, skill_id, self.logger).ok)
        if not loaded:
            store.uninstall(skill_id)
            raise ForgeError("il pacchetto non si e' caricato: l'ho rimosso.")
        self.drafts.pop(draft.draft_id, None)
        if self.on_skill_installed is not None:
            try:
                self.on_skill_installed(draft)
            except Exception:
                if self.logger:
                    self.logger.exception("Errore nel callback post-installazione della skill")
        if self.logger:
            self.logger.info("Skill forgiata installata come pacchetto firmato: %s (%s)", skill_id, draft.digest[:12])
        return draft, store.usable_directory(skill_id)

    # ---- gestione ----------------------------------------------------------------------

    def _forged_packages(self) -> list[dict]:
        store = self.skill_store
        if store is None:
            return []
        found = []
        for skill_id in store.skills():
            if not skill_id.startswith(FORGE_ID_PREFIX):
                continue
            info = {"file": skill_id, "intent": None, "description": "", "request": "", "package": skill_id}
            try:
                data = json.loads((store.usable_directory(skill_id) / MANIFEST_FILENAME).read_text(encoding="utf-8"))
                spec = data["intents"][0]
                info.update(intent=spec["intent"], description=spec.get("description", ""),
                            request=data["provenance"]["source"].split(": ", 1)[-1])
            except Exception:
                pass
            found.append(info)
        return found

    def list_created(self) -> list[dict]:
        created = self._forged_packages()
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
        if best.get("package"):
            removed = self.skill_store.uninstall(best["package"])
            if best["intent"] and hasattr(self.registry, "unregister_skill"):
                self.registry.unregister_skill(best["intent"])
            return {**best, "path": str(removed)}
        path = self.plugins_dir / best["file"]
        # F1.3 (execution_safety.INTENT_SAFETY_REGISTRY, verificatore per DELETE_CREATED_SKILL):
        # il percorso completo va nel risultato PRIMA di eliminare il file, cosi' un verificatore
        # indipendente (Path(data["path"]).exists()) puo' controllare per davvero che il file sia
        # sparito - "file" da solo (il nome, senza cartella) non basta a ricostruirlo altrove.
        best = {**best, "path": str(path)}
        path.unlink(missing_ok=True)
        if best["intent"] and hasattr(self.registry, "unregister_skill"):
            self.registry.unregister_skill(best["intent"])
        return best
