"""Classificazione del rischio per skill (v3.2, fase Reliability & Architecture).

Prima d'ora ogni skill decideva da sola, caso per caso, quando chiedere conferma
(il pattern CONFIRMATION_REQUIRED sparso nei singoli file in skills/), config.json
aveva un elenco a parte di always_confirm_intents, e core/command_safety.py
copriva solo RUN_COMMAND. Nessuna di queste fonti sapeva delle altre due, e non
c'era modo di chiedersi "quali skill di Jake possono fare danni?" senza leggere
tutti i file uno per uno.

Questo modulo e' la fonte di verita' unica: ogni skill built-in e' censita qui in
uno dei 5 livelli usati anche dal futuro Permissions & Security Kernel (fase 5.4
della roadmap), cosi' la stessa scala serve gia' da ora e restera' valida quando
quella fase arrivera' a fare enforcement vero (ALLOW/DENY/CONFIRM/REQUIRE_AUTH).

Per ora la classificazione e' solo informativa (esposta da SkillRegistry.list_
capabilities, vedi core/skill_registry.py): non cambia ancora quali azioni
richiedono conferma, quel comportamento resta quello gia' esistente in JakeCore
e nelle singole skill. E' il censimento che deve esistere PRIMA di poter costruire
un'enforcement centralizzata sopra, e tests/test_risk.py obbliga chi aggiunge una
nuova skill built-in a classificarla esplicitamente qui, invece di lasciarla
scoperta senza che nessuno se ne accorga."""

from enum import Enum


class RiskLevel(str, Enum):
    """Ordine crescente di blast radius: READ_ONLY non cambia mai nulla, ADMIN puo'
    spegnere la macchina o eseguire codice arbitrario."""

    READ_ONLY = "read_only"
    LOCAL_REVERSIBLE = "local_reversible"
    EXTERNAL_ACTION = "external_action"
    DESTRUCTIVE = "destructive"
    ADMIN = "admin"


_ORDER = {level: index for index, level in enumerate(RiskLevel)}


def is_at_least(level: RiskLevel, threshold: RiskLevel) -> bool:
    """Vero se level e' al livello di rischio di threshold o superiore (es. e' utile per un
    futuro 'chiedi sempre conferma per le skill DESTRUCTIVE o piu' rischiose')."""
    return _ORDER[level] >= _ORDER[threshold]


