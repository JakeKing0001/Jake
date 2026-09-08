"""Traduzione dei SkillResult in frasi italiane da leggere/mostrare (estratto da JakeCore
nella v3.0: era diventato meta' del file). Le skill di terze parti possono fornire un
format_result() proprio, usato come ultimo ripiego."""
from core.skill_result import SkillResult


def format_plan_outcome(outcome, total_steps: int, registry=None) -> str:
    lines = []
    for step_outcome in outcome.completed:
        label = step_outcome.step.description or step_outcome.step.intent
        lines.append(f"- fatto: {label}")

    if outcome.success:
        lines.insert(0, f"Ho completato i {len(outcome.completed)} passi richiesti:")
        return "\n".join(lines)

    stopped = outcome.stopped_step
    label = stopped.step.description or stopped.step.intent
    if stopped.result.error == "CONFIRMATION_REQUIRED":
        lines.append(f"- in pausa: {label}: {stopped.result.data.get('message', 'richiede conferma')}")
        lines.append("Chiedimelo singolarmente per confermare questo passo.")
    else:
        lines.append(f"- fallito: {label}: {format_skill_result(stopped.step.intent, stopped.result, registry)}")
        if outcome.rolled_back:
            undone = ", ".join(o.step.description or o.step.intent for o in outcome.rolled_back)
            lines.append(f"Ho annullato i passi precedenti per sicurezza: {undone}.")

    lines.insert(0, f"Piano interrotto dopo {len(outcome.completed)} passi completati su {total_steps}:")
    return "\n".join(lines)


_NOT_FOUND_BY_INTENT = {
    "LIST_SMART_DEVICES": "Non ho trovato nessun dispositivo smart home.",
    "RECALL": "Non ho trovato nulla su questo argomento.",
    "FORGET": "Non trovo nulla da dimenticare con quel nome.",
    "FIND_FILE": "Non ho trovato nessun file corrispondente.",
    "SEARCH_FILES": "Non ho trovato nessun file corrispondente.",
    "SEMANTIC_SEARCH_FILES": "Non ho trovato nessun file corrispondente.",
    "HYBRID_SEARCH_FILES": "Non ho trovato nessun file corrispondente.",
    "WEB_SEARCH": "Non ho trovato una risposta rapida per questa ricerca.",
    "GET_NEWS": "Non ho trovato notizie su questo argomento.",
    "LIST_TRIGGERS": "Non hai trigger impostati.",
    "LIST_REMINDERS": "Non hai promemoria in programma.",
    "LIST_NOTES": "Non hai ancora nessun appunto.",
    "LIST_TODOS": "Non hai nessuna attività in sospeso nella todo list.",
    "GET_BATTERY_STATUS": "Questo computer non ha una batteria (probabilmente è un desktop).",
    "EXPORT_NOTES": "Non hai ancora nessun appunto.",
    "CLEAR_NOTES": "Non hai ancora nessun appunto.",
    "GIT_DIFF": "Nessuna modifica in sospeso.",
    "GET_WIFI_STATUS": "Non risulti connesso a nessuna rete Wi-Fi.",
    "LIST_OPEN_WINDOWS": "Non trovo nessuna finestra aperta.",
    "LIST_STARTUP_APPS": "Non ho trovato programmi corrispondenti.",
    "LIST_INSTALLED_APPS": "Non ho trovato programmi corrispondenti.",
    "LIST_RECENT_FILES": "Non ho trovato file aperti di recente.",
    "FIND_LARGE_FILES": "Non ho trovato nulla di rilevante in quella cartella.",
    "FIND_DUPLICATE_FILES": "Non ho trovato nulla di rilevante in quella cartella.",
    "EXTRACT_URLS_FROM_TEXT": "Non ho trovato nessun link in quel testo.",
    "GET_BROWSER_HISTORY": "Non ho trovato cronologia recente nel browser.",
    "READ_SCREEN": "Non ho trovato testo leggibile sullo schermo.",
    "GET_ACTIVE_WINDOW": "Non riesco a determinare la finestra attiva.",
    "RESEARCH": "Non ho trovato nulla, ne' sul web ne' nei file locali, su questo argomento.",
    "CANCEL_TIMER": "Non hai timer attivi.",
    "LIST_TIMERS": "Non hai timer attivi.",
    "CLICK_ELEMENT": "Non riesco a individuare quell'elemento sullo schermo.",
    "LIST_LEARNED": "Non mi hai ancora insegnato nessun comando.",
    "FORGET_LEARNED": "Non ho nessun comando imparato con quella frase.",
    "LIST_CREATED_SKILLS": "Non ho ancora creato nessuna capacità da solo.",
    "LIST_CONTACTS": "La rubrica è vuota.",
    "STOP_POMODORO": "Non c'è nessuna sessione pomodoro in corso.",
    "LIST_MODELS": "Non trovo modelli installati in Ollama.",
}


