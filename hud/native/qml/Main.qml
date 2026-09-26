import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import JakeHud

// Fase 4.9.2 (QML prototype): "niente effetti complessi ancora, prima deve funzionare".
// Finestra normale, bordata, opaca - la trasparenza/click-through reale e' la fase 4.9.3, il
// vetro vero e proprio (blur/rifrazione) la 4.9.4/4.9.5. Qui ricrea solo i cinque pezzi
// richiesti: orb centrale, barra comando, pannello conversazione, pannello di stato, azioni
// rapide, tutti collegati DAVVERO al server companion di Jake (core/companion_server.py).
//
// Fase 4.9.3/F4.2.1 ("trasparenza, click-through selettivo e no-activate"): finestra senza
// bordi/trasparente, sempre sopra, senza icona in barra applicazioni (Qt.Tool) e che non ruba
// mai il focus tastiera (WS_EX_NOACTIVATE, applicato in OverlayStyler - Qt non ha un flag
// cross-platform per questo). Click-through PER PANNELLO (seconda fetta di F4.2.1): ciascuno dei
// cinque pannelli espone un proprio "hovered" (HoverHandler nel rispettivo file .qml, vedi
// StatusPanel.qml per il commento completo) - l'overlay e' click-through ovunque TRANNE quando il
// puntatore e' sopra uno di questi cinque, inclusi quindi i vuoti tra un pannello e l'altro
// (limite del primo passo, ora chiuso).
ApplicationWindow {
    id: window
    width: 420
    height: 640
    visible: true
    title: qsTr("Jake HUD")
    color: "transparent"
    flags: Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool

    // 127.0.0.1:8765 e' il default di companion_server_port in config/settings.example.json.
    property string jakeBaseUrl: "http://127.0.0.1:8765"

    JakeClient {
        id: jake
        onMessageReceived: (role, text) => conversation.append(role, text)
        onNotification: (kind, text) => conversation.append("notifica", text)
        onErrorOccurred: (detail) => conversation.append("errore", detail || qsTr("Errore sconosciuto"))
        // F4.2.2 ("gestire show/hide senza rubare focus"): HUD_SHOW/HUD_HIDE nascondono/mostrano
        // DAVVERO la finestra (prima cadevano nel ramo generico di JakeClient e non facevano
        // nulla di osservabile). WS_EX_NOACTIVATE (F4.2.1) e' gia' applicato una volta all'avvio
        // e sopravvive a ShowWindow (non tocca gli extended style) - riapplicato comunque ad ogni
        // ricomparsa per essere espliciti, non per necessita' dimostrata: costa un'unica chiamata
        // Win32 innocua anche se gia' impostato.
        onVisibilityRequested: (visible) => {
            window.visible = visible;
            overlayStyler.forceVisibility(window, visible);
            if (visible)
                overlayStyler.makeNoActivate(window);
        }
    }

    OverlayStyler {
        id: overlayStyler
    }

    HoverHandler {
        id: permissionHover
        target: permissionCard
    }

    // Vero SOLO quando il puntatore e' sopra uno dei cinque pannelli - mai calcolato dalla
    // geometria del ColumnLayout (che includerebbe anche i vuoti tra un pannello e l'altro, il
    // limite dichiarato del primo passo di F4.2.1).
    readonly property bool pointerOverAnyPanel: statusPanel.hovered || orb.hovered || permissionHover.hovered
        || conversation.hovered || quickActions.hovered || commandBar.hovered

    onPointerOverAnyPanelChanged: overlayStyler.setClickThrough(window, !pointerOverAnyPanel)

    Component.onCompleted: {
        jake.connectToJake(jakeBaseUrl)
        overlayStyler.makeNoActivate(window)
        overlayStyler.setClickThrough(window, true)
    }

    ColumnLayout {
        id: content
        anchors.fill: parent
        anchors.margins: 16
        spacing: 12

        StatusPanel {
            id: statusPanel
            Layout.fillWidth: true
            connected: jake.connected
            state: jake.state
            activeDevice: jake.activeDevice
            micOpen: jake.micOpen
            micDiscarding: jake.micDiscarding
        }

        Orb {
            id: orb
            Layout.alignment: Qt.AlignHCenter
            Layout.topMargin: 8
            Layout.bottomMargin: 8
            state: jake.state
        }

        // F4.5.3 (permission card): cosa aspetta conferma, con quale rischio e se l'ha suggerito un
        // contenuto esterno. Nessun bottone qui: si conferma a voce o dalla barra comandi ("si'"),
        // lo stesso percorso gia' coperto dalla policy.
        Rectangle {
            id: permissionCard
            Layout.fillWidth: true
            visible: jake.confirmationPending
            implicitHeight: permissionColumn.implicitHeight + 16
            radius: 10
            color: "#2a1f14"
            border.color: "#fb923c"
            Accessible.role: Accessible.AlertMessage
            Accessible.name: permissionTitle.text + ". " + permissionDetail.text

            Column {
                id: permissionColumn
                anchors.fill: parent
                anchors.margins: 8
                spacing: 2
                Text {
                    id: permissionTitle
                    text: jake.confirmationAuth ? qsTr("Serve l'autenticazione") : qsTr("Serve la tua conferma")
                    color: "#fb923c"
                    font.bold: true
                    font.pixelSize: 13
                }
                Text {
                    id: permissionDetail
                    width: parent.width
                    wrapMode: Text.WordWrap
                    text: qsTr("Azione: %1 · rischio: %2").arg(jake.confirmationIntent).arg(jake.confirmationRisk || qsTr("sconosciuto"))
                        + (jake.confirmationExternal ? qsTr(" · suggerita da un contenuto esterno") : "")
                    color: "#e5e7eb"
                    font.pixelSize: 12
                }
            }
        }

        ConversationPanel {
            id: conversation
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.topMargin: 14
            transcriptText: jake.transcriptText
            transcriptFinal: jake.transcriptFinal
            stepDescription: jake.state === "EXECUTING" ? jake.stepDescription : ""
            evidenceSummary: jake.evidenceSummary
            inspectionReason: jake.inspectionReason
        }

        QuickActions {
            id: quickActions
            Layout.fillWidth: true
            onActionTriggered: (command) => jake.sendCommand(command)
        }

        CommandBar {
            id: commandBar
            Layout.fillWidth: true
            onCommandSubmitted: (text) => jake.sendCommand(text)
        }
    }
}
