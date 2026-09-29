## 24. Prossima azione esatta

Aggiornato 29/09/2026 (le versioni precedenti restano nella cronologia Git). Sviluppo del 28/09 su master (#168-#190),
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
3. Utente, decisione: installer per F0.6.1 (MSIX, Inno Setup o portable). Fino ad allora F0.6.3-F0.6.6 restano
   `BLOCKED`.
4. Sviluppo, gap rimasti in ordine: F4 (rifinitura HUD/glass, orb e stati, GPU/batteria, secondo monitor e testo),
   F5 (separazione entita'/episodi/procedure, retrieval, controlli privacy), F6 (milestone e next action dei goal,
   suggerimenti, focus/meeting), F7 (coda offline, trasporto di sync, continuita' residua, remote wipe), F8
   (runtime degli agenti: loop e budget, eval e rollback), F0 (packaging/preflight/performance non legati
   all'installer).