import QtQuick

// Orb centrale (fase 4.9.2): solo colore + un impulso semplice per stato, niente particelle/
// rifrazione/shader (quella e' la fase 4.9.7, "Orb 2.0"). Riflette lo stesso vocabolario di
// core/hud_protocol.py (EventType) esposto da JakeClient.state.
Item {
    id: root
    property string state: "IDLE"
    width: 120
    height: 120
    // F4.2.1: vedi lo stesso alias in StatusPanel.qml - l'orb non gestisce ancora click propri,
    // ma resta comunque un'area "del pannello", non uno sfondo click-through.
    property alias hovered: hoverHandler.hovered

    HoverHandler {
        id: hoverHandler
    }

    readonly property color stateColor: {
        switch (state) {
        case "LISTENING": return "#4fd1ff";
        case "THINKING": return "#a78bfa";
        case "EXECUTING": return "#facc15";
        case "ERROR": return "#f87171";
        case "PAUSED": return "#6b7280";
        default: return "#3ddc84"; // IDLE e stati non mappati esplicitamente
        }
    }

    readonly property bool pulsing: state === "LISTENING" || state === "THINKING" || state === "EXECUTING"

    Rectangle {
        id: glow
        anchors.centerIn: parent
        width: parent.width
        height: parent.height
        radius: width / 2
        color: root.stateColor
        opacity: 0.25

        SequentialAnimation on scale {
            running: root.pulsing
            loops: Animation.Infinite
            NumberAnimation { from: 1.0; to: 1.25; duration: 900; easing.type: Easing.InOutQuad }
            NumberAnimation { from: 1.25; to: 1.0; duration: 900; easing.type: Easing.InOutQuad }
        }
    }

    Rectangle {
        anchors.centerIn: parent
        width: parent.width * 0.6
        height: parent.height * 0.6
        radius: width / 2
        color: root.stateColor

        Behavior on color { ColorAnimation { duration: 250 } }
    }
}
