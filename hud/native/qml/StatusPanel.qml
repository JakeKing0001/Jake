import QtQuick
import QtQuick.Layouts

// Status panel (fase 4.9.2): connessione al server companion, stato corrente, dispositivo
// attivo (v5.9, DEVICE_HANDOFF).
RowLayout {
    id: root
    property bool connected: false
    property string state: "IDLE"
    property string activeDevice: ""

    Rectangle {
        width: 8
        height: 8
        radius: 4
        color: root.connected ? "#3ddc84" : "#f87171"
        Layout.alignment: Qt.AlignVCenter
    }

    Text {
        text: root.connected ? qsTr("Connesso") : qsTr("Non connesso")
        color: "#9ca3af"
        font.pixelSize: 12
    }

    Text {
        text: "· " + root.state
        color: "#6b7280"
        font.pixelSize: 12
    }

    Item { Layout.fillWidth: true }

    Text {
        visible: root.activeDevice.length > 0
        text: qsTr("Attivo: %1").arg(root.activeDevice)
        color: "#6b7280"
        font.pixelSize: 12
    }
}
