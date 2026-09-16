import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Quick actions (fase 4.9.2): scorciatoie per comandi frequenti, stesso ruolo di hud_quick_
// actions in config/settings.example.json (l'HUD PySide6 attuale), qui come semplici pulsanti
// che inviano il comando gia' pronto.
RowLayout {
    id: root
    signal actionTriggered(string command)
    // F4.2.1: vedi lo stesso alias in StatusPanel.qml.
    property alias hovered: hoverHandler.hovered

    HoverHandler {
        id: hoverHandler
    }

    property var actions: [
        { label: qsTr("Che ore sono"), command: "che ore sono" },
        { label: qsTr("Meteo"), command: "che tempo fa" },
        { label: qsTr("Promemoria"), command: "che promemoria ho" }
    ]

    Repeater {
        model: root.actions
        delegate: Button {
            text: modelData.label
            onClicked: root.actionTriggered(modelData.command)
        }
    }

    Item { Layout.fillWidth: true }
}