# Un intent per riga cosi' com'e' raggruppato in core/skill_catalog.py, cosi' i due file si
# possono confrontare a vista. Le skill che gia' implementano una propria conferma esplicita
# (es. DELETE_PATH) restano comunque classificate con il livello di rischio reale dell'azione,
# non con quello "attenuato" dalla conferma: la conferma e' un controllo, non cambia il rischio.
SKILL_RISK: dict[str, RiskLevel] = {
    # -- tempo/data: sola lettura --------------------------------------------------------
    "GET_TIME": RiskLevel.READ_ONLY,
    "GET_DATE": RiskLevel.READ_ONLY,
    "GET_DAY_OF_WEEK": RiskLevel.READ_ONLY,
    "DAYS_UNTIL": RiskLevel.READ_ONLY,
    "GET_WEEK_NUMBER": RiskLevel.READ_ONLY,
    "CONVERT_TIMEZONE": RiskLevel.READ_ONLY,

    # -- app/finestre: locali e reversibili, tranne chiudere un processo -----------------
    "OPEN_APP": RiskLevel.LOCAL_REVERSIBLE,
    "FOCUS_WINDOW": RiskLevel.LOCAL_REVERSIBLE,
    "MINIMIZE_WINDOW": RiskLevel.LOCAL_REVERSIBLE,
    "LIST_PROCESSES": RiskLevel.READ_ONLY,
    "CLOSE_APP": RiskLevel.DESTRUCTIVE,
    "GET_ACTIVE_WINDOW": RiskLevel.READ_ONLY,
    "LIST_OPEN_WINDOWS": RiskLevel.READ_ONLY,
    "MAXIMIZE_WINDOW": RiskLevel.LOCAL_REVERSIBLE,
    "RESTORE_WINDOW": RiskLevel.LOCAL_REVERSIBLE,
    "SNAP_WINDOW_LEFT": RiskLevel.LOCAL_REVERSIBLE,
    "SNAP_WINDOW_RIGHT": RiskLevel.LOCAL_REVERSIBLE,
    "SWITCH_NEXT_WINDOW": RiskLevel.LOCAL_REVERSIBLE,
    "MINIMIZE_ALL_WINDOWS": RiskLevel.LOCAL_REVERSIBLE,
    "SET_WINDOW_ALWAYS_ON_TOP": RiskLevel.LOCAL_REVERSIBLE,
    "RESIZE_WINDOW": RiskLevel.LOCAL_REVERSIBLE,
    "CLOSE_WINDOW": RiskLevel.LOCAL_REVERSIBLE,

    # -- filesystem: lettura libera, scrittura reversibile, cancellazione distruttiva ----
    "OPEN_PATH": RiskLevel.LOCAL_REVERSIBLE,
    "CREATE_PATH": RiskLevel.LOCAL_REVERSIBLE,
    "RENAME_PATH": RiskLevel.LOCAL_REVERSIBLE,
    "MOVE_PATH": RiskLevel.LOCAL_REVERSIBLE,
    "DELETE_PATH": RiskLevel.DESTRUCTIVE,
    "FIND_FILE": RiskLevel.READ_ONLY,
    "SEARCH_FILES": RiskLevel.READ_ONLY,
    "OPEN_SEARCH_RESULT": RiskLevel.LOCAL_REVERSIBLE,
    "SEMANTIC_SEARCH_FILES": RiskLevel.READ_ONLY,
    "HYBRID_SEARCH_FILES": RiskLevel.READ_ONLY,
    "BUILD_SEMANTIC_INDEX": RiskLevel.LOCAL_REVERSIBLE,
    "COMPRESS_PATH": RiskLevel.LOCAL_REVERSIBLE,
    "EXTRACT_ARCHIVE": RiskLevel.DESTRUCTIVE,  # puo' sovrascrivere file esistenti senza preavviso
    "GET_FILE_INFO": RiskLevel.READ_ONLY,
    "COUNT_WORDS_IN_FILE": RiskLevel.READ_ONLY,
    "READ_FILE_TEXT": RiskLevel.READ_ONLY,
    "DUPLICATE_FILE": RiskLevel.LOCAL_REVERSIBLE,
    "GET_FOLDER_SIZE": RiskLevel.READ_ONLY,
    "FIND_LARGE_FILES": RiskLevel.READ_ONLY,
    "FIND_DUPLICATE_FILES": RiskLevel.READ_ONLY,
    "LIST_RECENT_FILES": RiskLevel.READ_ONLY,
    "PRINT_FILE": RiskLevel.EXTERNAL_ACTION,  # consuma una risorsa fisica esterna

    # -- web: query in lettura, salvo azioni che aprono/inviano qualcosa -----------------
    "WEB_SEARCH": RiskLevel.READ_ONLY,
    "GET_WEATHER": RiskLevel.READ_ONLY,
    "GET_NEWS": RiskLevel.READ_ONLY,
    "OPEN_URL": RiskLevel.LOCAL_REVERSIBLE,
    "GET_BROWSER_HISTORY": RiskLevel.READ_ONLY,
    "READ_WEB_PAGE": RiskLevel.READ_ONLY,  # legge, il browser isolato e' terminato prima che execute() ritorni
    "PING_HOST": RiskLevel.READ_ONLY,
    "TRACE_ROUTE": RiskLevel.READ_ONLY,
    "CHECK_WEBSITE_STATUS": RiskLevel.READ_ONLY,
    "SEARCH_IN_BROWSER": RiskLevel.LOCAL_REVERSIBLE,
    "PLAY_MEDIA": RiskLevel.LOCAL_REVERSIBLE,
    "OPEN_INCOGNITO_WINDOW": RiskLevel.LOCAL_REVERSIBLE,

    # -- memoria/note/todo: cancellare e' l'unica azione distruttiva ---------------------
    "REMEMBER": RiskLevel.LOCAL_REVERSIBLE,
    "RECALL": RiskLevel.READ_ONLY,
    "FORGET": RiskLevel.DESTRUCTIVE,
    "LINK_MEMORY": RiskLevel.LOCAL_REVERSIBLE,
    "PURGE_OLD_HISTORY": RiskLevel.DESTRUCTIVE,
    "ADD_NOTE": RiskLevel.LOCAL_REVERSIBLE,
    "LIST_NOTES": RiskLevel.READ_ONLY,
    "SEARCH_NOTES": RiskLevel.READ_ONLY,
    "EXPORT_NOTES": RiskLevel.LOCAL_REVERSIBLE,
    "CLEAR_NOTES": RiskLevel.DESTRUCTIVE,
    "ADD_TODO": RiskLevel.LOCAL_REVERSIBLE,
    "LIST_TODOS": RiskLevel.READ_ONLY,
    "COMPLETE_TODO": RiskLevel.LOCAL_REVERSIBLE,
    "DELETE_TODO": RiskLevel.DESTRUCTIVE,

    # -- automazioni: definirle e' reversibile, farle partire eredita il rischio dei passi --
    "SAVE_WORKFLOW": RiskLevel.LOCAL_REVERSIBLE,
    "RUN_WORKFLOW": RiskLevel.EXTERNAL_ACTION,  # esegue passi salvati in precedenza, non ispezionati qui
    # F3.8.5: stesso principio di RUN_WORKFLOW sopra - "definirla e' reversibile" non si applica
    # ancora (nessuna skill SAVE_COMPUTER_PROCEDURE esiste oggi, una procedura si costruisce solo
    # in codice/con core/procedure_manager.py direttamente), ma "farla partire eredita il rischio
    # dei passi" si' - ogni passo con un risk_intent proprio resta comunque gated singolarmente
    # da PolicyEngine dentro ComputerAgent (F3.4.3), non ispezionato qui.
    "RUN_COMPUTER_PROCEDURE": RiskLevel.EXTERNAL_ACTION,
    # osserva click/scritture in UNA finestra e salva selettori (mai schermo ne' password)
    "RECORD_COMPUTER_PROCEDURE": RiskLevel.LOCAL_REVERSIBLE,
    # F1.8.4: riprende un compito composto interrotto rieseguendo l'agente - stesso principio di
    # RUN_WORKFLOW sopra, i passi che l'agente ripreso decide di fare non sono ispezionati qui
    # (ma restano comunque gated singolarmente da PolicyEngine come qualunque altro passo).
    "RESUME_INTERRUPTED_TASK": RiskLevel.EXTERNAL_ACTION,
    "SET_TRIGGER": RiskLevel.LOCAL_REVERSIBLE,
    "LIST_TRIGGERS": RiskLevel.READ_ONLY,
    "DELETE_TRIGGER": RiskLevel.DESTRUCTIVE,
    "SET_REMINDER": RiskLevel.LOCAL_REVERSIBLE,
    "LIST_REMINDERS": RiskLevel.READ_ONLY,
    "SNOOZE_REMINDER": RiskLevel.LOCAL_REVERSIBLE,
    "DELETE_REMINDER": RiskLevel.DESTRUCTIVE,
    "SET_DAILY_REMINDER": RiskLevel.LOCAL_REVERSIBLE,
    "START_POMODORO": RiskLevel.LOCAL_REVERSIBLE,
    "STOP_POMODORO": RiskLevel.LOCAL_REVERSIBLE,
    "SET_TIMER": RiskLevel.LOCAL_REVERSIBLE,
    "CANCEL_TIMER": RiskLevel.LOCAL_REVERSIBLE,
    "LIST_TIMERS": RiskLevel.READ_ONLY,

    # -- schermo/input: precursori del Computer Use Engine (fase 3.7) --------------------
    "TAKE_SCREENSHOT": RiskLevel.LOCAL_REVERSIBLE,
    "READ_SCREEN": RiskLevel.READ_ONLY,
    "DESCRIBE_SCREEN": RiskLevel.READ_ONLY,
    "CLICK_MOUSE": RiskLevel.LOCAL_REVERSIBLE,
    "MOVE_MOUSE": RiskLevel.LOCAL_REVERSIBLE,
    "TYPE_TEXT": RiskLevel.LOCAL_REVERSIBLE,
    "PRESS_KEY": RiskLevel.LOCAL_REVERSIBLE,
    "CLICK_TEXT": RiskLevel.LOCAL_REVERSIBLE,
    "CLICK_ELEMENT": RiskLevel.LOCAL_REVERSIBLE,
    "SCROLL": RiskLevel.LOCAL_REVERSIBLE,
    "READ_SELECTION": RiskLevel.READ_ONLY,

    # -- media --------------------------------------------------------------------------
    "SET_VOLUME": RiskLevel.LOCAL_REVERSIBLE,
    "MEDIA_CONTROL": RiskLevel.LOCAL_REVERSIBLE,
    "SET_VOLUME_LEVEL": RiskLevel.LOCAL_REVERSIBLE,
    "GET_VOLUME_LEVEL": RiskLevel.READ_ONLY,

    # -- sistema: la maggior parte legge stato, poche toccano configurazione o spengono --
    "SYSTEM_POWER": RiskLevel.ADMIN,  # spegnimento/riavvio/logoff
    "SET_BRIGHTNESS": RiskLevel.LOCAL_REVERSIBLE,
    "EMPTY_RECYCLE_BIN": RiskLevel.DESTRUCTIVE,
    "LIST_WIFI_NETWORKS": RiskLevel.READ_ONLY,
    "GET_WIFI_STATUS": RiskLevel.READ_ONLY,
    "GET_BATTERY_STATUS": RiskLevel.READ_ONLY,
    "GET_CPU_USAGE": RiskLevel.READ_ONLY,
    "GET_MEMORY_USAGE": RiskLevel.READ_ONLY,
    "GET_DISK_USAGE": RiskLevel.READ_ONLY,
    "GET_UPTIME": RiskLevel.READ_ONLY,
    "GET_SYSTEM_INFO": RiskLevel.READ_ONLY,
    "GET_LOCAL_IP": RiskLevel.READ_ONLY,
    "GET_PUBLIC_IP": RiskLevel.READ_ONLY,
    "CLEAR_TEMP_FILES": RiskLevel.DESTRUCTIVE,
    "RESTART_EXPLORER": RiskLevel.DESTRUCTIVE,  # termina e riavvia explorer.exe
    "FLUSH_DNS": RiskLevel.LOCAL_REVERSIBLE,
    "LIST_STARTUP_APPS": RiskLevel.READ_ONLY,
    "LIST_INSTALLED_APPS": RiskLevel.READ_ONLY,
    "SET_POWER_PLAN": RiskLevel.ADMIN,  # cambia una configurazione di sistema globale
    "TOGGLE_DARK_MODE": RiskLevel.LOCAL_REVERSIBLE,
    "GET_SCREEN_RESOLUTION": RiskLevel.READ_ONLY,
    "GET_GPU_INFO": RiskLevel.READ_ONLY,
    "GET_DNS_SERVERS": RiskLevel.READ_ONLY,
    "GET_ENVIRONMENT_VARIABLE": RiskLevel.READ_ONLY,
    "GET_MAC_ADDRESS": RiskLevel.READ_ONLY,
    "LIST_DRIVES": RiskLevel.READ_ONLY,

    # -- domotica: leggere lo stato e' innocuo, agire su un dispositivo tocca il mondo reale --
    "LIST_SMART_DEVICES": RiskLevel.READ_ONLY,
    "CONTROL_SMART_DEVICE": RiskLevel.EXTERNAL_ACTION,

    # -- dev tools: RUN_COMMAND/RUN_PYTHON_SCRIPT eseguono codice arbitrario -------------
    "GIT_STATUS": RiskLevel.READ_ONLY,
    "GIT_PULL": RiskLevel.EXTERNAL_ACTION,  # porta dentro contenuto da un remoto
    "GIT_LOG": RiskLevel.READ_ONLY,
    "GIT_BRANCH": RiskLevel.READ_ONLY,
    "GIT_DIFF": RiskLevel.READ_ONLY,
    "OPEN_IN_EDITOR": RiskLevel.LOCAL_REVERSIBLE,
    "RUN_COMMAND": RiskLevel.ADMIN,
    "RUN_PYTHON_SCRIPT": RiskLevel.ADMIN,
    "FORMAT_JSON": RiskLevel.READ_ONLY,
    "COUNT_LINES_OF_CODE": RiskLevel.READ_ONLY,
    "GENERATE_UUID": RiskLevel.READ_ONLY,
    "CHECK_PORT_IN_USE": RiskLevel.READ_ONLY,
    "KILL_PROCESS_BY_PORT": RiskLevel.DESTRUCTIVE,
    "CHECK_PASSWORD_STRENGTH": RiskLevel.READ_ONLY,
    "CHECK_FILE_HASH": RiskLevel.READ_ONLY,

    # -- testo/matematica: quasi tutte pure funzioni di calcolo/trasformazione ----------
    "ASK_QUESTION": RiskLevel.READ_ONLY,
    "TRANSLATE_TEXT": RiskLevel.READ_ONLY,
    "CALCULATE": RiskLevel.READ_ONLY,
    "CONVERT_UNITS": RiskLevel.READ_ONLY,
    "SUMMARIZE_CLIPBOARD": RiskLevel.READ_ONLY,
    "CLIPBOARD_READ": RiskLevel.READ_ONLY,
    "CLIPBOARD_WRITE": RiskLevel.LOCAL_REVERSIBLE,
    "TRANSLATE_CLIPBOARD": RiskLevel.READ_ONLY,
    "EMPTY_CLIPBOARD": RiskLevel.LOCAL_REVERSIBLE,
    "COUNT_WORDS": RiskLevel.READ_ONLY,
    "CONVERT_CASE": RiskLevel.READ_ONLY,
    "GENERATE_PASSWORD": RiskLevel.READ_ONLY,
    "PROOFREAD_TEXT": RiskLevel.READ_ONLY,
    "SUMMARIZE_TEXT": RiskLevel.READ_ONLY,
    "DETECT_LANGUAGE": RiskLevel.READ_ONLY,
    "EXTRACT_URLS_FROM_TEXT": RiskLevel.READ_ONLY,
    "CONVERT_NUMBER_TO_WORDS": RiskLevel.READ_ONLY,
    "CONVERT_ROMAN_NUMERAL": RiskLevel.READ_ONLY,
    "CONVERT_MORSE_CODE": RiskLevel.READ_ONLY,
    "CALCULATE_BMI": RiskLevel.READ_ONLY,
    "CALCULATE_TIP": RiskLevel.READ_ONLY,
    "CALCULATE_AGE": RiskLevel.READ_ONLY,
    "CALCULATE_DISCOUNT": RiskLevel.READ_ONLY,
    "IS_PRIME": RiskLevel.READ_ONLY,
    "FIBONACCI": RiskLevel.READ_ONLY,
    "GCD_LCM": RiskLevel.READ_ONLY,
    "CALCULATE_PERCENTAGE": RiskLevel.READ_ONLY,
    "RANDOM_NUMBER": RiskLevel.READ_ONLY,

    # -- fun/misc: sempre sola lettura o generazione locale effimera --------------------
    "TELL_JOKE": RiskLevel.READ_ONLY,
    "ROLL_DICE": RiskLevel.READ_ONLY,
    "FLIP_COIN": RiskLevel.READ_ONLY,
    "RANDOM_QUOTE": RiskLevel.READ_ONLY,
    "RANDOM_FACT": RiskLevel.READ_ONLY,
    "MAGIC_8_BALL": RiskLevel.READ_ONLY,
    "CHOOSE_RANDOM": RiskLevel.READ_ONLY,
    "ROCK_PAPER_SCISSORS": RiskLevel.READ_ONLY,
    "GET_SUNRISE_SUNSET": RiskLevel.READ_ONLY,
    "GET_MOON_PHASE": RiskLevel.READ_ONLY,
    "CONVERT_CURRENCY": RiskLevel.READ_ONLY,
    "GET_NEXT_HOLIDAY": RiskLevel.READ_ONLY,

    "RESEARCH": RiskLevel.READ_ONLY,

    # -- comunicazione: inviare un messaggio e' l'unica azione visibile a terzi ---------
    "CHITCHAT": RiskLevel.READ_ONLY,
    "SAVE_CONTACT": RiskLevel.LOCAL_REVERSIBLE,
    "LIST_CONTACTS": RiskLevel.READ_ONLY,
    "SEND_WHATSAPP": RiskLevel.EXTERNAL_ACTION,
    "SEND_EMAIL": RiskLevel.EXTERNAL_ACTION,

    # -- skill registrate direttamente in JakeCore (non nel catalogo, vedi jake_core.py) --
    "LIST_MODELS": RiskLevel.READ_ONLY,
    "SET_MODEL": RiskLevel.LOCAL_REVERSIBLE,
    "LEARN_COMMAND": RiskLevel.LOCAL_REVERSIBLE,
    "LIST_LEARNED": RiskLevel.READ_ONLY,
    "FORGET_LEARNED": RiskLevel.DESTRUCTIVE,
    "CORRECT_LAST": RiskLevel.LOCAL_REVERSIBLE,
    "REPEAT_LAST": RiskLevel.READ_ONLY,
    "HELP": RiskLevel.READ_ONLY,
    "STOP_TALKING": RiskLevel.READ_ONLY,
    "PAUSE_LISTENING": RiskLevel.LOCAL_REVERSIBLE,
    "SET_PRIVATE_MODE": RiskLevel.LOCAL_REVERSIBLE,
    # LOCAL_REVERSIBLE apposta, non piu' su: un kill switch che chiedesse conferma prima di
    # fermare qualcosa (needs_central_confirmation scatta da DESTRUCTIVE in su, vedi sopra)
    # sarebbe inutile in un'emergenza - fermare/riavviare e' sempre annullabile, l'una con
    # l'altra (F1, vedi skills/kill_switch.py).
    "KILL_SWITCH": RiskLevel.LOCAL_REVERSIBLE,
    "RESET_KILL_SWITCH": RiskLevel.LOCAL_REVERSIBLE,
    "START_DICTATION": RiskLevel.LOCAL_REVERSIBLE,
    "STOP_DICTATION": RiskLevel.LOCAL_REVERSIBLE,
    "CREATE_SKILL": RiskLevel.ADMIN,  # la Skill Forge scrive ed espone codice eseguibile nuovo
    "LIST_CREATED_SKILLS": RiskLevel.READ_ONLY,
    "DELETE_CREATED_SKILL": RiskLevel.DESTRUCTIVE,
    "SET_NOTIFICATION_MODE": RiskLevel.LOCAL_REVERSIBLE,
    "GET_NOTIFICATION_MODE": RiskLevel.READ_ONLY,
    # READ_ONLY apposta: la skill stessa non muta mai nulla, propone solo una busta
    # CONFIRMATION_REQUIRED (skills/undo.py) - e' l'intent compensatorio dentro quella busta
    # (spesso DESTRUCTIVE, es. DELETE_PATH) a passare comunque da PolicyEngine per conto suo.
    "UNDO_LAST_ACTION": RiskLevel.READ_ONLY,
    # F7.1.2 (Companion Mobile MVP): autorizza un dispositivo NUOVO a controllare Jake con gli
    # stessi privilegi dell'utente - equivalente a consegnare un secondo companion_token.
    # ADMIN, non in SELF_CONFIRMING_INTENTS: passa dal gate centrale come ogni altra azione ADMIN,
    # quindi chiede la passphrase se ne e' stata configurata una (v5.4/5.5), esattamente come
    # SET_POWER_PLAN/RUN_COMMAND - nessuna eccezione per questo intent.
    "APPROVE_PAIRING": RiskLevel.ADMIN,
}


