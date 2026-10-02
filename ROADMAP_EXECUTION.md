## 24. Prossima azione esatta

Aggiornato 02/10/2026 (le versioni precedenti restano nella cronologia Git). PR #191: RAM ridotta (modelli Ollama secondari scaricati dopo 2 minuti, `low_memory`, server RVC chiuso dopo inattivita'), master di nuovo verde (lint, mypy e quattro letture dei ricordi sensibili cifrati), decisione installer F0.6.1, updater atomico, focus assistant collegato al pomodoro. Stato al 29/09: Sviluppo del 28/09 su master (#168-#190),
compresi provenienza dei messaggi companion nell'HUD (#184, F7.4.6), test dei pacchetti di terzi nella sandbox
(#185, F8.3), merge di PR #186-#189 e aggiornamento di #190 con correzione della logica di riparazione trascrizione
(e verificato con test_strongly_corrupted_or_garbled_uncertain_speech_still_asks_to_repeat), oltre al
fix di integrità goal/step per F6.4.3/F6.4.4.

1. Utente: gate hardware di F2 con `docs/f2-hardware-validation.md` (tre profili, interruzioni, sessione wake di 24 h,
   poi `python -m benchmarks.f2_hardware_session evaluate`). Solo con `PASS` F2 passa a `DONE`. Microfono dedicato e
   VRAM libera prima della prova (il 28/09 mattina: 55 MB liberi): il preflight lo segnala.
2. Utente, verifica a schermo: vetro vero dietro i pannelli su desktop scuri/chiari/colorati, orb 3D per stato,
   HUD su un secondo monitor a scala diversa; interruttore "Privato"; con il telefono: conferma che passa al telefono
   e ritorno al PC, notifiche solo sul dispositivo attivo, "Tu (da Telefono)" nella conversazione; "impara a ..." con
   specifica, permessi, installazione firmata e periodo di prova.
3. Decisione presa il 02/10/2026 (F0.6.1): installazione portable per utente con `setup.ps1`, senza admin ne'
   certificato. Rilanciarlo ripara (F0.6.3); `setup.ps1 -Uninstall` conserva `data\` e `config\settings.json`,
   `-PurgeData` li cancella solo su richiesta esplicita (F0.6.4); `python -m tools.updater` aggiorna in
   fast-forward su canale `stable` (ultimo tag v*) o `dev`, con controllo di salute e rollback (F0.6.6/F0.6.7).
   Restano aperti: firma verificata degli aggiornamenti (F0.6.5) e un pacchetto MSIX se servira' la distribuzione.
4. Sviluppo, gap rimasti in ordine: F4 (rifinitura HUD/glass, orb e stati, GPU/batteria, secondo monitor e testo),
   F5 (separazione entita'/episodi/procedure, retrieval, controlli privacy), F6 (milestone e next action dei goal,
   suggerimenti, focus/meeting), F7 (coda offline, trasporto di sync, continuita' residua, remote wipe), F8
   (runtime degli agenti: loop e budget, eval e rollback), F0 (packaging/preflight/performance non legati
   all'installer).

## Registro owner e stato dei pacchetti attivi

Questo registro riguarda l'incremento in corso; il catalogo storico completo resta nella cronologia Git.
`DOING` indica lavoro e verifiche ancora aperti, non il completamento della fase.

| Pacchetto | Owner logico | Stato |
|---|---|---|
| `F4.3` | Sviluppo HUD | `DOING` |
| `F4.7` | Sviluppo HUD / utente per monitor fisici | `DOING` |

## Incremento del 29/09/2026 — materiale HUD e regressioni

### F4.3 — Vetro, fallback e risorse

- Proseguite le modifiche locali al vetro: gradiente multistop, grana, riflesso speculare e ombra interna
  conservati, senza tipi QML non risolti (`OpacityMask`/`RadialGradient`) o texture inesistente.
- Superficie decorativa statica e ritagliata agli angoli; texture e cattura del backdrop si spengono quando
  il pannello e' nascosto o la qualita' scende. La tinta leggibile resta sempre presente.
- Test nativo `hud_glass_material`: carica e disegna gli stessi sorgenti QML dell'app, fallisce sui warning,
  verifica 48 combinazioni di fondo/stato/qualita'/modalita' vetro, transizioni, resize e rilascio delle risorse.
  CI configurata per pubblicare screenshot diagnostici e log CTest.
- Ancora da verificare: compositing DWM sul desktop reale e budget GPU/frame-time. Il rendering software
  offscreen non sostituisce queste prove e non chiude F4.

### F4.7 — Contrasto e scala del materiale

- Tinta e testo secondario regolati sul rendering effettivo: contrasto almeno 4,5 nell'area interna dei
  pannelli sui fondi di prova bianco, scuro e colorato. In contrasto elevato: nero pieno, bordo bianco
  anche con accento scuro, niente decorazioni.
- La stessa suite gira anche al 150% (`hud_glass_material_hidpi`); il controllo degli angoli impedisce
  regressioni a pannelli quadrati. Nessuna dichiarazione di supporto multi-monitor basata su questa simulazione.
- Ancora da verificare: spostamento fra monitor a DPI diversi, scala testo reale e accessibilita' dell'intero HUD.

Verifiche locali dell'incremento: build MSVC/Qt 6.7.3 riuscita, 5/5 CTest, 213 test Python HUD e test
della struttura roadmap superati, `qmllint` e lint Python dei file toccati puliti. Non eseguita la CI remota
ne' la suite Python completa del repository.

Prossima azione di sviluppo: proseguire F4 su orb/stati e budget GPU, mantenendo aperte le verifiche manuali
del punto 2; poi F5 nell'ordine sopra. I gate F2 e la decisione sull'installer non cambiano.
