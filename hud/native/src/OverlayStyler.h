#pragma once

#include <QObject>
#include <QQmlEngine>
#include <QQuickWindow>

// F4.2.1 ("Implementare trasparenza, click-through selettivo e no-activate"): l'unica
// manipolazione nativa Win32 necessaria per un vero overlay - Qt non espone nessuna delle tre
// proprieta' tramite API cross-platform (la trasparenza vera, `color: "transparent"` + finestra
// senza bordi, e' l'unica gia' ottenibile in puro QML). Richiede la HWND reale della finestra
// (QQuickWindow::winId()), quindi ogni metodo va chiamato SOLO dopo che la finestra e' visibile
// (Component.onCompleted della root Window in QML), mai da un costruttore chiamato prima che la
// finestra nativa esista davvero.
class OverlayStyler : public QObject {
    Q_OBJECT
    QML_ELEMENT

public:
    using QObject::QObject;

    // No-activate (WS_EX_NOACTIVATE, applicato una volta sola): impedisce all'overlay di rubare
    // il focus tastiera a qualunque altra app quando diventa visibile o viene cliccato - un
    // overlay "sempre sopra" non deve mai comportarsi come una finestra normale che l'utente sta
    // "usando" (roadmap F4.2.2: "gestire show/hide senza rubare focus").
    Q_INVOKABLE void makeNoActivate(QQuickWindow *window);

    // Click-through selettivo (WS_EX_TRANSPARENT, attivabile/disattivabile a runtime): quando
    // attivo, OGNI click e movimento del mouse sulla finestra passa alla finestra sottostante
    // come se l'overlay non esistesse. La QML lo attiva solo sull'area di sfondo (fuori dai
    // pannelli, vedi Main.qml) e lo disattiva quando il puntatore entra nel contenuto - mai
    // "sempre attivo" (i pannelli diventerebbero tutti click-through) ne' "mai attivo" (nessun
    // click raggiungerebbe mai il desktop/le altre finestre sotto).
    Q_INVOKABLE void setClickThrough(QQuickWindow *window, bool enabled);

    // F4.2.2: buco reale trovato con un log su file (vedi la cronologia della roadmap per la
    // sequenza di debug completa) - per la combinazione di flag di QUESTA finestra (Qt.Tool +
    // Qt.FramelessWindowHint + Qt.WindowStaysOnTopHint + sfondo trasparente, quindi WS_EX_LAYERED
    // aggiunto da Qt stesso), assegnare `window.visible` in QML AGGIORNA la proprieta' QML (letta
    // indietro correttamente) ma NON chiama `ShowWindow(SW_HIDE)`/`SW_SHOW` sulla HWND reale -
    // verificato empiricamente ispezionando `IsWindowVisible()` dall'esterno mentre la proprieta'
    // QML cambiava correttamente. `ShowWindow` esplicito qui bypassa qualunque cosa nella
    // pipeline QPA di Qt fallisca per questa combinazione di flag. `SW_SHOWNOACTIVATE` (non
    // `SW_SHOW`) per non rubare il focus alla ricomparsa, in linea con `makeNoActivate()`.
    Q_INVOKABLE void forceVisibility(QQuickWindow *window, bool visible);

    // F4.7.1: con WS_EX_NOACTIVATE un click non porta la finestra in primo piano, quindi la tastiera
    // resterebbe all'app sottostante e nella barra comandi non si potrebbe scrivere. Per il tempo della
    // digitazione (click sulla barra o Ctrl+Shift+J) l'HUD diventa attivabile e prende il focus; finita
    // la digitazione (invio, Esc, focus perso) torna no-activate e click-through come prima.
    Q_INVOKABLE void beginKeyboardInput(QQuickWindow *window);
    Q_INVOKABLE void endKeyboardInput(QQuickWindow *window);
};
