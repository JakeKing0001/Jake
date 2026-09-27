import QtQuick
import QtCore
import QtQuick.Controls.Basic
import QtQuick.Layouts
import JakeHud

// HUD nativo: overlay trasparente, sempre sopra, no-activate e click-through per pannello (F4.2.1/F4.2.2),
// collegato al server companion di Jake (core/companion_server.py) con la credenziale per-dispositivo che il
// core gli consegna (core/native_hud.py).
//
// Gerarchia (F4.4/F4.5): l'orb e' la presenza principale; attorno, pannelli di vetro (Theme/GlassPanel, un
// solo materiale) che compaiono quando servono - conversazione compatta apribile, permission card solo con
// una conferma in sospeso, action center compatto, barra comandi. Notifiche ed errori sono toast che si
// chiudono da soli: non si accumulano mai nella conversazione.
ApplicationWindow {
    id: window
    width: 440
    height: layoutMode === "full" ? 720 : layoutMode === "compact" ? 560 : 470
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

    JakeClient {
        id: jake
        onMessageReceived: (role, text) => conversation.append(role, text)
        onNotification: (kind, text) => toast.show(text, kind === "reminder" ? Theme.accent : Theme.textMuted, kind)
        onErrorOccurred: (detail) => toast.show(detail || qsTr("Errore sconosciuto"), Theme.danger, "error")
        onVisibilityRequested: (visible) => {
            window.visible = visible;
            overlayStyler.forceVisibility(window, visible);
            if (visible)
                overlayStyler.makeNoActivate(window);
        }
    }

    OverlayStyler { id: overlayStyler }

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

    // Click-through ovunque tranne sopra i pannelli (mai calcolato dalla geometria del layout, che
    // includerebbe i vuoti tra un pannello e l'altro).
    // F4.7.3/F4.7.4: layout completo / compatto / focus, ricordato PER MONITOR, e il monitor scelto. Compatto: niente
    // conversazione; focus: niente conversazione ne' barra comandi (torna con Ctrl+Shift+J). Richiesta di conferma
    // e "Ferma tutto" restano sempre visibili. L'HUD sta sul bordo destro del monitor e ci torna se un monitor sparisce.
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
    function place() {
        const s = window.screen;
        if (!s) return;
        // margine per la barra delle applicazioni: il QScreen di QML espone solo la geometria intera del monitor
        window.x = s.virtualX + s.width - window.width - 24;
        window.y = s.virtualY + Math.max(12, (s.height - 48 - window.height) / 2);
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
            }
        }
    }

    Component.onCompleted: {
        Theme.highContrast = highContrast
        Theme.textScale = textScale
        if (jakeToken.length > 0)
            jake.setCredentials(jakeDeviceId, jakeToken)
        jake.connectToJake(jakeBaseUrl)
        overlayStyler.makeNoActivate(window)
        overlayStyler.setClickThrough(window, true)
        chooseScreen()
    }

    ColumnLayout {
        id: content
        anchors.fill: parent
        anchors.margins: 14
        spacing: 10

        StatusPanel {
            id: statusPanel
            Layout.fillWidth: true
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
        }

        // F4.4.7: orb 3D (Qt Quick 3D) se disponibile, altrimenti l'orb 2D con la stessa interfaccia.
        // L'area dell'orb prende lo spazio libero: e' la presenza principale dell'HUD.
        Item {
            id: orbArea
            Layout.fillWidth: true
            Layout.fillHeight: !conversation.expanded
            Layout.preferredHeight: 250
            Layout.minimumHeight: 170
        Loader {
            id: orb
            anchors.centerIn: parent
            // un Loader con dimensioni esplicite ridimensiona il suo item: le dimensioni stanno solo qui
            width: window.orb3d ? Math.min(orbArea.width, orbArea.height, 300) : 140
            height: width
            readonly property bool hovered: item ? item.hovered : false
            source: window.orb3d ? "Orb3D.qml" : "Orb.qml"
            onStatusChanged: if (status === Loader.Error && source.toString().indexOf("Orb3D") >= 0) source = "Orb.qml"
        }
        }
        Binding { target: orb.item; property: "state"; value: window.orbDemo ? demo.state : jake.state; when: orb.item !== null }
        Binding { target: orb.item; property: "outcome"; value: window.orbDemo ? demo.outcome : jake.lastOutcome; when: orb.item !== null }
        Binding { target: orb.item; property: "outcomeSerial"; value: window.orbDemo ? demo.index : jake.outcomeSerial
                  when: orb.item !== null && orb.item.hasOwnProperty("outcomeSerial") }
        // F4.4.4: livello audio reale (mai audio); decade a zero se non arrivano aggiornamenti
        Binding { target: orb.item; property: "level"; value: window.orbDemo ? demo.level : levelDecay.level
                  when: orb.item !== null && orb.item.hasOwnProperty("level") }
        QtObject {
            id: demo
            readonly property var steps: [
                ["IDLE", ""], ["LISTENING", ""], ["THINKING", ""], ["EXECUTING", ""], ["IDLE", "success"],
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

        // F4.5.3: permission card - azione, rischio, fonte esterna. Si conferma a voce o scrivendo "si'"
        // (stesso percorso della policy), nessun bottone che scavalchi la conferma.
        GlassPanel {
            id: permissionCard
            Layout.fillWidth: true
            visible: jake.confirmationPending
            accentColor: Theme.attention
            implicitHeight: permissionColumn.implicitHeight + 20
            Accessible.role: Accessible.AlertMessage
            Accessible.name: permissionTitle.text + ". " + permissionDetail.text
            Column {
                id: permissionColumn
                anchors.fill: parent
                anchors.margins: 10
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

        ConversationPanel {
            id: conversation
            visible: window.layoutMode === "full"
            Layout.fillWidth: true
            Layout.preferredHeight: implicitHeight
            Layout.fillHeight: expanded
            transcriptText: jake.transcriptText
            transcriptFinal: jake.transcriptFinal
            stepDescription: jake.state === "EXECUTING" ? jake.stepDescription : ""
            // il piano resta visibile finche' si lavora e mentre si attende una conferma a meta' compito
            planSteps: jake.state === "EXECUTING" || jake.state === "WAITING" ? jake.planSteps : []
            evidenceSummary: jake.evidenceSummary
            inspectionReason: jake.inspectionReason
        }

        ActionCenter {
            id: actionCenter
            Layout.fillWidth: true
            activitySummary: jake.lastActivitySummary
            activityDetails: jake.lastActivityDetails
            undoExpiresAt: jake.lastUndoExpiresAt
            retryAvailable: jake.lastOutcome === "error"
            // undo, riprova e stop passano dagli stessi skill del comando vocale (scadenza, policy, controlli)
            onUndoRequested: jake.sendCommand("annulla l'ultima azione")
            onRetryRequested: jake.sendCommand("riprova l'ultima azione")
            onStopRequested: jake.sendCommand("ferma tutto")
        }

        CommandBar {
            id: commandBar
            // in focus ricompare solo mentre si scrive (Ctrl+Shift+J)
            visible: window.layoutMode !== "focus" || window.typing
            Layout.fillWidth: true
            onCommandSubmitted: (text) => jake.sendCommand(text)
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
        y: 58
        width: Math.min(parent.width - 40, Math.max(toastText.implicitWidth, toastActions.implicitWidth) + 36)
        height: toastColumn.implicitHeight + 18
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
                width: Math.min(implicitWidth, window.width - 76)
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
