"""Guardia sui comandi da terminale (ispirata a OpenJarvis: 'destructive runtime actions
blocked unless explicitly allowed'). RunCommandSkill esegue qualsiasi comando shell dettato a
voce: una conferma vocale ("si") e' una barriera debole contro un comando davvero distruttivo
(ambiguita' del riconoscimento, rumore di fondo, un "si" detto per altro motivo). Questi
pattern restano bloccati SEMPRE, anche dopo conferma esplicita: non sono azioni che un utente
comune ha bisogno di dettare a voce, e i danni (dati, avvio del sistema, tracce di sicurezza)
sono spesso irreversibili."""
import re

DESTRUCTIVE_PATTERNS = [
    (re.compile(r"\brm\s+-[a-z]*r[a-z]*f|\brd\s+/s\s+/q|\bdel\s+/[fsq].*[\\/]\*|\bremove-item\b.*-recurse", re.I),
     "cancellazione ricorsiva di file/cartelle"),
    (re.compile(r"\bformat\s+[a-z]:|\bdiskpart\b", re.I), "formattazione o partizionamento dischi"),
    (re.compile(r"\bvssadmin\b.*\bdelete\b|\bwbadmin\b.*\bdelete\b", re.I), "cancellazione di copie shadow/backup (pattern da ransomware)"),
    (re.compile(r"\bbcdedit\b|\bbootrec\b", re.I), "modifica della configurazione di avvio"),
    (re.compile(r"\breg\s+delete\b|\breg\s+add\b.*hklm", re.I), "modifica distruttiva del registro di sistema"),
    (re.compile(r"\bwevtutil\b.*\bcl\b|\bClear-EventLog\b", re.I), "cancellazione dei log di sistema (anti-forensics)"),
    (re.compile(r"\bnetsh\s+advfirewall\b.*\bstate\s+off\b|\bnetsh\s+firewall\b.*\bdisable\b", re.I), "disattivazione del firewall"),
    (re.compile(r"(curl|wget|iwr|invoke-webrequest)[^\n|]*\|\s*(iex|invoke-expression|sh|bash|powershell)", re.I),
     "scarica ed esegue codice da internet (download-and-execute)"),
    (re.compile(r"-enc(odedcommand)?\s+[a-z0-9+/=]{20,}", re.I), "comando PowerShell offuscato/codificato in base64"),
    (re.compile(r":\(\)\s*\{\s*:\|:&\s*\};\s*:", re.I), "fork bomb"),
    (re.compile(r"\btakeown\b.*\/r\b|\bicacls\b.*\beveryone\b.*\/grant\b", re.I), "presa di possesso/permessi ricorsivi su file altrui"),
    (re.compile(r"\bcipher\s+/w\b", re.I), "sovrascrittura sicura dello spazio libero su disco"),
]


def check_command_safety(command: str) -> str | None:
    """Ritorna il motivo del blocco se il comando corrisponde a un pattern distruttivo noto,
    altrimenti None (comando ammesso, resta comunque soggetto a conferma esplicita)."""
    if not command:
        return None
    for pattern, reason in DESTRUCTIVE_PATTERNS:
        if pattern.search(command):
            return reason
    return None
