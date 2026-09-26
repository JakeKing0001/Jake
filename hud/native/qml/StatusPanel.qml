import QtQuick
import QtQuick.Layouts
import JakeHud

// Riga di stato compatta (chip): connessione (con il motivo se manca), microfono sempre visibile (F2.3.5),
// dispositivo attivo (v5.9). Testo oltre al colore in ogni chip (F4.4.5).
GlassPanel {
    id: root
    property bool connected: false
    property string connectionProblem: ""
    property string state: "IDLE"
    property string activeDevice: ""
    property bool micOpen: false
    property bool micDiscarding: false
    implicitHeight: 36
    radius: height / 2

    component Chip: RowLayout {
        property color dot: Theme.textFaint
        property string label: ""
        spacing: 6
        Rectangle { width: 8; height: 8; radius: 4; color: parent.dot; Layout.alignment: Qt.AlignVCenter }
        Text {
            text: parent.label
            color: Theme.textMuted
            font.pixelSize: Theme.fontSmall
            elide: Text.ElideRight
            Layout.maximumWidth: 190
            Accessible.role: Accessible.StaticText
            Accessible.name: text
        }
    }

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: 14
        anchors.rightMargin: 14
        spacing: 14

        Chip {
            dot: root.connected ? Theme.ok : Theme.danger
            label: root.connected ? qsTr("Connesso")
                : root.connectionProblem.length > 0 ? qsTr("Non connesso: %1").arg(root.connectionProblem) : qsTr("Non connesso")
        }
        Item { Layout.fillWidth: true }
        Chip {
            dot: !root.micOpen ? Theme.textFaint : root.micDiscarding ? Theme.warn : Theme.danger
            label: !root.micOpen ? qsTr("Mic chiuso") : root.micDiscarding ? qsTr("Mic in pausa") : qsTr("Mic aperto")
        }
        Chip {
            visible: root.activeDevice.length > 0
            dot: Theme.accent
            label: root.activeDevice
        }
    }
}
