import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import JakeHud

// Riga di stato compatta (chip): connessione (con il motivo se manca), microfono sempre visibile (F2.3.5),
// dispositivo attivo (v5.9), modalita' privata (F4.5.7). Testo oltre al colore in ogni chip (F4.4.5).
GlassPanel {
    id: root
    property bool connected: false
    property string connectionProblem: ""
    property string state: "IDLE"
    property string activeDevice: ""
    property bool micOpen: false
    property bool micDiscarding: false
    property bool privateMode: false
    property string notificationMode: "normal"
    property string notificationModeLabel: ""
    property int notificationsPending: 0
    // F4.7.4: layout dell'HUD e spostamento su un altro monitor
    property string layoutMode: "full"
    property int screenCount: 1
    signal layoutRequested()
    signal screenRequested()
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
            // F4.5.6: una modalita' che trattiene le notifiche, o notifiche che aspettano, non restano invisibili
            visible: root.notificationMode !== "normal" || root.notificationsPending > 0
            dot: Theme.attention
            label: (root.notificationMode !== "normal" ? root.notificationModeLabel : "")
                + (root.notificationMode !== "normal" && root.notificationsPending > 0 ? " · " : "")
                + (root.notificationsPending > 0 ? qsTr("%n in attesa", "", root.notificationsPending) : "")
        }
        Chip {
            // i contenuti arrivano gia' nascosti dal core; qui solo il perche' si vede "(privato)"
            visible: root.privateMode
            dot: Theme.warn
            label: qsTr("Privato: niente testo, niente registro")
        }
        ToolButton {
            text: root.layoutMode === "full" ? "▣" : root.layoutMode === "compact" ? "▤" : "◉"
            font.pixelSize: Theme.fontSmall
            Layout.preferredHeight: 26
            Accessible.name: qsTr("Layout dell'HUD: %1. Cambia layout").arg(
                root.layoutMode === "full" ? qsTr("completo") : root.layoutMode === "compact" ? qsTr("compatto") : qsTr("focus"))
            onClicked: root.layoutRequested()
        }
        ToolButton {
            visible: root.screenCount > 1
            text: "⇆"
            font.pixelSize: Theme.fontSmall
            Layout.preferredHeight: 26
            Accessible.name: qsTr("Sposta l'HUD sull'altro monitor")
            onClicked: root.screenRequested()
        }
        Chip {
            visible: root.activeDevice.length > 0
            dot: Theme.accent
            label: root.activeDevice
        }
    }
}
