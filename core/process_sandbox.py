"""Esecuzione con privilegi ridotti per la Skill Forge (F1, "sandbox OS per i plugin generati
dalla fucina" in ROADMAP.md).

Prima di questo modulo, `SkillForge._sandbox_import()` isolava solo i CRASH (un processo
separato, un timeout) - il codice generato girava comunque con gli STESSI privilegi dell'utente
che esegue Jake: se una tecnica di evasione non ancora prevista dal blocklist testuale/AST
fosse riuscita a far scrivere un file o lanciare un processo, sarebbe successo per davvero
durante la validazione stessa, prima che l'utente vedesse o approvasse nulla (vedi il buco
reale gia' trovato in `core/skill_forge.py::FORBIDDEN_PATTERNS` in questa stessa sessione: il
blocklist bloccava `subprocess.Popen/run` solo con `shell=True` esplicito).

Questo modulo aggiunge un secondo strato, indipendente dal blocklist: il processo di prova gira
con un token duplicato impostato a integrita' 'Low' (Mandatory Integrity Control di Windows -
lo stesso meccanismo usato da Chrome per i processi renderer e da Internet Explorer per la
Protected Mode). E' un livello IMPOSTO DAL SISTEMA OPERATIVO, non da Jake: un processo Low non
puo' scrivere su NESSUN oggetto (file, chiave di registro...) con etichetta 'Medium' o
superiore - praticamente tutto cio' che l'utente possiede normalmente - indipendentemente dai
permessi ACL espliciti sull'oggetto. L'unica eccezione e' un singolo file di output creato da
questo modulo, a cui viene assegnata esplicitamente un'etichetta 'Low' (`SetNamedSecurityInfo`)
prima di lanciare il processo: il canale con cui la sonda comunica il proprio risultato al
chiamante, l'unica scrittura concessa.

Verificato per davvero, non solo implementato: un processo Low lanciato con questo meccanismo
NON riesce a scrivere in una cartella utente normale (PermissionError), riesce a scrivere solo
nel suo file di output dedicato, e riesce comunque a leggere l'input - vedi
tests/test_process_sandbox.py.

Non e' un sandbox completo (rete, quota di CPU/memoria, chiamate di sistema non filtrate
restano possibili): resta un livello aggiuntivo, non un rimpiazzo del blocklist testuale/AST
gia' esistente ne' un vero Job Object/AppContainer con restrizioni piu' ampie - quello resta il
pezzo piu' grande dichiarato ⬜ in ROADMAP.md.

Solo Windows: se le API di sicurezza non sono disponibili per qualunque motivo (piattaforma
diversa, un ambiente ristretto che nega la duplicazione del token...), ripiega su un processo
normale SENZA restrizione di integrita', con un avviso esplicito nel log invece di un
fallimento silenzioso - "degrado elegante" (uno dei principi non negoziabili di ROADMAP.md) non
deve significare "silenzioso" quando il degrado toglie una protezione di sicurezza."""
import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

LOW_INTEGRITY_SID = "S-1-16-4096"

try:
    import win32api
    import win32con
    import win32event
    import win32process
    import win32security

    _WIN32_AVAILABLE = True
except ImportError:
    _WIN32_AVAILABLE = False


@dataclass
class ProbeOutcome:
    """Risultato di run_probe_with_reduced_privileges(). timed_out/launch_error hanno
    precedenza su ok/error (se il processo non e' mai arrivato a scrivere il file di output,
    ok/error restano ai valori di default)."""

    ok: bool = False
    error: str = None
    timed_out: bool = False
    launch_error: str = None
    integrity_restricted: bool = False


def _make_low_integrity_token():
    """Duplica il token del processo corrente e ne abbassa l'integrita' a 'Low'. Solleva
    un'eccezione (pywintypes.error o simile) se una qualunque delle chiamate Win32 fallisce -
    chi chiama deve trattarlo come 'nessuna restrizione disponibile', non propagarlo."""
    current_token = win32security.OpenProcessToken(
        win32api.GetCurrentProcess(),
        win32con.TOKEN_DUPLICATE | win32con.TOKEN_QUERY | win32con.TOKEN_ADJUST_DEFAULT
        | win32con.TOKEN_ASSIGN_PRIMARY,
    )
    new_token = win32security.DuplicateTokenEx(
        current_token, win32security.SecurityImpersonation, win32con.TOKEN_ALL_ACCESS,
        win32security.TokenPrimary, None,
    )
    sid = win32security.ConvertStringSidToSid(LOW_INTEGRITY_SID)
    win32security.SetTokenInformation(new_token, win32security.TokenIntegrityLevel, (sid, 0))
    return new_token


