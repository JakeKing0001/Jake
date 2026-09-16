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
// cross-platform per questo). Click-through GROSSOLANO in questo primo passo, non ancora
// per-pannello (limite dichiarato, vedi README): l'intera area occupata dal ColumnLayout dei
// pannelli resta interattiva, solo il margine esterno (16px) e' click-through - i vuoti TRA un
// pannello e l'altro non sono ancora distinti dai pannelli stessi.
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
    }

    OverlayStyler {
        id: overlayStyler
    }

    Component.onCompleted: {
        jake.connectToJake(jakeBaseUrl)
        overlayStyler.makeNoActivate(window)
        overlayStyler.setClickThrough(window, true)
    }

    HoverHandler {
        id: contentHover
        target: content
        onHoveredChanged: overlayStyler.setClickThrough(window, !hovered)
    }

    ColumnLayout {
        id: content
        anchors.fill: parent
        anchors.margins: 16
        spacing: 12

        StatusPanel {
            Layout.fillWidth: true
            connected: jake.connected
            state: jake.state
            activeDevice: jake.activeDevice
        }

        Orb {
            id: orb
            Layout.alignment: Qt.AlignHCenter
            Layout.topMargin: 8
            Layout.bottomMargin: 8
            state: jake.state
        }

        ConversationPanel {
            id: conversation
            Layout.fillWidth: true
            Layout.fillHeight: true
        }

        QuickActions {
            Layout.fillWidth: true
            onActionTriggered: (command) => jake.sendCommand(command)
        }

        CommandBar {
            Layout.fillWidth: true
            onCommandSubmitted: (text) => jake.sendCommand(text)
        }
    }
}
