import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import JakeHud

// Fase 4.9.2 (QML prototype): "niente effetti complessi ancora, prima deve funzionare".
// Finestra normale, bordata, opaca - la trasparenza/click-through reale e' la fase 4.9.3, il
// vetro vero e proprio (blur/rifrazione) la 4.9.4/4.9.5. Qui ricrea solo i cinque pezzi
// richiesti: orb centrale, barra comando, pannello conversazione, pannello di stato, azioni
// rapide, tutti collegati DAVVERO al server companion di Jake (core/companion_server.py).
ApplicationWindow {
    id: window
    width: 420
    height: 640
    visible: true
    title: qsTr("Jake HUD")
    color: "#14161c"

    // 127.0.0.1:8765 e' il default di companion_server_port in config/settings.example.json.
    property string jakeBaseUrl: "http://127.0.0.1:8765"

    JakeClient {
        id: jake
        onMessageReceived: (role, text) => conversation.append(role, text)
        onNotification: (kind, text) => conversation.append("notifica", text)
        onErrorOccurred: (detail) => conversation.append("errore", detail || qsTr("Errore sconosciuto"))
    }

    Component.onCompleted: jake.connectToJake(jakeBaseUrl)

    ColumnLayout {
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
