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

    function append(senderRole, messageText) {
        // Nomi dei campi del modello deliberatamente diversi da proprieta' comuni di Item/Text
        // (es. "text"): "text: text" dentro un delegate Text{} si legherebbe a se stesso invece
        // che al ruolo del modello, un bug classico e silenzioso di QML.
        model.append({ senderRole: senderRole, messageText: messageText });
        list.positionViewAtEnd();
    }

    ListModel { id: model }

    ListView {
        id: list
        anchors.fill: parent
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