# Skill che gia' implementano una propria richiesta di conferma su misura, con controlli di
# precondizione (esiste il file? il percorso e' protetto?) PRIMA di chiedere, e un messaggio
# specifico (il percorso vero, il nome del processo...) invece di uno generico. Per queste il
# gate centrale (vedi JakeCore._resolve_and_execute, core/jake_core.py) non deve intervenire:
# altrimenti l'utente vedrebbe un "Confermi questa azione?" spoglio PRIMA che la skill controlli
# se l'azione e' persino possibile, perdendo il messaggio piu' preciso che gia' esiste.
SELF_CONFIRMING_INTENTS: frozenset[str] = frozenset({
    "DELETE_PATH", "RUN_COMMAND", "RUN_PYTHON_SCRIPT", "KILL_PROCESS_BY_PORT",
    "CLOSE_APP", "EMPTY_RECYCLE_BIN", "SYSTEM_POWER", "CREATE_SKILL",
    "CLEAR_TEMP_FILES", "CLEAR_NOTES", "PURGE_OLD_HISTORY",
})


def needs_central_confirmation(intent: str) -> bool:
    """Vero se l'intent e' abbastanza rischioso (DESTRUCTIVE o superiore) da dover sempre
    passare da una conferma, e non e' gia' una di quelle che se la gestiscono da sole in modo
    piu' preciso (vedi SELF_CONFIRMING_INTENTS). E' il criterio con cui JakeCore popola
    always_confirm_intents all'avvio (vedi core/jake_core.py): prima quell'insieme andava
    riempito a mano in config.json skill per skill, ora chiudere il rischio e' automatico e non
    dipende dal ricordarsi di aggiornare la configurazione ogni volta che nasce una nuova skill
    pericolosa."""
    return is_at_least(risk_of(intent), RiskLevel.DESTRUCTIVE) and intent not in SELF_CONFIRMING_INTENTS


