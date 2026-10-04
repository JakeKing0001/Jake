import QtQuick

// Presenza ambientale dell'HUD: QUANTO e' presente sullo schermo, indipendente da COME e' organizzato (layoutMode
// full/compact/focus di Main.qml, ricordato per monitor e mai toccato da qui).
//   expanded  l'HUD normale nel layout scelto
//   mini      solo l'orb, finestra piccola nell'angolo in alto a destra dell'area utile del monitor
//   hidden    finestra invisibile; Jake, la voce e la wake word restano attivi (HUD_HIDE del protocollo)
// Stato di sessione: non si salva mai, ogni avvio riparte da expanded. Logica pura (nessun item visivo) cosi' che
// tests/presence_tests.cpp la verifichi senza finestra.
QtObject {
    id: presence

    property string mode: "expanded"
    // chi l'ha deciso: "auto_idle" (inattivita'), "manual" (comando dell'utente), "wake", "confirmation", ...
    property string reason: ""
    property bool reducedMotion: false

    readonly property int windowSize: 168      // finestra del mini: contiene la scena 3D (1,5 x l'item) senza tagli
    readonly property int orbSize: 112
    readonly property int edgeMargin: 16
    readonly property int idleMs: 30000        // inattivita' vera prima del mini automatico
    readonly property int transitionMs: reducedMotion ? 0 : 260

    readonly property bool mini: mode === "mini"
    readonly property bool hidden: mode === "hidden"
    readonly property bool expanded: mode === "expanded"

    // Vero se il modo e' cambiato. Ogni ritorno a expanded e' un'interazione: chi chiama azzera l'inattivita'.
    function request(target, why) {
        if (target !== "expanded" && target !== "mini" && target !== "hidden")
            target = "expanded";
        reason = why || "";
        if (target === mode)
            return false;
        mode = target;
        return true;
    }

    // Mini automatico SOLO con Jake davvero fermo: stato IDLE, nessuna attivita' recente, niente conferme, nessuno che
    // scrive o ha il cursore su un pannello, nessun errore o avviso ancora in vista. Mai da mini o nascosto.
    function autoMiniDue(s) {
        return expanded && s.visible && s.state === "IDLE" && !s.recentlyActive && !s.typing
            && !s.confirmationPending && !s.pointerOverPanel && !s.holdingError && !s.toastShown
    }

    // Una notifica normale non fa esplodere l'HUD: in mini l'orb resta piccolo e accende un segnale minimo, da
    // nascosto non si riapre (la voce e la policy delle notifiche restano quelle di Jake).
    function notificationShowsToast() { return expanded }
    function notificationMarksOrb() { return mini }

    // Una conferma richiede l'utente: da mini si torna grandi per mostrare azione e rischio. Da nascosto no: e' una
    // scelta esplicita dell'utente, e la domanda arriva comunque a voce.
    function confirmationRestores() { return mini }

    // Angolo in alto a destra dell'area UTILE (senza barra delle applicazioni) del monitor dell'HUD.
    function miniPosition(area, size) {
        return Qt.point(Math.round(area.x + area.width - size - edgeMargin), Math.round(area.y + edgeMargin))
    }
}
