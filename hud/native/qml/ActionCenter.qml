import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import JakeHud

// Action Center compatto (F4.6): ultima azione con esito/verifica, Annulla finche' l'undo non scade (con
// il tempo rimasto), e "Ferma tutto". Tutto passa dagli skill del core (stessa policy del comando vocale):
// l'HUD non esegue nulla da solo. Due righe (testo sopra, azioni sotto): nella colonna accanto all'orb una riga
// sola troncava il testo ("GET_WEATHE...", demo reale del 28/09/2026).
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
    implicitHeight: layout.implicitHeight + 24

    Timer {
        interval: 1000
        repeat: true
        running: root.undoExpiresAt > 0 && !root.undoExpired
        onTriggered: root.nowSeconds = Date.now() / 1000
    }

    ColumnLayout {
        id: layout
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: 12
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
            wrapMode: Text.WordWrap
            maximumLineCount: 2
            elide: Text.ElideRight
            Accessible.name: text
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 6
            Button {
                visible: root.activityDetails.length > 0
                flat: true
                text: root.showDetails ? qsTr("Meno") : qsTr("Dettagli")
                font.pixelSize: Theme.fontSmall
                Accessible.name: root.showDetails ? qsTr("Nascondi i dettagli dell'ultima azione") : qsTr("Mostra i dettagli dell'ultima azione")
                onClicked: root.showDetails = !root.showDetails
            }
            Item { Layout.fillWidth: true }
            Button {
                visible: root.retryAvailable
                text: qsTr("Riprova")
                font.pixelSize: Theme.fontSmall
                Accessible.name: qsTr("Riprova l'ultima azione fallita")
                onClicked: root.retryRequested()
            }
            Button {
                visible: root.undoAvailable
                text: qsTr("Annulla")
                font.pixelSize: Theme.fontSmall
                Accessible.name: qsTr("Annulla l'ultima azione")
                onClicked: root.undoRequested()
            }
            Button {
                id: stopButton
                text: qsTr("Ferma tutto")
                font.pixelSize: Theme.fontSmall
                Accessible.name: qsTr("Ferma tutto: interrompe subito agenti e automazioni (Ctrl+Alt+Fine)")
                palette.button: "#7f1d1d"
                palette.buttonText: "#fecaca"
                onClicked: root.stopRequested()
            }
        }

        Text {
            id: detailsText
            Layout.fillWidth: true
            visible: root.showDetails && root.activityDetails.length > 0
            text: root.activityDetails
            color: Theme.textMuted
            font.pixelSize: Theme.fontTiny
            wrapMode: Text.WordWrap
            Accessible.name: text
        }
    }
}