def _make_file_low_writable(path: str) -> None:
    """Etichetta il file con integrita' 'Low' (SACL, mandatory label): un processo Low potra'
    scriverci nonostante la restrizione generale, tutto il resto del filesystem resta escluso."""
    sid = win32security.ConvertStringSidToSid(LOW_INTEGRITY_SID)
    sacl = win32security.ACL()
    # dwMandatoryPolicy=0: nessuna restrizione aggiuntiva sull'oggetto stesso - e' l'etichetta
    # 'Low' che, da sola, permette la scrittura a un processo altrettanto Low (gli oggetti sono
    # 'Medium' di default, da cui la restrizione "no write up" osservata altrove).
    sacl.AddMandatoryAce(win32security.ACL_REVISION_DS, 0, 0, sid)
    win32security.SetNamedSecurityInfo(
        path, win32security.SE_FILE_OBJECT, win32security.LABEL_SECURITY_INFORMATION,
        None, None, None, sacl,
    )


def run_probe_with_reduced_privileges(
    probe_path: Path, input_text: str, cwd: str, timeout: float = 20, extra_args: list = None,
) -> ProbeOutcome:
    """Esegue `python probe_path <input_file> <output_file> [extra_args...]` (probe_path deve
    leggere il primo argomento e scrivere un JSON {"ok": bool, "error": str|None, ...} nel
    secondo) con l'interprete corrente. Se le API di integrita' Windows sono disponibili, il
    processo gira a integrita' Low (vedi il modulo); altrimenti gira normalmente, con
    integrity_restricted=False a indicarlo esplicitamente a chi chiama (per loggare l'avviso,
    vedi SkillForge)."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        input_path = Path(tmp_dir) / "input.txt"
        output_path = Path(tmp_dir) / "output.json"
        input_path.write_text(input_text, encoding="utf-8")
        output_path.write_text("", encoding="utf-8")

        argv = [sys.executable, str(probe_path), str(input_path), str(output_path), *(extra_args or [])]
        outcome = ProbeOutcome()

        low_token = None
        if _WIN32_AVAILABLE:
            try:
                low_token = _make_low_integrity_token()
                _make_file_low_writable(str(output_path))
                outcome.integrity_restricted = True
            except Exception:
                low_token = None
                outcome.integrity_restricted = False

        if low_token is not None:
            _run_with_token(low_token, argv, cwd, timeout, outcome)
        else:
            _run_plain(argv, cwd, timeout, outcome)

        if outcome.launch_error or outcome.timed_out:
            return outcome

        try:
            payload = json.loads(output_path.read_text(encoding="utf-8") or "{}")
        except (json.JSONDecodeError, OSError):
            outcome.ok = False
            outcome.error = "la sonda non ha scritto un risultato leggibile"
            return outcome

        outcome.ok = bool(payload.get("ok"))
        outcome.error = payload.get("error")
        return outcome


def _run_with_token(token, argv: list, cwd: str, timeout: float, outcome: ProbeOutcome) -> None:
    startup_info = win32process.STARTUPINFO()
    cmdline = subprocess.list2cmdline(argv)
    try:
        handle_process, handle_thread, _pid, _tid = win32process.CreateProcessAsUser(
            token, argv[0], cmdline, None, None, False, 0, None, cwd, startup_info,
        )
    except Exception as exc:
        outcome.launch_error = f"impossibile avviare la sandbox a integrita' ridotta: {exc}"
        return
    try:
        wait_result = win32event.WaitForSingleObject(handle_process, int(timeout * 1000))
        if wait_result == win32event.WAIT_TIMEOUT:
            win32process.TerminateProcess(handle_process, 1)
            outcome.timed_out = True
    finally:
        handle_process.Close()
        handle_thread.Close()


def _run_plain(argv: list, cwd: str, timeout: float, outcome: ProbeOutcome) -> None:
    try:
        subprocess.run(argv, cwd=cwd, timeout=timeout, capture_output=True)
    except subprocess.TimeoutExpired:
        outcome.timed_out = True
    except OSError as exc:
        outcome.launch_error = f"impossibile avviare la sandbox: {exc}"
