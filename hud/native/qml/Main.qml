import QtQuick
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
    height: 720
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
    palette.light: Theme.controlHover

    // 127.0.0.1:8765 e' il default di companion_server_port; il core passa quello reale (--jake-url).
    property string jakeBaseUrl: "http://127.0.0.1:8765"
    // Credenziale per-dispositivo consegnata dal core sullo stdin (vedi src/main.cpp).
    property string jakeDeviceId: ""
    property string jakeToken: ""
    // Impostati da src/main.cpp: accessibilita' e qualita' dell'orb (F4.4.5, F4.3.5).
    property bool reducedMotion: false
    property string orbQuality: "high"
    property bool orb3d: false

    JakeClient {
        id: jake
        onMessageReceived: (role, text) => conversation.append(role, text)
        onNotification: (kind, text) => toast.show(text, kind === "reminder" ? Theme.accent : Theme.textMuted)
        onErrorOccurred: (detail) => toast.show(detail || qsTr("Errore sconosciuto"), Theme.danger)
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
            commandBar.focusInput();
        }
        onKillSwitchRequested: {
            jake.sendCommand("ferma tutto");
            toast.show(qsTr("Ferma tutto inviato"), Theme.danger);
        }
    }

    // Click-through ovunque tranne sopra i pannelli (mai calcolato dalla geometria del layout, che
    // includerebbe i vuoti tra un pannello e l'altro).
    readonly property bool pointerOverAnyPanel: statusPanel.hovered || orb.hovered || permissionCard.hovered
        || conversation.hovered || actionCenter.hovered || commandBar.hovered || toast.hovered
    property bool typing: false
    onPointerOverAnyPanelChanged: if (!typing) overlayStyler.setClickThrough(window, !pointerOverAnyPanel)

    Component.onCompleted: {
        if (jakeToken.length > 0)
            jake.setCredentials(jakeDeviceId, jakeToken)
        jake.connectToJake(jakeBaseUrl)
        overlayStyler.makeNoActivate(window)
        overlayStyler.setClickThrough(window, true)
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
        Binding { target: orb.item; property: "state"; value: jake.state; when: orb.item !== null }
        Binding { target: orb.item; property: "outcome"; value: jake.lastOutcome; when: orb.item !== null }
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
                    font.pixelSize: 13
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
            Layout.fillWidth: true
            Layout.preferredHeight: implicitHeight
            Layout.fillHeight: expanded
            transcriptText: jake.transcriptText
            transcriptFinal: jake.transcriptFinal
            stepDescription: jake.state === "EXECUTING" ? jake.stepDescription : ""
            evidenceSummary: jake.evidenceSummary
            inspectionReason: jake.inspectionReason
        }

        ActionCenter {
            id: actionCenter
            Layout.fillWidth: true
            activitySummary: jake.lastActivitySummary
            undoExpiresAt: jake.lastUndoExpiresAt
            // undo e stop passano dagli stessi skill del comando vocale (scadenza, policy, controlli)
            onUndoRequested: jake.sendCommand("annulla l'ultima azione")
            onStopRequested: jake.sendCommand("ferma tutto")
        }

        CommandBar {
            id: commandBar
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
        property color tone: Theme.textMuted
        function show(text, color) {
            message = text;
            tone = color;
            opacity = 1;
            hideTimer.restart();
        }
        anchors.horizontalCenter: parent.horizontalCenter
        y: 58
        width: Math.min(parent.width - 40, toastText.implicitWidth + 36)
        height: toastText.implicitHeight + 18
        visible: opacity > 0
        opacity: 0
        accentColor: tone
        Behavior on opacity { NumberAnimation { duration: window.reducedMotion ? 0 : 220 } }
        Accessible.role: Accessible.AlertMessage
        Accessible.name: message
        Text {
            id: toastText
            anchors.centerIn: parent
            width: Math.min(implicitWidth, window.width - 76)
            text: toast.message
            color: Theme.text
            wrapMode: Text.WordWrap
            font.pixelSize: Theme.fontSmall
        }
        Timer { id: hideTimer; interval: 6000; onTriggered: toast.opacity = 0 }
    }
}
