import QtQuick
import QtQuick.Controls.Basic

// Conversation panel (fase 4.9.2): cronologia USER_MESSAGE/JAKE_MESSAGE/NOTIFICATION ricevuta
// dal bus eventi (core/event_bus.py) via SSE. Nessuna persistenza qui: e' solo la sessione
// visibile in questa finestra, la cronologia vera resta in MemoryManager sul lato Python.
Rectangle {
    id: root
    color: "#1a1c22"
    radius: 10
    border.color: "#2c2f38"
    // F4.2.1: vedi lo stesso alias in StatusPanel.qml.
    property alias hovered: hoverHandler.hovered

    HoverHandler {
        id: hoverHandler
    }

    // F2.2.7/F4.5.1: trascrizione live (partial provvisorio in corsivo, final normale), passo in
    // corso, ultima prova e diagnosi del selettore - dal riduttore condiviso via JakeClient.
    property string transcriptText: ""
    property bool transcriptFinal: false
    property string stepDescription: ""
    property string evidenceSummary: ""
    property string inspectionReason: ""
    // F4.6.1/F4.6.3: ultima attivita' e scadenza del suo undo (epoch s; 0 = non annullabile).
    property string activitySummary: ""
    property real undoExpiresAt: 0
    property real nowSeconds: Date.now() / 1000
    readonly property bool undoAvailable: undoExpiresAt > nowSeconds
    signal undoRequested()

    Timer {
        interval: 1000
        repeat: true
        running: root.undoExpiresAt > 0
        onTriggered: root.nowSeconds = Date.now() / 1000
    }

    function append(senderRole, messageText) {
        // Nomi dei campi del modello deliberatamente diversi da proprieta' comuni di Item/Text
        // (es. "text"): "text: text" dentro un delegate Text{} si legherebbe a se stesso invece
        // che al ruolo del modello, un bug classico e silenzioso di QML.
        model.append({ senderRole: senderRole, messageText: messageText });
        list.positionViewAtEnd();
    }

    ListModel { id: model }

    Column {
        id: live
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.margins: 10
        spacing: 2

        Text {
            width: parent.width
            visible: root.transcriptText.length > 0
            text: root.transcriptText
            font.italic: !root.transcriptFinal
            color: root.transcriptFinal ? "#e5e7eb" : "#9ca3af"
            wrapMode: Text.WordWrap
            font.pixelSize: 13
            Accessible.name: (root.transcriptFinal ? qsTr("Hai detto: ") : qsTr("Sto sentendo: ")) + text
        }
        Text {
            width: parent.width
            visible: root.stepDescription.length > 0
            text: qsTr("Passo: %1").arg(root.stepDescription)
            color: "#facc15"
            wrapMode: Text.WordWrap
            font.pixelSize: 12
        }
        Text {
            width: parent.width
            visible: root.evidenceSummary.length > 0
            text: root.evidenceSummary
            color: "#3ddc84"
            wrapMode: Text.WordWrap
            font.pixelSize: 12
        }
        Row {
            width: parent.width
            spacing: 8
            visible: root.activitySummary.length > 0
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: qsTr("Ultima azione: %1").arg(root.activitySummary)
                    + (root.undoAvailable ? qsTr(" · annullabile per %1 s").arg(Math.max(0, Math.floor(root.undoExpiresAt - root.nowSeconds))) : "")
                color: "#cbd5e1"
                font.pixelSize: 12
                Accessible.name: text
            }
            Button {
                visible: root.undoAvailable
                text: qsTr("Annulla")
                Accessible.name: qsTr("Annulla l'ultima azione")
                onClicked: root.undoRequested()
            }
        }
        Text {
            width: parent.width
            visible: root.inspectionReason.length > 0
            text: qsTr("Non trovato: %1").arg(root.inspectionReason)
            color: "#fb923c"
            wrapMode: Text.WordWrap
            font.pixelSize: 12
        }
    }

    ListView {
        id: list
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.bottom: live.top
        anchors.margins: 10
        spacing: 8
        clip: true
        model: model
        delegate: Column {
            width: list.width
            spacing: 2
            Text {
                text: senderRole === "user" ? qsTr("Tu") : senderRole === "jake" ? qsTr("Jake") : senderRole
                font.pixelSize: 11
                font.bold: true
                color: senderRole === "user" ? "#4fd1ff" : senderRole === "errore" ? "#f87171" : "#3ddc84"
            }
            Text {
                width: list.width
                text: messageText
                wrapMode: Text.WordWrap
                color: "#e5e7eb"
                font.pixelSize: 14
            }
        }
    }

    Text {
        anchors.centerIn: parent
        visible: model.count === 0
        text: qsTr("Nessun messaggio ancora")
        color: "#6b7280"
    }
}