def needs_central_auth(intent: str) -> bool:
    """Vero se l'intent e' al livello di rischio piu' alto (ADMIN: spegnimento, esecuzione di
    codice, installazione di una capacita' auto-generata) e non si autogestisce gia' da solo.
    E' il gradino sopra needs_central_confirmation nel Permissions & Security Kernel (v5.4):
    ALLOW per READ_ONLY/LOCAL_REVERSIBLE/EXTERNAL_ACTION, CONFIRM per DESTRUCTIVE, REQUIRE_AUTH
    per ADMIN. L'enforcement vero (vedi core/auth_gate.py, v5.5) resta pero' opt-in: se l'utente
    non ha mai configurato una passphrase, questi stessi intent continuano a passare solo dalla
    conferma si'/no, non da un vicolo cieco senza via d'uscita."""
    return risk_of(intent) == RiskLevel.ADMIN and intent not in SELF_CONFIRMING_INTENTS


# Effetti DICHIARATI di un'azione su un'app qualsiasi (core/computer_use/sensitive_ui.py): non sono
# skill, sono il rischio che chi chiama attribuisce a un click/tasto ("questo invia", "questo elimina").
# Tenuti fuori da SKILL_RISK, che censisce solo skill reali.
DECLARED_UI_EFFECT_RISK: dict[str, RiskLevel] = {
    "UI_SEND": RiskLevel.EXTERNAL_ACTION,
    "UI_SUBMIT": RiskLevel.EXTERNAL_ACTION,
    "UI_UPLOAD": RiskLevel.EXTERNAL_ACTION,
    "UI_DELETE": RiskLevel.DESTRUCTIVE,
    "UI_PURCHASE": RiskLevel.DESTRUCTIVE,  # spende denaro: irreversibile
}


def risk_of(intent: str) -> RiskLevel:
    """Livello di rischio di un intent. Una skill non ancora censita qui (un plugin di terze
    parti, o una skill scritta dalla Skill Forge) ricade su ADMIN per difetto: e' la scelta
    piu' prudente finche' un umano non la classifica esplicitamente, invece di trattare per
    errore qualcosa di potenzialmente pericoloso come se fosse a sola lettura."""
    if intent in SKILL_RISK:
        return SKILL_RISK[intent]
    return DECLARED_UI_EFFECT_RISK.get(intent, RiskLevel.ADMIN)
