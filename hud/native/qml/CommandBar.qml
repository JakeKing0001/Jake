import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Barra comando (fase 4.9.2): scrive un testo, lo invia a POST /command sul server companion
// (invece della dettatura vocale nativa, fuori scopo per questo prototipo).
RowLayout {
    id: root
    signal commandSubmitted(string text)
    // F4.2.1: vedi lo stesso alias in StatusPanel.qml.
    property alias hovered: hoverHandler.hovered

    HoverHandler {
        id: hoverHandler
    }

    TextField {
        id: input
        Layout.fillWidth: true
        placeholderText: qsTr("Scrivi un comando per Jake...")
        color: "#f5f5f7"
        placeholderTextColor: "#8b8f9a"
        background: Rectangle {
            color: "#1f2229"
            radius: 8
            border.color: "#33363f"
        }
        onAccepted: root._submit()
    }

    Button {
        text: qsTr("Invia")
        enabled: input.text.trim().length > 0
        onClicked: root._submit()
    }

    function _submit() {
        const text = input.text.trim();
        if (text.length === 0) return;
        root.commandSubmitted(text);
        input.text = "";
    }
}
