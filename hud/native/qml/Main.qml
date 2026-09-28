import QtQuick
import QtCore
import QtQuick.Controls.Basic
import QtQuick.Effects
import QtQuick.Layouts
import JakeHud

// HUD nativo: overlay trasparente, sempre sopra, no-activate e click-through per pannello (F4.2.1/F4.2.2),
// collegato al server companion di Jake (core/companion_server.py) con la credenziale per-dispositivo che il
// core gli consegna (core/native_hud.py).
//
// Composizione (seconda prova reale del 27/09/2026: "troppo spostato a destra, l'orb schiacciato dai pannelli"):
// l'orb e' il centro visivo, al centro esatto della finestra, e la finestra e' al centro dell'area utile del monitor.
// Attorno, simmetrici, i pannelli di vetro (un solo materiale, Theme/GlassPanel) che COMPAIONO SOLO QUANDO SERVONO:
// a sinistra la conversazione, a destra conferme e ultima azione; sotto l'orb il sottotitolo live, in basso la barra
// comandi. Le colonne laterali tengono il loro spazio anche vuote: l'orb non si sposta mai quando un pannello
// compare o sparisce. Dietro a tutto l'ambiente (aura dello stato e polvere luminosa, Ambience.qml), che il vetro
// dei pannelli rifrange. Notifiche ed errori sono toast che si chiudono da soli.
ApplicationWindow {
    id: window
    // F4.7.3/F4.7.4: completo (orb + pannelli ai lati), compatto (orb + pannelli sotto), focus (solo l'orb)
    width: layoutMode === "full" ? 1080 : layoutMode === "compact" ? 600 : 560
    height: layoutMode === "full" ? 680 : layoutMode === "compact" ? 820 : 540
    visible: true
    title: qsTr("Jake HUD")
    color: "transparent"
    flags: Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
    // Una palette scura per tutti i controlli (stesso materiale dei pannelli, testo sempre leggibile).
    palette.button: Theme.control
    palette.buttonText: Theme.text
    palette.window: Theme.glassBottom
    palette.windowText: Theme.text
    palette.text: Theme.text
    palette.base: Theme.control
    palette.highlight: Theme.accent
    palette.mid: Theme.controlHover
    // F4.7.4: i controlli (pulsanti, campo di testo) ereditano il font della finestra: stessa scala del resto
    font.pixelSize: Theme.fontBody
    palette.light: Theme.controlHover

    // 127.0.0.1:8765 e' il default di companion_server_port; il core passa quello reale (--jake-url).
    property string jakeBaseUrl: "http://127.0.0.1:8765"
    // Credenziale per-dispositivo consegnata dal core sullo stdin (vedi src/main.cpp).
    property string jakeDeviceId: ""
    property string jakeToken: ""
    // Impostati da src/main.cpp: accessibilita' e qualita' dell'orb (F4.4.5, F4.3.5).
    property bool reducedMotion: false
    property bool highContrast: false
    property real textScale: 1.0
    property string orbQuality: "high"
    property bool orb3d: false
    // JAKE_HUD_DEMO=1 (src/main.cpp): l'orb scorre da solo tutti gli stati, con un livello audio sintetico e gli esiti
    // success/warning/error - per verificarne a schermo il comportamento senza Jake acceso. Il resto dell'HUD resta reale.
    property bool orbDemo: false
    // F4.3.1: JAKE_HUD_GLASS=hud|off (src/main.cpp) tiene il vetro dell'HUD anche dove il vetro sul desktop e' possibile
    property bool desktopGlassAllowed: true

    JakeClient {
        id: jake
        onMessageReceived: (role, text) => { conversation.append(role, text); window.touch(); }
        onNotification: (kind, text) => toast.show(text, kind === "reminder" ? Theme.accent : Theme.textMuted, kind)
        onErrorOccurred: (detail) => toast.show(detail || qsTr("Errore sconosciuto"), Theme.danger, "error")
        onVisibilityRequested: (visible) => {
            window.visible = visible;
            overlayStyler.forceVisibility(window, visible);
            if (!visible)
                desktopGlass.hideAll();
            if (visible)
                overlayStyler.makeNoActivate(window);
        }
    }

    OverlayStyler { id: overlayStyler }

    // F4.3.1: il desktop vero, sfocato da DWM, dietro ogni pannello visibile (src/DesktopGlass.h). Segue i pannelli
    // registrati in Theme.panels alla stessa cadenza del click-through; si spegne con l'HUD e senza trasparenza.
    DesktopGlass { id: desktopGlass }
    function glassRegions() {
        const regions = [];
        for (let i = 0; i < Theme.panels.length; ++i) {
            const panel = Theme.panels[i];
            let opacity = 1, visible = true;
            for (let item = panel; item; item = item.parent) {
                opacity *= item.opacity;
                visible = visible && item.visible;
            }
            const origin = panel.mapToItem(null, 0, 0);
            regions.push({ x: origin.x, y: origin.y, width: panel.width, height: panel.height,
                           radius: panel.radius, visible: visible, opacity: opacity });
        }
        return regions;
    }
    Timer {
        interval: 60; repeat: true; running: Theme.desktopGlass && window.visible
        onTriggered: desktopGlass.sync(window, window.glassRegions())
    }
    onVisibleChanged: if (!visible) desktopGlass.hideAll()

    GlobalHotkeys {
        id: hotkeys
        onCommandBarRequested: {
            window.visible = true;
            overlayStyler.forceVisibility(window, true);
            window.typing = true;   // in layout focus la barra e' nascosta finche' non si scrive
            commandBar.focusInput();
        }
        onKillSwitchRequested: {
            jake.sendCommand("ferma tutto");
            toast.show(qsTr("Ferma tutto inviato"), Theme.danger);
        }
    }

    // Layout ricordato PER MONITOR, e il monitor scelto. "Ferma tutto" (riga di stato) e la richiesta di conferma
    // restano sempre visibili. Se il monitor dell'HUD sparisce si torna al principale, sempre al centro.
    Settings {
        id: hudSettings
        category: "layout"
        property string screenName: ""
        property string modes: "{}"       // {"nome monitor": "full" | "compact" | "focus"}
    }
    readonly property var layoutModes: ["full", "compact", "focus"]
    property string layoutMode: "full"
    function modeFor(screenName) {
        try { return JSON.parse(hudSettings.modes)[screenName] || "full"; } catch (e) { return "full"; }
    }
    function cycleLayout() {
        layoutMode = layoutModes[(layoutModes.indexOf(layoutMode) + 1) % layoutModes.length];
        let modes = {};
        try { modes = JSON.parse(hudSettings.modes); } catch (e) {}
        modes[window.screen.name] = layoutMode;
        hudSettings.modes = JSON.stringify(modes);
        Qt.callLater(place);
    }
    function nextScreen() {
        const screens = Qt.application.screens;
        if (screens.length < 2) return;
        let index = 0;
        for (let i = 0; i < screens.length; ++i)
            if (screens[i].name === window.screen.name) index = i;
        window.screen = screens[(index + 1) % screens.length];
        hudSettings.screenName = window.screen.name;
        layoutMode = modeFor(window.screen.name);
        Qt.callLater(place);
    }
    // al centro dell'area UTILE del monitor (senza barra delle applicazioni), su ogni monitor e in ogni layout
    function place() {
        const area = overlayStyler.availableGeometry(window);
        const s = window.screen;
        if (area.width > 0) {
            window.x = Math.round(area.x + (area.width - window.width) / 2);
            window.y = Math.round(area.y + Math.max(0, (area.height - window.height) / 2));
        } else if (s) {
            window.x = Math.round(s.virtualX + (s.width - window.width) / 2);
            window.y = Math.round(s.virtualY + Math.max(0, (s.height - 48 - window.height) / 2));
        }
    }
    function chooseScreen() {
        const screens = Qt.application.screens;
        for (let i = 0; i < screens.length; ++i)
            if (screens[i].name === hudSettings.screenName) { window.screen = screens[i]; break; }
        layoutMode = modeFor(window.screen.name);
        Qt.callLater(place);
    }
    // hot-plug: un monitor collegato o scollegato (il monitor dell'HUD puo' sparire) -> si riparte dalla scelta
    Connections { target: Qt.application; function onScreensChanged() { window.chooseScreen(); } }
    onScreenChanged: Qt.callLater(place)
    onWidthChanged: Qt.callLater(place)
    onHeightChanged: Qt.callLater(place)

    // ---- stato mostrato dall'orb ----------------------------------------------------------------------------
    // ERROR resta visibile almeno 1,8 s (B7, prova reale del 27/09/2026: un errore era subito coperto dalla risposta)
    // anche se nel frattempo Jake parla per spiegarlo; solo l'utente che parla (ascolto, trascrizione, dettatura) lo
    // sostituisce subito. Poi si torna allo stato REALE del momento, mai a uno vecchio.
    property bool holdingError: false
    readonly property string rawState: holdingError && ["LISTENING", "TRANSCRIBING", "DICTATION"].indexOf(jake.state) < 0
        ? "ERROR" : jake.state
    Connections {
        target: jake
        function onStateChanged() {
            if (jake.state === "ERROR") { window.holdingError = true; errorHold.restart(); }
            else if (["LISTENING", "TRANSCRIBING", "DICTATION"].indexOf(jake.state) >= 0) window.holdingError = false;
            if (jake.state !== "IDLE") window.touch();
        }
    }
    Timer { id: errorHold; interval: 1800; onTriggered: window.holdingError = false }
    // IDLE si mostra dopo un istante: la risposta testuale arriva un attimo prima della voce (JAKE_MESSAGE -> IDLE,
    // poi SPEAKING), e l'orb non deve "spegnersi" per un fotogramma fra le due. Ogni stato attivo passa subito.
    property string displayState: "IDLE"
    onRawStateChanged: {
        if (rawState === "IDLE") idleSettle.restart();
        else { idleSettle.stop(); displayState = rawState; }
    }
    Timer { id: idleSettle; interval: 220; onTriggered: if (window.rawState === "IDLE") window.displayState = "IDLE" }
    readonly property string shownState: orbDemo ? demo.state : displayState

    // ---- quando i pannelli servono ----------------------------------------------------------------------------
    property real lastActivityAt: 0
    property real nowMs: Date.now()
    function touch() { lastActivityAt = Date.now(); nowMs = lastActivityAt; }
    Timer { interval: 1000; repeat: true; running: window.visible; onTriggered: window.nowMs = Date.now() }
    readonly property bool recentlyActive: nowMs - lastActivityAt < 30000 || shownState !== "IDLE"
    Connections {
        target: jake
        function onViewChanged() {
            if (jake.transcriptText.length > 0 || jake.planSteps.length > 0 || jake.confirmationPending) window.touch();
        }
    }

    // ---- click-through ---------------------------------------------------------------------------------------
    readonly property bool pointerOverAnyPanel: statusPanel.hovered || orb.hovered || permissionCard.hovered
        || conversation.hovered || actionCenter.hovered || commandBar.hovered || toast.hovered
    property bool typing: false
    onPointerOverAnyPanelChanged: if (!typing) overlayStyler.setClickThrough(window, !pointerOverAnyPanel)

    // Click-through deciso dalla posizione REALE del cursore (OverlayStyler.cursorInWindow): con la finestra
    // trasparente ai click non arriva nessun hover, quindi l'hover da solo non poteva mai riattivare i pannelli.
    property bool cursorOverPanel: false
    function panelAt(point) {
        const items = Theme.panels.concat([orb]);
        for (let i = 0; i < items.length; ++i) {
            const item = items[i];
            if (!item || !item.visible || item.opacity < 0.02) continue;
            let ancestor = item.parent, hidden = false;
            while (ancestor) { if (!ancestor.visible || ancestor.opacity < 0.02) { hidden = true; break; } ancestor = ancestor.parent; }
            if (hidden) continue;
            const p = item.mapFromItem(null, point.x, point.y);
            if (p.x >= 0 && p.y >= 0 && p.x <= item.width && p.y <= item.height) return true;
        }
        return false;
    }
    Timer {
        interval: 60; repeat: true; running: window.visible
        onTriggered: {
            if (window.typing) return;
            const over = window.panelAt(overlayStyler.cursorInWindow(window));
            if (over !== window.cursorOverPanel) {
                window.cursorOverPanel = over;
                overlayStyler.setClickThrough(window, !over);
                if (over) window.touch();   // un pannello sotto il cursore non sparisce mentre lo si usa
            }
        }
    }

    Component.onCompleted: {
        Theme.highContrast = highContrast
        Theme.textScale = textScale
        Theme.richGlass = orbQuality === "high" && !highContrast
        Theme.desktopGlass = desktopGlassAllowed && desktopGlass.supported && !highContrast
            && desktopGlass.transparencyEffectsEnabled()
        console.info("Vetro dei pannelli:", Theme.desktopGlass ? "desktop (" + desktopGlass.mode + ")" : "HUD")
        Theme.backdrop = frosted
        if (jakeToken.length > 0)
            jake.setCredentials(jakeDeviceId, jakeToken)
        jake.connectToJake(jakeBaseUrl)
        overlayStyler.makeNoActivate(window)
        overlayStyler.setClickThrough(window, true)
        chooseScreen()
    }
    Binding { target: Theme; property: "stateColor"; value: orb.item && orb.item.glowColor !== undefined ? orb.item.glowColor : Theme.ok }

    // ---- geometria -----------------------------------------------------------------------------------------
    readonly property int margin: 16
    readonly property int orbSize: layoutMode === "full" ? 410 : layoutMode === "compact" ? 350 : 320
    readonly property int orbTop: 64
    readonly property int sideWidth: 318
    readonly property int sideGap: 26
    readonly property point orbCenter: Qt.point(width / 2, orbTop + orbSize / 2)

    // ---- ambiente: aura e polvere, e la loro copia sfocata che il vetro rifrange --------------------------------
    MultiEffect {
        id: frosted
        anchors.fill: ambience
        source: ambience
        visible: Theme.richGlass
        blurEnabled: true
        blur: 1.0
        blurMax: 48
        autoPaddingEnabled: false
    }
    Ambience {
        id: ambience
        anchors.fill: parent
        state: window.shownState
        tint: Theme.stateColor
        center: window.orbCenter
        orbRadius: window.orbSize * 0.42
        level: orb.item && orb.item.audio !== undefined ? orb.item.audio : 0
        reducedMotion: window.reducedMotion
        rich: window.orbQuality === "high"
    }

    StatusPanel {
        id: statusPanel
        anchors.top: parent.top
        anchors.topMargin: 10
        anchors.horizontalCenter: parent.horizontalCenter
        width: Math.min(parent.width - 2 * window.margin, 640)
        opacity: window.recentlyActive || hovered ? 1 : 0.72
        Behavior on opacity { NumberAnimation { duration: 300 } }
        connected: jake.connected
        connectionProblem: jake.connectionProblem
        state: jake.state
        activeDevice: jake.activeDevice
        micOpen: jake.micOpen
        micDiscarding: jake.micDiscarding
        privateMode: jake.privateMode
        notificationMode: jake.notificationMode
        notificationModeLabel: jake.notificationModeLabel
        notificationsPending: jake.notificationsPending
        layoutMode: window.layoutMode
        screenCount: Qt.application.screens.length
        onLayoutRequested: window.cycleLayout()
        onScreenRequested: window.nextScreen()
        onStopRequested: jake.sendCommand("ferma tutto")
    }

    // F4.4.7: orb 3D (Qt Quick 3D) se disponibile, altrimenti l'orb 2D con la stessa interfaccia. Al centro.
    Item {
        id: orbArea
        x: (window.width - window.orbSize) / 2
        y: window.orbTop
        width: window.orbSize
        height: window.orbSize
        Loader {
            id: orb
            anchors.centerIn: parent
            // un Loader con dimensioni esplicite ridimensiona il suo item: le dimensioni stanno solo qui
            width: window.orb3d ? orbArea.width : 160
            height: width
            readonly property bool hovered: item ? item.hovered : false
            source: window.orb3d ? "Orb3D.qml" : "Orb.qml"
            onStatusChanged: if (status === Loader.Error && source.toString().indexOf("Orb3D") >= 0) source = "Orb.qml"
        }
    }
    Binding { target: orb.item; property: "state"; value: window.shownState; when: orb.item !== null }
    Binding { target: orb.item; property: "status"; value: window.orbDemo ? "" : jake.stateStatus
              when: orb.item !== null && orb.item.hasOwnProperty("status") }
    Binding { target: orb.item; property: "outcome"; value: window.orbDemo ? demo.outcome : jake.lastOutcome; when: orb.item !== null }
    Binding { target: orb.item; property: "outcomeSerial"; value: window.orbDemo ? demo.index : jake.outcomeSerial
              when: orb.item !== null && orb.item.hasOwnProperty("outcomeSerial") }
    // F4.4.4: livello audio reale (mai audio); decade a zero se non arrivano aggiornamenti
    Binding { target: orb.item; property: "level"; value: window.orbDemo ? demo.level : levelDecay.level
              when: orb.item !== null && orb.item.hasOwnProperty("level") }
    QtObject {
        id: demo
        readonly property var steps: [
            ["IDLE", ""], ["LISTENING", ""], ["TRANSCRIBING", ""], ["THINKING", ""], ["EXECUTING", ""], ["IDLE", "success"],
            ["SPEAKING", ""], ["WAITING", ""], ["IDLE", "warning"], ["ERROR", "error"], ["PAUSED", ""]]
        property int index: 0
        property string state: steps[index][0]
        property string outcome: steps[index][1]
        property real phase: 0
        // voce/microfono sintetici: sillabe irregolari, non una sinusoide perfetta
        readonly property real level: (state === "LISTENING" || state === "SPEAKING")
            ? Math.max(0, Math.sin(phase * 7.3) * 0.6 + Math.sin(phase * 2.1) * 0.4) : 0
    }
    Timer { running: window.orbDemo; interval: 4000; repeat: true
            onTriggered: demo.index = (demo.index + 1) % demo.steps.length }
    Timer { running: window.orbDemo; interval: 50; repeat: true; onTriggered: demo.phase += 0.05 }
    QtObject {
        id: levelDecay
        property real level: 0
        property real incoming: jake.audioLevel
        onIncomingChanged: { level = incoming; decayTimer.restart(); }
    }
    Timer { id: decayTimer; interval: 400; onTriggered: levelDecay.level = 0 }
    Binding { target: orb.item; property: "reducedMotion"; value: window.reducedMotion
              when: orb.item !== null && orb.item.hasOwnProperty("reducedMotion") }
    Binding { target: orb.item; property: "quality"; value: window.orbQuality
              when: orb.item !== null && orb.item.hasOwnProperty("quality") }

    // pillola di vetro dietro al sottotitolo: sopra un desktop pieno di testo il sottotitolo nudo non si leggeva
    Rectangle {
        anchors.centerIn: caption
        width: caption.contentWidth + 28
        height: caption.contentHeight + 12
        radius: height / 2
        color: Theme.glassBottom
        border.width: 1
        border.color: Theme.glassBorder
        opacity: caption.opacity * 0.92
        visible: caption.text.length > 0
    }
    // sottotitolo live sotto l'orb: cio' che Jake sta sentendo (provvisorio, in corsivo) o il passo in corso; nei
    // layout senza conversazione laterale anche l'ultima risposta, per qualche secondo
    Text {
        id: caption
        anchors.top: orbArea.bottom
        anchors.topMargin: 8
        anchors.horizontalCenter: parent.horizontalCenter
        width: Math.min(window.width - 2 * window.margin, 560)
        horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.WordWrap
        maximumLineCount: 2
        elide: Text.ElideRight
        readonly property bool live: jake.transcriptText.length > 0 && !jake.transcriptFinal
        text: live ? "«" + jake.transcriptText + "»"
            : jake.state === "EXECUTING" && jake.stepDescription.length > 0 ? jake.stepDescription
            : window.layoutMode !== "full" && window.recentlyActive ? conversation.lastJake : ""
        font.italic: live
        font.pixelSize: Theme.fontBody
        color: live ? Theme.textMuted : Theme.text
        style: Text.Raised
        styleColor: "#a0000000"
        opacity: text.length > 0 ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: 250 } }
        Accessible.name: live ? qsTr("Sto sentendo: ") + jake.transcriptText : text
    }

    // ---- colonna sinistra: conversazione (solo layout completo) ------------------------------------------------
    ConversationPanel {
        id: conversation
        readonly property bool wanted: window.layoutMode === "full"
            && (expanded || hovered || (hasContent && window.recentlyActive))
        x: orbArea.x - window.sideGap - width
        width: window.sideWidth
        height: Math.min(implicitHeight, window.orbSize)
        anchors.verticalCenter: orbArea.verticalCenter
        opacity: wanted ? 1 : 0
        visible: opacity > 0.01
        Behavior on opacity { NumberAnimation { duration: window.reducedMotion ? 0 : 320; easing.type: Easing.OutCubic } }
        transcriptText: ""   // la trascrizione live e' il sottotitolo sotto l'orb
        transcriptFinal: jake.transcriptFinal
        stepDescription: jake.state === "EXECUTING" ? jake.stepDescription : ""
        // il piano resta visibile finche' si lavora e mentre si attende una conferma a meta' compito
        planSteps: jake.state === "EXECUTING" || jake.state === "WAITING" ? jake.planSteps : []
        evidenceSummary: jake.evidenceSummary
        inspectionReason: jake.inspectionReason
    }

    // ---- colonna destra (completo) o sotto il sottotitolo (compatto): conferma e ultima azione ----------------
    Column {
        id: sideColumn
        spacing: 10
        width: window.layoutMode === "full" ? window.sideWidth : Math.min(window.width - 2 * window.margin, 480)
        x: window.layoutMode === "full" ? orbArea.x + orbArea.width + window.sideGap : (window.width - width) / 2
        y: window.layoutMode === "full" ? orbArea.y + (orbArea.height - height) / 2 : caption.y + Math.max(caption.height, 20) + 16
        visible: window.layoutMode !== "focus" || jake.confirmationPending

        // F4.5.3: permission card - azione, rischio, fonte esterna. Si conferma a voce o scrivendo "si'"
        // (stesso percorso della policy), nessun bottone che scavalchi la conferma. Sempre visibile se in sospeso.
        GlassPanel {
            id: permissionCard
            width: parent.width
            visible: jake.confirmationPending
            accentColor: Theme.attention
            height: permissionColumn.implicitHeight + 24
            Accessible.role: Accessible.AlertMessage
            Accessible.name: permissionTitle.text + ". " + permissionDetail.text
            Column {
                id: permissionColumn
                anchors.fill: parent
                anchors.margins: 12
                spacing: 3
                Text {
                    id: permissionTitle
                    text: jake.confirmationAuth ? qsTr("Serve l'autenticazione") : qsTr("Serve la tua conferma")
                    color: Theme.attention
                    font.bold: true
                    font.pixelSize: Theme.fontLabel
                }
                Text {
                    id: permissionDetail
                    width: parent.width
                    wrapMode: Text.WordWrap
                    text: qsTr("Azione: %1 · rischio: %2").arg(jake.confirmationIntent).arg(jake.confirmationRisk || qsTr("sconosciuto"))
                        + (jake.confirmationExternal ? qsTr(" · suggerita da un contenuto esterno") : "")
                        + qsTr("  —  rispondi \"sì\" o \"no\"")
                    color: Theme.text
                    font.pixelSize: Theme.fontSmall
                }
            }
        }

        ActionCenter {
            id: actionCenter
            width: parent.width
            // compare con un'azione recente, finche' si puo' annullare o riprovare, o sotto il cursore
            readonly property bool wanted: activitySummary.length > 0
                && (window.recentlyActive || undoAvailable || retryAvailable || hovered)
            opacity: wanted ? 1 : 0
            visible: opacity > 0.01
            Behavior on opacity { NumberAnimation { duration: window.reducedMotion ? 0 : 320 } }
            activitySummary: jake.lastActivitySummary
            activityDetails: jake.lastActivityDetails
            undoExpiresAt: jake.lastUndoExpiresAt
            retryAvailable: jake.lastOutcome === "error"
            // undo, riprova e stop passano dagli stessi skill del comando vocale (scadenza, policy, controlli)
            onUndoRequested: jake.sendCommand("annulla l'ultima azione")
            onRetryRequested: jake.sendCommand("riprova l'ultima azione")
            onStopRequested: jake.sendCommand("ferma tutto")
        }
    }

    CommandBar {
        id: commandBar
        // in focus ricompare solo mentre si scrive (Ctrl+Shift+J)
        visible: window.layoutMode !== "focus" || window.typing
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 12
        anchors.horizontalCenter: parent.horizontalCenter
        width: Math.min(parent.width - 2 * window.margin, 580)
        height: implicitHeight
        onCommandSubmitted: (text) => { jake.sendCommand(text); window.touch(); }
        onKeyboardRequested: {
            window.typing = true;
            overlayStyler.beginKeyboardInput(window);
        }
        onKeyboardReleased: {
            window.typing = false;
            overlayStyler.endKeyboardInput(window);
            overlayStyler.setClickThrough(window, !window.pointerOverAnyPanel);
        }
    }

    // Toast (F4.5.6): l'ultima notifica o errore per qualche secondo, poi sparisce. Mai una pila che
    // sommerge l'interfaccia; la stessa notifica ripetuta rinnova solo il toast esistente.
    GlassPanel {
        id: toast
        property string message: ""
        property string kind: ""
        property color tone: Theme.textMuted
        // F6.3.4: per una notifica proattiva (non un promemoria, non un errore) l'utente puo' chiederne meno o
        // silenziarla; le frasi sono quelle esatte degli skill vocali, stessa pipeline e stessa policy.
        readonly property bool proactive: kind === "advisory" || kind === "trigger"
        function show(text, color, notificationKind) {
            message = text;
            kind = notificationKind || "";
            tone = color;
            opacity = 1;
            hideTimer.restart();
        }
        anchors.horizontalCenter: parent.horizontalCenter
        y: 56
        width: Math.min(parent.width - 40, 560, Math.max(toastText.implicitWidth, toastActions.implicitWidth) + 40)
        height: toastColumn.implicitHeight + 20
        visible: opacity > 0
        opacity: 0
        accentColor: tone
        Behavior on opacity { NumberAnimation { duration: window.reducedMotion ? 0 : 220 } }
        Accessible.role: Accessible.AlertMessage
        Accessible.name: message
        Column {
            id: toastColumn
            anchors.centerIn: parent
            spacing: 6
            Text {
                id: toastText
                width: Math.min(implicitWidth, 520)
                text: toast.message
                color: Theme.text
                wrapMode: Text.WordWrap
                font.pixelSize: Theme.fontSmall
            }
            Row {
                id: toastActions
                visible: toast.proactive
                spacing: 6
                Button {
                    text: qsTr("Meno così")
                    font.pixelSize: Theme.fontTiny
                    Accessible.name: qsTr("Mostra meno notifiche come questa")
                    onClicked: { jake.sendCommand("meno notifiche così"); toast.opacity = 0; }
                }
                Button {
                    text: qsTr("Non più")
                    font.pixelSize: Theme.fontTiny
                    Accessible.name: qsTr("Non mostrare più notifiche come questa")
                    onClicked: { jake.sendCommand("non mostrarmelo più"); toast.opacity = 0; }
                }
            }
        }
        Timer { id: hideTimer; interval: 6000; onTriggered: toast.opacity = 0 }
    }
}
