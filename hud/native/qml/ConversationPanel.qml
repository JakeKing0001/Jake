import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import JakeHud

// Conversazione (F4.5.1): di default solo l'ultimo scambio e la riga "live" (trascrizione, passo in corso,
// prova, diagnosi) - l'orb resta la presenza principale. "Cronologia" apre l'elenco completo della sessione
// visibile; la cronologia vera resta nella memoria di Jake, qui niente persistenza.
GlassPanel {
    id: root
    property bool expanded: false
    property string transcriptText: ""
    property bool transcriptFinal: false
    property string stepDescription: ""
    property string evidenceSummary: ""
    property string inspectionReason: ""
    property string lastUser: ""
    property string lastJake: ""
    readonly property bool hasContent: model.count > 0 || transcriptText.length > 0 || stepDescription.length > 0
        || evidenceSummary.length > 0 || inspectionReason.length > 0
    implicitHeight: expanded ? 320 : Math.min(150, header.implicitHeight + summary.implicitHeight + live.implicitHeight + 28)

    function append(senderRole, messageText) {
        // "senderRole"/"messageText": nomi diversi da proprieta' di Text, evita il binding su se stessi.
        model.append({ senderRole: senderRole, messageText: messageText });
        if (senderRole === "user") { lastUser = messageText; lastJake = ""; }
        if (senderRole === "jake") lastJake = messageText;
        list.positionViewAtEnd();
    }

    ListModel { id: model }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 12
        spacing: 6

        RowLayout {
            id: header
            Layout.fillWidth: true
            Text {
                text: root.expanded ? qsTr("Cronologia") : qsTr("Conversazione")
                color: Theme.textFaint
                font.pixelSize: Theme.fontSmall
                font.bold: true
                Layout.fillWidth: true
            }
            Button {
                id: toggle
                flat: true
                visible: model.count > 2
                text: root.expanded ? qsTr("Riduci") : qsTr("Cronologia (%1)").arg(model.count)
                font.pixelSize: Theme.fontSmall
                Accessible.name: root.expanded ? qsTr("Riduci la cronologia") : qsTr("Apri la cronologia")
                onClicked: root.expanded = !root.expanded
            }
        }

        // ultimo scambio (compatto)
        Column {
            id: summary
            visible: !root.expanded
            Layout.fillWidth: true
            spacing: 4
            Text {
                width: parent.width
                visible: root.lastUser.length > 0
                text: qsTr("Tu: %1").arg(root.lastUser)
                color: Theme.accent
                font.pixelSize: Theme.fontSmall
                elide: Text.ElideRight
            }
            Text {
                width: parent.width
                visible: root.lastJake.length > 0
                text: root.lastJake
                color: Theme.text
                font.pixelSize: Theme.fontBody
                wrapMode: Text.WordWrap
                maximumLineCount: 3
                elide: Text.ElideRight
            }
            Text {
                visible: !root.hasContent
                text: qsTr("Di' \"Jake\" o scrivi un comando qui sotto.")
                color: Theme.textFaint
                font.pixelSize: Theme.fontSmall
            }
        }

        ListView {
            id: list
            visible: root.expanded
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 8
            clip: true
            model: model
            ScrollBar.vertical: ScrollBar {}
            delegate: Column {
                width: list.width - 8
                spacing: 2
                Text {
                    text: senderRole === "user" ? qsTr("Tu") : qsTr("Jake")
                    font.pixelSize: 11
                    font.bold: true
                    color: senderRole === "user" ? Theme.accent : Theme.ok
                }
                Text {
                    width: parent.width
                    text: messageText
                    wrapMode: Text.WordWrap
                    color: Theme.text
                    font.pixelSize: Theme.fontBody
                }
            }
        }

        // riga live: trascrizione (partial in corsivo), passo, prova, diagnosi (F2.2.7, F4.5.2, F4.5.4)
        Column {
            id: live
            Layout.fillWidth: true
            spacing: 2
            Text {
                width: parent.width
                visible: root.transcriptText.length > 0 && !root.transcriptFinal
                text: root.transcriptText
                font.italic: true
                color: Theme.textMuted
                wrapMode: Text.WordWrap
                font.pixelSize: Theme.fontSmall
                Accessible.name: qsTr("Sto sentendo: ") + text
            }
            Text {
                width: parent.width
                visible: root.stepDescription.length > 0
                text: qsTr("Passo: %1").arg(root.stepDescription)
                color: Theme.warn
                wrapMode: Text.WordWrap
                font.pixelSize: Theme.fontSmall
            }
            Text {
                width: parent.width
                visible: root.evidenceSummary.length > 0
                text: root.evidenceSummary
                color: Theme.ok
                font.pixelSize: Theme.fontSmall
            }
            Text {
                width: parent.width
                visible: root.inspectionReason.length > 0
                text: qsTr("Non trovato: %1").arg(root.inspectionReason)
                color: Theme.attention
                wrapMode: Text.WordWrap
                font.pixelSize: Theme.fontSmall
            }
        }
    }
}
