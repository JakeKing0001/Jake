import QtQuick
import JakeHud

// Pannello di vetro condiviso (vedi Theme.qml): ogni pannello dell'HUD e' un GlassPanel, cosi' il
// materiale resta uno solo. `hovered` serve al click-through per pannello di Main.qml (F4.2.1).
Rectangle {
    id: panel
    property alias hovered: hover.hovered
    property color accentColor: "transparent"   // bordo colorato per pannelli che chiedono attenzione
    radius: Theme.radius
    border.width: 1
    border.color: accentColor.a > 0 ? accentColor : Theme.glassBorder
    gradient: Gradient {
        GradientStop { position: 0.0; color: Theme.glassTop }
        GradientStop { position: 1.0; color: Theme.glassBottom }
    }

    HoverHandler { id: hover }

    // riflesso sottile sul bordo superiore: da' materia al vetro senza effetti costosi
    Rectangle {
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.margins: 1
        height: Math.min(parent.height / 2, 28)
        radius: panel.radius
        color: Theme.glassHighlight
        opacity: 0.8
    }
}