def _format_error(intent: str, result: SkillResult) -> str:
    data = result.data or {}
    error = result.error
    if error == "UNSUPPORTED_APP":
        app = data.get("app", "")
        return f"Non trovo nessuna applicazione chiamata {app}" if app else "Applicazione non specificata"
    if error == "LAUNCH_FAILED":
        return f"Errore nell'apertura di {data.get('app', data.get('path', ''))}"
    if error == "CONFIRMATION_REQUIRED":
        return data.get("message", "Confermi questa azione?")
    if error == "MISSING_PARAMETERS":
        if intent == "REMEMBER":
            return "Dimmi cosa devo ricordare: mi servono sia il nome che il contenuto."
        if intent == "FORGET":
            return "Dimmi cosa devo dimenticare."
        if intent == "SET_TIMER":
            return "Per quanto tempo? Dimmi ad esempio: timer di 5 minuti."
        if intent == "SET_REMINDER":
            return "Quando devo ricordartelo? Dimmi ad esempio: tra 10 minuti, oppure alle 18."
        return "Mancano delle informazioni per eseguire questa azione."
    if error == "NOT_FOUND":
        if intent in ("LIST_PROCESSES", "CLOSE_APP"):
            return f"Non trovo nessun processo con '{data.get('name', '')}' nel nome."
        if intent == "RUN_WORKFLOW":
            return f"Non ho un'automazione salvata chiamata '{data.get('name', '')}'."
        if intent == "SET_TRIGGER":
            return f"Non ho un'automazione salvata chiamata '{data.get('name', '')}': creala prima con un'automazione."
        if intent == "DELETE_TRIGGER":
            return f"Non ho nessun trigger chiamato '{data.get('name', '')}'."
        if intent in ("COMPLETE_TODO", "DELETE_TODO"):
            return f"Non trovo nessuna attività da fare che corrisponda a '{data.get('text', '')}'."
        if intent in ("SNOOZE_REMINDER", "DELETE_REMINDER"):
            return f"Non trovo nessun promemoria che corrisponda a '{data.get('text', '')}'."
        if intent == "SEARCH_NOTES":
            return f"Non trovo appunti che corrispondano a '{data.get('query', '')}'."
        if intent == "KILL_PROCESS_BY_PORT":
            return f"Nessun processo in ascolto sulla porta {data.get('port', '')}."
        if intent == "GET_ENVIRONMENT_VARIABLE":
            return f"La variabile d'ambiente '{data.get('name', '')}' non è impostata."
        if intent == "CLICK_TEXT":
            return f"Non trovo '{data.get('text', '')}' sullo schermo."
        if intent == "DELETE_CREATED_SKILL":
            return f"Non ho nessuna capacità creata da me che corrisponda a '{data.get('name', '')}'."
        if intent == "LINK_MEMORY":
            return f"Non trovo nessun ricordo chiamato '{data.get('key', '')}': salvalo prima con REMEMBER."
        if intent == "CONTROL_SMART_DEVICE":
            return f"Non trovo nessun dispositivo smart home chiamato '{data.get('name', '')}'."
        return _NOT_FOUND_BY_INTENT.get(intent, "Non ho trovato nulla.")
    if error == "INVALID_TIME":
        return f"'{data.get('at_time', '')}' non è un orario valido (usa HH:MM)."
    if error == "OCR_UNAVAILABLE":
        return "Non ho un motore OCR disponibile per la lingua di questo PC."
    if error == "VISION_UNAVAILABLE":
        return "Non riesco a vedere lo schermo in questo momento (verifica che il modello di visione sia installato: 'ollama pull qwen2.5vl:7b')."
    if error == "BROWSER_HISTORY_UNAVAILABLE":
        return "Non trovo la cronologia di Chrome o Edge su questo PC."
    if error == "VERIFICATION_FAILED":
        return f"L'azione sembrava riuscita ma la verifica successiva non conferma l'effetto su {data.get('path', '')}."
    if error == "PATH_NOT_FOUND":
        return f"Non trovo il percorso {data.get('path', '')}"
    if error == "PROTECTED_PATH":
        return f"Non posso modificare {data.get('path', '')}: è una cartella protetta."
    if error == "ALREADY_EXISTS":
        return f"Esiste già qualcosa in {data.get('path', '')}"
    if error == "OPERATION_FAILED":
        target = data.get("path") or data.get("text") or data.get("description") or data.get("title") or ""
        return f"Non sono riuscito a completare l'operazione su {target}" if target else "Non sono riuscito a completare l'operazione."
    if error == "RESULT_NOT_FOUND":
        return "Non so quale risultato aprire: prova prima a fare una ricerca."
    if error == "NEST_UNAVAILABLE":
        return "NEST non è disponibile su questo computer."
    if error == "NEST_ERROR":
        return f"NEST ha restituito un errore: {data.get('message', '')}"
    if error == "HOME_ASSISTANT_UNAVAILABLE":
        return "Home Assistant non è configurato: imposta home_assistant_url e home_assistant_token."
    if error == "HOME_ASSISTANT_ERROR":
        return "Home Assistant ha restituito un errore: controlla che l'indirizzo e il token siano corretti."
    if error == "NETWORK_UNAVAILABLE":
        return "Non ho accesso a internet in questo momento."
    if error == "OLLAMA_UNAVAILABLE":
        return "Non riesco a contattare Ollama in questo momento."
    if error == "MISSING_API_KEY":
        return f"Per usarlo devi configurare '{data.get('setting', '')}' in config/settings.json (vedi config/settings.example.json)."
    if error == "CITY_NOT_FOUND":
        return f"Non trovo la città {data.get('city', '')}"
    if error == "INVALID_URL":
        return f"'{data.get('url', '')}' non è un indirizzo web valido."
    if error == "CLIPBOARD_EMPTY":
        return "Gli appunti sono vuoti o non contengono testo."
    if error == "WINDOW_NOT_FOUND":
        title = data.get("title", "")
        return f"Non trovo nessuna finestra con '{title}' nel titolo." if title else "Non trovo una finestra attiva da chiudere."
    if error == "PLAN_FAILED":
        if intent == "LEARN_COMMAND":
            return "Non sono riuscito a capire cosa devo fare quando lo dici: prova a descriverlo in un altro modo."
        return "Non sono riuscito a scomporre questa richiesta in passi."
    if error == "POLICY_BLOCKED":
        return "Un passo di questa automazione è disabilitato dalla configurazione."
    if error == "BRIGHTNESS_UNAVAILABLE":
        return "Non riesco a regolare la luminosità su questo schermo."
    if error == "AUDIO_UNAVAILABLE":
        return "Non riesco a controllare il volume su questo computer."
    if error == "NOT_A_GIT_REPO":
        return f"{data.get('path', '')} non è un repository git."
    if error == "TIMEOUT":
        return "Il comando ha impiegato troppo tempo e l'ho interrotto."
    if error == "INVALID_EXPRESSION":
        return f"'{data.get('expression', '')}' non è un'espressione valida."
    if error == "INCOMPATIBLE_UNITS":
        return "Non posso convertire tra queste due unità di misura."
    if error == "HOST_UNREACHABLE":
        host = data.get("host", "quell'host")
        return f"Non riesco a raggiungere {host}."
    if error == "INVALID_JSON":
        return "Il testo fornito non è JSON valido."
    if error == "INVALID_DATE":
        return f"'{data.get('date') or data.get('birth_date', '')}' non è una data valida (usa GG/MM/AAAA)."
    if error == "INVALID_VALUE":
        return f"'{data.get('value', '')}' non è un valore valido."
    if error == "CURRENCY_NOT_FOUND":
        return "Non riconosco una di queste valute."
    if error == "NO_SELECTION":
        return "Non c'è nessun testo selezionato."
    if error == "CONTACT_NOT_FOUND":
        name = data.get("name", "")
        return f"Non ho i contatti di {name} in rubrica. Dimmi: salva il numero di {name}, e il numero."
    if error == "VOICE_ONLY":
        return "Questa funzione è disponibile solo in modalità vocale."
    if error == "FORGE_FAILED":
        return data.get("message") or "Non sono riuscito a creare la nuova capacità."
    if error == "BLOCKED":
        return data.get("message") or "Non eseguo questa azione: è nella lista di quelle bloccate per sicurezza."
    return "Si è verificato un errore durante l'esecuzione"


