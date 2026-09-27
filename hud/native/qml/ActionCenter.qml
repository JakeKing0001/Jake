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
    property string activityDetails: ""
    property bool showDetails: false
    property real undoExpiresAt: 0
    property real nowSeconds: Date.now() / 1000
    readonly property bool undoAvailable: undoExpiresAt > nowSeconds
    // F4.6.4: l'undo non sparisce in silenzio - dopo la scadenza si dice perche' non c'e' piu'
    readonly property bool undoExpired: undoExpiresAt > 0 && undoExpiresAt <= nowSeconds
    // F4.6.2: l'ultima azione e' fallita -> "Riprova" (il core decide se e' sicuro e se chiedere prima)
    property bool retryAvailable: false
    signal undoRequested()
    signal retryRequested()
    signal stopRequested()
    implicitHeight: showDetails && activityDetails.length > 0 ? 48 + detailsText.implicitHeight + 10 : 48

    Timer {
        interval: 1000
        repeat: true
        running: root.undoExpiresAt > 0 && !root.undoExpired
        onTriggered: root.nowSeconds = Date.now() / 1000
    }

    Text {
        id: detailsText
        visible: root.showDetails && root.activityDetails.length > 0
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.margins: 12
        text: root.activityDetails
        color: Theme.textMuted
        font.pixelSize: Theme.fontTiny
        wrapMode: Text.WordWrap
        Accessible.name: text
    }

    RowLayout {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        height: 48
        anchors.leftMargin: 14
        anchors.rightMargin: 8
        spacing: 8

        Text {
            Layout.fillWidth: true
            text: root.activitySummary.length > 0
                ? root.activitySummary + (root.undoAvailable
                    ? qsTr(" · annullabile per %1 s").arg(Math.max(0, Math.floor(root.undoExpiresAt - root.nowSeconds)))
                    : root.undoExpired ? qsTr(" · annullamento scaduto alle %1").arg(
                        Qt.formatTime(new Date(root.undoExpiresAt * 1000), "hh:mm")) : "")
                : qsTr("Nessuna azione recente")
            color: root.activitySummary.length > 0 ? Theme.text : Theme.textFaint
            font.pixelSize: Theme.fontSmall
            elide: Text.ElideRight
            Accessible.name: text
        }
        Button {
            visible: root.activityDetails.length > 0
            flat: true
            text: root.showDetails ? qsTr("Meno") : qsTr("Dettagli")
            Accessible.name: root.showDetails ? qsTr("Nascondi i dettagli dell'ultima azione") : qsTr("Mostra i dettagli dell'ultima azione")
            onClicked: root.showDetails = !root.showDetails
        }
        Button {
            visible: root.retryAvailable
            text: qsTr("Riprova")
            Accessible.name: qsTr("Riprova l'ultima azione fallita")
            onClicked: root.retryRequested()
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
