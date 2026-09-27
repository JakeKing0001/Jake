import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import JakeHud

// Barra comando: invia a POST /command. F4.7.1: un click (o Ctrl+Shift+J) chiede alla finestra di prendere la
// tastiera (keyboardRequested) - l'overlay e' no-activate e senza questo i tasti andrebbero all'app sotto;
// invio, Esc o focus perso la restituiscono (keyboardReleased). Scorciatoie rapide come chip sopra il campo.
GlassPanel {
    id: root
    signal commandSubmitted(string text)
    signal keyboardRequested()
    signal keyboardReleased()
    property var quickActions: [
        { label: qsTr("Che ore sono"), command: "che ore sono" },
        { label: qsTr("Meteo"), command: "che tempo fa" },
        { label: qsTr("Promemoria"), command: "che promemoria ho" }
    ]
    implicitHeight: column.implicitHeight + 20

    function focusInput() {
        root.keyboardRequested();
        input.forceActiveFocus();
    }

    ColumnLayout {
        id: column
        anchors.fill: parent
        anchors.margins: 10
        spacing: 8

        Flow {
            Layout.fillWidth: true
            spacing: 6
            Repeater {
                model: root.quickActions
                delegate: Button {
                    text: modelData.label
                    font.pixelSize: Theme.fontSmall
                    flat: true
                    Accessible.name: qsTr("Chiedi a Jake: %1").arg(modelData.label)
                    onClicked: root.commandSubmitted(modelData.command)
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            TextField {
                id: input
                Layout.fillWidth: true
                placeholderText: qsTr("Scrivi un comando per Jake…  (Ctrl+Shift+J)")
                color: Theme.text
                placeholderTextColor: Theme.textFaint
                Accessible.name: qsTr("Comando per Jake")
                background: Rectangle {
                    color: Qt.rgba(0, 0, 0, 0.35)
                    radius: Theme.smallRadius
                    border.width: input.activeFocus ? 2 : 1
                    border.color: input.activeFocus ? Theme.focusRing : Theme.glassBorder
                }
                TapHandler { onTapped: root.focusInput() }
                onAccepted: root._submit()
                onActiveFocusChanged: if (!activeFocus) root.keyboardReleased()
                Keys.onEscapePressed: { input.text = ""; input.focus = false; root.keyboardReleased(); }
            }
            Button {
                text: qsTr("Invia")
                enabled: input.text.trim().length > 0
                Accessible.name: qsTr("Invia il comando")
                onClicked: root._submit()
            }
        }
    }

    function _submit() {
        const text = input.text.trim();
        if (text.length === 0) return;
        root.commandSubmitted(text);
        input.text = "";
        input.focus = false;
        root.keyboardReleased();
    }
}
