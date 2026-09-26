import QtQuick
import QtQuick.Layouts

// Status panel (fase 4.9.2): connessione al server companion, stato corrente, dispositivo
// attivo (v5.9, DEVICE_HANDOFF).
RowLayout {
    id: root
    property bool connected: false
    property string state: "IDLE"
    property string activeDevice: ""
    // F2.3.5: indicatore del microfono sempre visibile (MIC_STATE): aperto, aperto ma in pausa
    // mentre Jake parla, chiuso. Testo oltre al colore.
    property bool micOpen: false
    property bool micDiscarding: false
    // F4.2.1: esposto cosi' Main.qml puo' sapere se il puntatore e' su QUESTO pannello, per
    // disattivare il click-through dell'overlay solo quando serve davvero (vedi il commento in
    // Main.qml sul perche' e' per-pannello e non un'unica area grande quanto tutti i pannelli).
    property alias hovered: hoverHandler.hovered

    HoverHandler {
        id: hoverHandler
    }

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

    Rectangle {
        width: 8
        height: 8
        radius: 4
        color: !root.micOpen ? "#6b7280" : root.micDiscarding ? "#facc15" : "#f87171"
        Layout.alignment: Qt.AlignVCenter
    }

    Text {
        text: !root.micOpen ? qsTr("Microfono chiuso")
            : root.micDiscarding ? qsTr("Microfono in pausa") : qsTr("Microfono aperto")
        color: "#9ca3af"
        font.pixelSize: 12
        Accessible.role: Accessible.StaticText
        Accessible.name: text
    }

    Text {
        visible: root.activeDevice.length > 0
        text: qsTr("Attivo: %1").arg(root.activeDevice)
        color: "#6b7280"
        font.pixelSize: 12
    }
}