def _format_success(intent: str, result: SkillResult, registry=None) -> str | None:
    data = result.data or {}
    if intent == "REMEMBER":
        return f"Ok, ricorderò che {data['key']} è {data['value']}."
    if intent == "RECALL":
        def _entry_line(entry: dict) -> str:
            line = f"{entry['key']}: {entry['value']}"
            related = entry.get("related") or []
            if related:
                # Un salto nel grafo di conoscenza personale (v4.4): mostra anche cosa e'
                # collegato a questo ricordo, senza dover fare una RECALL separata per scoprirlo.
                extra = "; ".join(f"{r['predicate']} {r['key']}" for r in related if r.get("value") is not None)
                if extra:
                    line += f" ({extra})"
            return line
        formatted = "; ".join(_entry_line(entry) for entry in data["results"])
        return f"Ecco cosa ricordo: {formatted}"
    if intent == "FORGET":
        return f"Ho dimenticato {data['key']}."
    if intent == "LINK_MEMORY":
        return f"Ok, ho collegato {data['subject']} — {data['predicate']} — {data['object']}."
    if intent in ("OPEN_PATH", "OPEN_SEARCH_RESULT"):
        return f"Ho aperto {data['path']}"
    if intent == "CREATE_PATH":
        kind = "la cartella" if data.get("type") == "folder" else "il file"
        return f"Ho creato {kind} {data['path']}"
    if intent == "RENAME_PATH":
        return f"Ho rinominato {data['path']} in {data['new_path']}"
    if intent == "MOVE_PATH":
        return f"Ho spostato {data['path']} in {data['new_path']}"
    if intent == "DELETE_PATH":
        return f"Ho eliminato {data['path']}"
    if intent == "FIND_FILE":
        files = data["results"]
        return f"Ho trovato {len(files)} file: " + "; ".join(files)
    if intent in ("SEARCH_FILES", "SEMANTIC_SEARCH_FILES", "HYBRID_SEARCH_FILES"):
        formatted = "; ".join(f"{i}. {r['path']}" for i, r in enumerate(data["results"], start=1))
        if intent == "SEMANTIC_SEARCH_FILES":
            prefix = "Ecco cosa ho trovato per significato"
        elif intent == "HYBRID_SEARCH_FILES":
            prefix = "Ecco cosa ho trovato (ricerca combinata)"
        else:
            prefix = "Ecco cosa ho trovato"
        return f"{prefix}: {formatted}"
    if intent == "WEB_SEARCH":
        source = f" (fonte: {data['url']})" if data.get("url") else ""
        return f"{data['summary']}{source}"
    if intent == "GET_WEATHER":
        return f"A {data['city']}: {data['description']}, {data.get('temperature')}°C (percepiti {data.get('feels_like')}°C)"
    if intent == "GET_NEWS":
        formatted = "; ".join(f"{h['title']} ({h['source']})" for h in data["headlines"])
        return f"Ultime notizie: {formatted}"
    if intent == "OPEN_URL":
        return f"Ho aperto {data['url']}"
    if intent == "CLIPBOARD_READ":
        return f"Negli appunti c'è: {data['text']}"
    if intent == "CLIPBOARD_WRITE":
        return "Ho copiato il testo negli appunti."
    if intent == "FOCUS_WINDOW":
        return f"Ho attivato la finestra {data['title']}"
    if intent == "MINIMIZE_WINDOW":
        return f"Ho minimizzato la finestra {data['title']}"
    if intent == "SET_VOLUME":
        labels = {"up": "alzato", "down": "abbassato", "mute": "silenziato"}
        return f"Volume {labels.get(data['action'], 'modificato')}."
    if intent == "LIST_PROCESSES":
        formatted = ", ".join(f"{p['name']} (PID {p['pid']})" for p in data["processes"])
        return f"Processi trovati: {formatted}"
    if intent == "CLOSE_APP":
        return "Ho chiuso: " + ", ".join(data["closed"])
    if intent == "SAVE_WORKFLOW":
        return f"Ho salvato l'automazione '{data['name']}' con {data['step_count']} passi."
    if intent == "RUN_WORKFLOW":
        return format_plan_outcome(data["outcome"], data["total_steps"], registry)
    if intent == "SET_TRIGGER":
        return f"Ok, '{data['workflow_name']}' partira' da sola in base al trigger '{data['name']}'."
    if intent == "LIST_TRIGGERS":
        formatted = "; ".join(f"{t['name']} -> {t['workflow_name']} ({t['type']})" for t in data["triggers"])
        return f"Trigger impostati: {formatted}"
    if intent == "DELETE_TRIGGER":
        return f"Ho rimosso il trigger '{data['name']}'."
    if intent == "SET_REMINDER":
        return f"Ok, alle {data['due_at_local']} ti ricorderò: {data['text']}."
    if intent == "LIST_REMINDERS":
        formatted = "; ".join(f"{r['due_at_local']}: {r['text']}" for r in data["reminders"])
        return f"Promemoria in programma: {formatted}"
    if intent == "TAKE_SCREENSHOT":
        return f"Screenshot salvato in {data['path']}"
    if intent == "READ_SCREEN":
        suffix = " (troncato)" if data.get("truncated") else ""
        return f"Sullo schermo leggo{suffix}: {data['text']}"
    if intent == "DESCRIBE_SCREEN":
        return data["description"]
    if intent == "ADD_NOTE":
        return f"Appuntato: {data['text']}"
    if intent == "LIST_NOTES":
        return "Ultimi appunti:\n" + "\n".join(data["notes"])
    if intent == "GET_BROWSER_HISTORY":
        formatted = "; ".join(f"{e['title']} ({e['url']})" for e in data["entries"])
        return f"Cronologia recente: {formatted}"
    if intent == "GET_ACTIVE_WINDOW":
        return f"Stai usando: {data['title']}"
    if intent == "CLICK_MOUSE":
        return f"Cliccato in ({data['x']}, {data['y']})"
    if intent == "MOVE_MOUSE":
        return f"Mouse spostato in ({data['x']}, {data['y']})"
    if intent == "TYPE_TEXT":
        return "Scritto."
    if intent == "PRESS_KEY":
        return f"Premuto {data['keys']}."
    if intent == "RESEARCH":
        return data["synthesis"]
    if intent == "BUILD_SEMANTIC_INDEX":
        return data["summary"]
    if intent == "LIST_MODELS":
        return "Modelli installati: " + ", ".join(data["models"])
    if intent == "SET_MODEL":
        return f"Ok, ora uso il modello {data['model']}."
    if intent == "ADD_TODO":
        return f"Aggiunto alla lista delle cose da fare: {data['text']}."
    if intent == "LIST_TODOS":
        formatted = "; ".join(f"{t['id']}. {t['text']}" for t in data["todos"])
        return f"Cose da fare: {formatted}"
    if intent == "COMPLETE_TODO":
        return f"Segnato come fatto: {data['text']}."
    if intent == "SYSTEM_POWER":
        labels = {"shutdown": "Spengo il computer.", "restart": "Riavvio il computer.", "sleep": "Metto il computer in sospensione.", "lock": "Ho bloccato lo schermo."}
        return labels.get(data["action"], "Fatto.")
    if intent == "SET_BRIGHTNESS":
        return f"Luminosità impostata al {data['level']}%."
    if intent == "EMPTY_RECYCLE_BIN":
        return "Ho svuotato il cestino."
    if intent == "LIST_WIFI_NETWORKS":
        return "Reti Wi-Fi trovate: " + ", ".join(data["networks"])
    if intent == "GIT_STATUS":
        lines = data["status_lines"]
        return f"Stato di {data['path']}:\n" + ("\n".join(lines) if lines else "Nessuna modifica in sospeso.")
    if intent == "GIT_PULL":
        return f"Pull completato su {data['path']}." + (f"\n{data['message']}" if data.get("message") else "")
    if intent == "OPEN_IN_EDITOR":
        return f"Ho aperto {data['path']} in Visual Studio Code."
    if intent in ("RUN_COMMAND", "RUN_PYTHON_SCRIPT"):
        output = data.get("output") or "(nessun output)"
        label = "Comando eseguito" if intent == "RUN_COMMAND" else "Script eseguito"
        return f"{label} (codice {data['return_code']}):\n{output}"
    if intent == "MEDIA_CONTROL":
        labels = {"play_pause": "Play/pausa.", "next": "Traccia successiva.", "previous": "Traccia precedente."}
        return labels.get(data["action"], "Fatto.")
    if intent == "TELL_JOKE":
        return data["joke"]
    if intent == "ROLL_DICE":
        rolls = ", ".join(str(r) for r in data["rolls"])
        return f"Hai tirato: {rolls} (totale {data['total']})"
    if intent == "FLIP_COIN":
        return f"È uscito: {data['result']}"
    if intent == "ASK_QUESTION":
        return data["answer"]
    if intent in ("TRANSLATE_TEXT", "TRANSLATE_CLIPBOARD"):
        return data["translation"]
    if intent == "CALCULATE":
        return f"{data['expression']} = {data['result']}"
    if intent == "CONVERT_UNITS":
        return f"{data['value']} {data['from_unit']} corrispondono a {data['result']} {data['to_unit']}"
    if intent in ("SUMMARIZE_CLIPBOARD", "SUMMARIZE_TEXT"):
        return data["summary"]
    if intent == "GET_BATTERY_STATUS":
        stato = "in carica" if data["plugged"] else "a batteria"
        return f"Batteria al {data['percent']}%, {stato}."
    if intent == "GET_CPU_USAGE":
        return f"La CPU è al {data['percent']}%."
    if intent == "GET_MEMORY_USAGE":
        return f"RAM in uso: {data['percent']}% ({data['used_gb']} GB su {data['total_gb']} GB)."
    if intent == "GET_DISK_USAGE":
        return f"Disco {data['drive']}: {data['free_gb']} GB liberi su {data['total_gb']} GB."
    if intent == "GET_UPTIME":
        return f"Il computer è acceso da {data['hours']} ore e {data['minutes']} minuti."
    if intent == "GET_SYSTEM_INFO":
        return f"{data['os']}, computer '{data['hostname']}', architettura {data['architecture']}."
    if intent == "GET_LOCAL_IP":
        return f"Il tuo indirizzo IP locale è {data['ip']}."
    if intent == "GET_PUBLIC_IP":
        return f"Il tuo indirizzo IP pubblico è {data['ip']}."
    if intent == "CLEAR_TEMP_FILES":
        return f"Eliminati {data['deleted']} elementi temporanei."
    if intent == "RESTART_EXPLORER":
        return "Ho riavviato Esplora risorse."
    if intent == "FLUSH_DNS":
        return "Cache DNS svuotata."
    if intent == "LIST_STARTUP_APPS":
        return "Si avviano con Windows: " + ", ".join(data["apps"])
    if intent == "LIST_INSTALLED_APPS":
        return "Programmi trovati: " + ", ".join(data["apps"])
    if intent == "SET_POWER_PLAN":
        return f"Piano di alimentazione impostato su {data['plan']}."
    if intent == "TOGGLE_DARK_MODE":
        return "Tema scuro attivato." if data["enabled"] else "Tema chiaro attivato."
    if intent == "GET_WIFI_STATUS":
        segnale = f", segnale {data['signal_percent']}%" if data.get("signal_percent") is not None else ""
        return f"Connesso a '{data['ssid']}'{segnale}."
    if intent == "PING_HOST":
        return f"{data['host']} risponde (latenza media {data['latency']})."
    if intent == "TRACE_ROUTE":
        return f"Percorso verso {data['host']}: {data['hop_count']} salti."
    if intent == "CHECK_WEBSITE_STATUS":
        if data["online"]:
            return f"{data['url']} è raggiungibile (codice {data.get('status_code', '')})."
        return f"{data['url']} non è raggiungibile in questo momento."
    if intent == "COMPRESS_PATH":
        return f"Ho creato l'archivio {data['archive_path']}"
    if intent == "EXTRACT_ARCHIVE":
        return f"Ho estratto l'archivio in {data['destination']}"
    if intent == "GET_FILE_INFO":
        tipo = "cartella" if data["is_folder"] else "file"
        return f"{tipo.capitalize()} {data['path']}: {data['size_kb']} KB, modificato il {data['modified']}."
    if intent == "COUNT_WORDS_IN_FILE":
        return f"{data['path']} contiene {data['words']} parole."
    if intent == "READ_FILE_TEXT":
        suffisso = " (troncato)" if data.get("truncated") else ""
        return f"Contenuto{suffisso}:\n{data['text']}"
    if intent == "DUPLICATE_FILE":
        return f"Ho duplicato il file in {data['destination']}"
    if intent == "GET_FOLDER_SIZE":
        return f"{data['path']} occupa {data['size_mb']} MB."
    if intent == "LIST_OPEN_WINDOWS":
        return "Finestre aperte: " + ", ".join(data["titles"])
    if intent == "MAXIMIZE_WINDOW":
        return f"Ho massimizzato {data['title']}"
    if intent == "RESTORE_WINDOW":
        return f"Ho ripristinato {data['title']}"
    if intent in ("SNAP_WINDOW_LEFT", "SNAP_WINDOW_RIGHT", "SWITCH_NEXT_WINDOW", "MINIMIZE_ALL_WINDOWS"):
        return "Fatto."
    if intent == "SET_WINDOW_ALWAYS_ON_TOP":
        return f"{data['title']} resterà sempre in primo piano."
    if intent == "RESIZE_WINDOW":
        return f"Ho ridimensionato {data['title']} a {data['width']}x{data['height']}."
    if intent == "SNOOZE_REMINDER":
        return f"Rinviato di {data['minutes']} minuti: {data['text']}."
    if intent == "DELETE_REMINDER":
        return f"Ho cancellato il promemoria: {data['text']}."
    if intent == "SET_DAILY_REMINDER":
        return f"Ok, ogni giorno alle {data['at_time']} ti ricorderò: {data['text']}."
    if intent == "START_POMODORO":
        return f"Sessione avviata: ti avviserò tra {data['minutes']} minuti."
    if intent == "STOP_POMODORO":
        return "Sessione pomodoro interrotta."
    if intent == "DELETE_TODO":
        return f"Ho rimosso dalla lista: {data['text']}."
    if intent == "SEARCH_NOTES":
        return "Ho trovato:\n" + "\n".join(data["notes"])
    if intent == "EXPORT_NOTES":
        return f"Ho esportato gli appunti in {data['path']}"
    if intent == "CLEAR_NOTES":
        return "Ho cancellato tutti gli appunti."
    if intent == "GIT_LOG":
        return f"Ultimi commit su {data['path']}:\n" + "\n".join(data["commits"])
    if intent == "GIT_BRANCH":
        return f"Sei sul branch '{data['branch']}'."
    if intent == "GIT_DIFF":
        return f"Modifiche in {data['path']}:\n{data['summary']}"
    if intent == "FORMAT_JSON":
        return data["formatted"]
    if intent == "COUNT_LINES_OF_CODE":
        return f"{data['lines']} righe in {data['files']} file, in {data['path']}."
    if intent == "GENERATE_UUID":
        return data["uuid"]
    if intent == "CHECK_PORT_IN_USE":
        if data["in_use"]:
            return f"La porta {data['port']} è occupata da '{data['process']}' (PID {data['pid']})."
        return f"La porta {data['port']} è libera."
    if intent == "KILL_PROCESS_BY_PORT":
        return f"Ho terminato '{data['process']}' sulla porta {data['port']}."
    if intent == "RANDOM_QUOTE":
        return data["quote"]
    if intent == "RANDOM_FACT":
        return data["fact"]
    if intent == "MAGIC_8_BALL":
        return data["answer"]
    if intent == "CHOOSE_RANDOM":
        return f"Ho scelto: {data['choice']}"
    if intent == "ROCK_PAPER_SCISSORS":
        return f"Tu: {data['user_choice']}, io: {data['jake_choice']} — {data['outcome']}."
    if intent == "COUNT_WORDS":
        return f"{data['words']} parole, {data['characters']} caratteri."
    if intent in ("CONVERT_CASE", "PROOFREAD_TEXT"):
        return data["text"]
    if intent == "GENERATE_PASSWORD":
        return data["password"]
    if intent == "DETECT_LANGUAGE":
        return f"È scritto in {data['language']}."
    if intent == "EXTRACT_URLS_FROM_TEXT":
        return "Link trovati: " + ", ".join(data["urls"])
    if intent == "CONVERT_NUMBER_TO_WORDS":
        return data["words"]
    if intent in ("CONVERT_ROMAN_NUMERAL", "CONVERT_MORSE_CODE"):
        return data["result"]
    if intent == "GET_DAY_OF_WEEK":
        return f"Il {data['date']} è {data['weekday']}."
    if intent == "DAYS_UNTIL":
        return f"Mancano {data['days']} giorni al {data['date']}."
    if intent == "GET_WEEK_NUMBER":
        return f"È la settimana numero {data['week_number']} dell'anno."
    if intent == "CONVERT_TIMEZONE":
        return f"Sono le {data['result']} a {data['to_zone']}."
    if intent == "PRINT_FILE":
        return f"Ho inviato {data['path']} in stampa."
    if intent == "CALCULATE_BMI":
        return f"BMI {data['bmi']} ({data['category']})."
    if intent == "CALCULATE_TIP":
        return f"Mancia {data['tip']}, totale {data['total']}, {data['per_person']} a testa su {data['people']} persone."
    if intent == "CALCULATE_AGE":
        return f"Hai {data['age']} anni."
    if intent == "CALCULATE_DISCOUNT":
        return f"Prezzo finale: {data['final_price']} (risparmi {data['savings']})."
    if intent == "LIST_RECENT_FILES":
        return "File recenti: " + ", ".join(data["files"])
    if intent == "OPEN_INCOGNITO_WINDOW":
        return "Ho aperto una finestra in incognito."
    if intent == "EMPTY_CLIPBOARD":
        return "Ho svuotato gli appunti."
    if intent == "IS_PRIME":
        return f"{data['number']} è {'primo' if data['is_prime'] else 'non primo'}."
    if intent == "FIBONACCI":
        return f"Il {data['n']}-esimo numero di Fibonacci è {data['result']}."
    if intent == "GCD_LCM":
        return f"MCD {data['gcd']}, mcm {data['lcm']}."
    if intent == "CALCULATE_PERCENTAGE":
        return f"Il {data['percent']}% di {data['value']} è {data['result']}."
    if intent == "RANDOM_NUMBER":
        return f"Numero casuale: {data['result']}"
    if intent == "CHECK_PASSWORD_STRENGTH":
        return f"Sicurezza password: {data['strength']}."
    if intent == "CHECK_FILE_HASH":
        return f"SHA-256 di {data['path']}: {data['sha256']}"
    if intent == "GET_SUNRISE_SUNSET":
        return f"A {data['city']} oggi: alba alle {data['sunrise']}, tramonto alle {data['sunset']}."
    if intent == "GET_MOON_PHASE":
        return f"Fase lunare attuale: {data['phase']}."
    if intent == "CONVERT_CURRENCY":
        return f"{data['amount']} {data['from_currency']} corrispondono a {data['result']} {data['to_currency']}"
    if intent == "GET_NEXT_HOLIDAY":
        return f"La prossima festività è {data['name']} il {data['date']} (tra {data['days_until']} giorni)."
    if intent == "GET_SCREEN_RESOLUTION":
        return f"Risoluzione schermo: {data['width']}x{data['height']}."
    if intent == "GET_GPU_INFO":
        return "Scheda video: " + ", ".join(data["gpus"])
    if intent == "GET_DNS_SERVERS":
        return "Server DNS: " + ", ".join(data["servers"])
    if intent == "GET_ENVIRONMENT_VARIABLE":
        return f"{data['name']} = {data['value']}"
    if intent == "GET_MAC_ADDRESS":
        return f"Indirizzo MAC: {data['mac_address']}"
    if intent == "LIST_DRIVES":
        formatted = "; ".join(f"{d['drive']} ({d['free_gb']}/{d['total_gb']} GB liberi)" for d in data["drives"])
        return f"Unità disco: {formatted}"
    if intent == "LIST_SMART_DEVICES":
        formatted = "; ".join(f"{d['name']}: {d['state']}" for d in data["devices"])
        return f"Dispositivi smart home: {formatted}"
    if intent == "CONTROL_SMART_DEVICE":
        verb = {"on": "acceso", "off": "spento", "toggle": "alternato"}.get(data["action"], "cambiato")
        return f"Ho {verb} {data['name']}."
    if intent == "FIND_LARGE_FILES":
        formatted = "; ".join(f"{f['path']} ({f['size_mb']} MB)" for f in data["files"])
        return f"File grandi trovati: {formatted}"
    if intent == "FIND_DUPLICATE_FILES":
        return f"Trovati {len(data['duplicate_groups'])} gruppi di file duplicati."

    # ---- v3.0 -----------------------------------------------------------------------
    if intent == "SEARCH_IN_BROWSER":
        site_labels = {"google": "Google", "youtube": "YouTube", "amazon": "Amazon", "wikipedia": "Wikipedia", "github": "GitHub", "maps": "Google Maps", "images": "Google Immagini"}
        site = site_labels.get(data["site"], data["site"])
        return f"Cerco {data['query']} su {site}." if data.get("query") else f"Ho aperto {site}."
    if intent == "PLAY_MEDIA":
        if data["service"] == "youtube":
            what = data.get("title") or data["query"]
            return f"Metto {what} su YouTube." if data.get("autoplay") else f"Ho aperto i risultati per {data['query']} su YouTube."
        return f"Ho aperto {data['query']} su Spotify: scegli e premi play, o dimmi 'play'."
    if intent == "SET_TIMER":
        label = f" per {data['label']}" if data.get("label") else ""
        return f"Timer di {data['duration']}{label} avviato."
    if intent == "CANCEL_TIMER":
        return "Timer annullato."
    if intent == "LIST_TIMERS":
        formatted = "; ".join(f"{t['label']}: mancano {t['remaining']}" for t in data["timers"])
        return f"Timer attivi: {formatted}"
    if intent == "CLICK_TEXT":
        return f"Ho cliccato su '{data['text']}'."
    if intent == "CLICK_ELEMENT":
        return "Fatto, ho cliccato."
    if intent == "SCROLL":
        return ""
    if intent == "SET_VOLUME_LEVEL":
        return f"Volume al {data['level']}%."
    if intent == "GET_VOLUME_LEVEL":
        muted = " (silenziato)" if data.get("muted") else ""
        return f"Il volume è al {data['level']}%{muted}."
    if intent == "READ_SELECTION":
        return data["text"]
    if intent == "CLOSE_WINDOW":
        return f"Ho chiuso {data['title']}."
    if intent == "CHITCHAT":
        return data["reply"]
    if intent == "SAVE_CONTACT":
        parts = [p for p in (data.get("phone"), data.get("email")) if p]
        return f"Salvato in rubrica: {data['name']}, {', '.join(parts)}."
    if intent == "LIST_CONTACTS":
        formatted = "; ".join(
            f"{c['name']}: {c.get('phone') or ''} {c.get('email') or ''}".strip() for c in data["contacts"]
        )
        return f"In rubrica ho: {formatted}"
    if intent == "SEND_WHATSAPP":
        return f"Ho preparato il messaggio per {data['contact']} su WhatsApp: controlla e premi invio."
    if intent == "SEND_EMAIL":
        return f"Ho aperto la bozza dell'email per {data['to']}: rileggila e inviala."
    if intent == "LEARN_COMMAND":
        return f"Imparato. Quando dici «{data['phrase']}» {data['description']}."
    if intent == "LIST_LEARNED":
        formatted = "; ".join(f"«{c['phrase']}» → {c['description']}" for c in data["commands"])
        auto = data.get("auto_count") or 0
        suffix = f" Inoltre ho imparato da solo {auto} frasi dai comandi passati." if auto else ""
        return f"Comandi che mi hai insegnato: {formatted}.{suffix}"
    if intent == "FORGET_LEARNED":
        return f"Ho dimenticato il comando «{data['phrase']}»."
    if intent == "CORRECT_LAST":
        return data["response"]
    if intent == "REPEAT_LAST":
        return data["text"]
    if intent == "HELP":
        return data["text"]
    if intent == "STOP_TALKING":
        return ""
    if intent == "PAUSE_LISTENING":
        return f"Ok, non ascolto per {data['minutes']} minuti. Per svegliarmi prima dì: Jake, svegliati."
    if intent == "SET_PRIVATE_MODE":
        return "Modalità privata attiva: non registro questa conversazione." if data["enabled"] else "Modalità privata disattivata: torno a registrare normalmente."
    if intent == "PURGE_OLD_HISTORY":
        removed = data.get("removed", 0)
        if not removed:
            return f"Non c'era nulla di più vecchio di {data['days']} giorni da eliminare."
        return f"Eliminate {removed} voci di cronologia più vecchie di {data['days']} giorni."
    if intent == "START_DICTATION":
        return "Dettatura attiva: scrivo tutto quello che dici. Dì «fine dettatura» per uscire."
    if intent == "STOP_DICTATION":
        return "Dettatura terminata."
    if intent == "CREATE_SKILL":
        example = data["examples"][0] if data.get("examples") else ""
        hint = f" Prova a dirmi: «{example}»." if example else ""
        return f"Fatto: ho imparato una nuova capacità. {data['description']}{hint}"
    if intent == "LIST_CREATED_SKILLS":
        formatted = "; ".join(f"{s.get('intent') or s['file']}: {s.get('description', '')}" for s in data["skills"])
        return f"Capacità che ho creato da solo: {formatted}"
    if intent == "DELETE_CREATED_SKILL":
        return f"Ho eliminato la capacità {data.get('intent') or data.get('file')}."
    if intent == "SET_NOTIFICATION_MODE":
        from core.notification_center import MODE_LABELS_IT, NotificationMode
        label = MODE_LABELS_IT.get(NotificationMode(data["mode"]), data["mode"])
        released = data.get("released") or []
        if not released:
            return f"Modalità {label} attiva."
        catch_up = " ".join(released)
        return f"Modalità {label} attiva. Nel frattempo: {catch_up}"
    if intent == "GET_NOTIFICATION_MODE":
        from core.notification_center import MODE_LABELS_IT, NotificationMode
        label = MODE_LABELS_IT.get(NotificationMode(data["mode"]), data["mode"])
        pending = data.get("pending", 0)
        if pending:
            noun = "notifica" if pending == 1 else "notifiche"
            return f"Modalità {label}: {pending} {noun} in attesa."
        return f"Modalità {label}."
    return None


def format_skill_result(intent: str, result: SkillResult, registry=None) -> str:
    if result is None:
        return "Si è verificato un errore durante l'esecuzione"
    if not result.success:
        return _format_error(intent, result)

    try:
        formatted = _format_success(intent, result, registry)
    except (KeyError, TypeError):
        formatted = None
    if formatted is not None:
        return formatted

    data = result.data or {}
    if "time" in data:
        return f"Attualmente sono le {data['time']}"
    if "date" in data:
        return f"La data di oggi è {data['date']}"
    if "app" in data:
        return f"Ho aperto {data['app']}"

    # Punto di estensione per plugin (v2.0): una skill di terze parti puo' fornire un
    # format_result(result) proprio, senza dover modificare questo file.
    skill = registry.get_skill(intent) if registry is not None else None
    format_result = getattr(skill, "format_result", None)
    if callable(format_result):
        try:
            return str(format_result(result))
        except Exception:
            pass

    return str(data)
