import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import JakeHud

// Action Center compatto (F4.6): ultima azione con esito/verifica, Annulla finche' l'undo non scade (con
// il tempo rimasto), e "Ferma tutto" sempre presente. Tutto passa dagli skill del core (stessa policy
// del comando vocale): l'HUD non esegue nulla da solo.
GlassPanel {
    id: root
    property string activitySummary: ""
    property real undoExpiresAt: 0
    property real nowSeconds: Date.now() / 1000
    readonly property bool undoAvailable: undoExpiresAt > nowSeconds
    signal undoRequested()
    signal stopRequested()
    implicitHeight: 48

    Timer {
        interval: 1000
        repeat: true
        running: root.undoExpiresAt > 0
        onTriggered: root.nowSeconds = Date.now() / 1000
    }

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: 14
        anchors.rightMargin: 8
        spacing: 8

        Text {
            Layout.fillWidth: true
            text: root.activitySummary.length > 0
                ? root.activitySummary + (root.undoAvailable
                    ? qsTr(" · annullabile per %1 s").arg(Math.max(0, Math.floor(root.undoExpiresAt - root.nowSeconds))) : "")
                : qsTr("Nessuna azione recente")
            color: root.activitySummary.length > 0 ? Theme.text : Theme.textFaint
            font.pixelSize: Theme.fontSmall
            elide: Text.ElideRight
            Accessible.name: text
        }
        Button {
            visible: root.undoAvailable
            text: qsTr("Annulla")
            Accessible.name: qsTr("Annulla l'ultima azione")
            onClicked: root.undoRequested()
        }
        Button {
            id: stopButton
            text: qsTr("Ferma tutto")
            Accessible.name: qsTr("Ferma tutto: interrompe subito agenti e automazioni (Ctrl+Alt+Fine)")
            palette.button: "#7f1d1d"
            palette.buttonText: "#fecaca"
            onClicked: root.stopRequested()
        }
    }
}
