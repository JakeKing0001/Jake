import QtQuick
import QtQuick.Effects
import JakeHud

// Pannello di vetro condiviso (vedi Theme.qml): ogni pannello dell'HUD e' un GlassPanel, cosi' il materiale resta
// uno solo. `hovered` serve al click-through per pannello di Main.qml (F4.2.1).
//
// Seconda prova reale del 27/09/2026 ("pannelli da rettangolo UI standard, niente vetro, niente profondita'"). Il
// blur del desktop dietro una finestra a livelli non e' possibile (F4.3, documentato), ma la luce DELL'HUD si': dietro
// ogni pannello c'e' la stessa aura e la stessa polvere che circondano l'orb (Theme.backdrop, gia' sfocate una volta
// per tutti in Main.qml) - il vetro le "rifrange", sfocate e ritagliate alla sua forma. Poi la tinta scura che tiene
// il testo leggibile, un orlo che prende luce in alto, un bagliore del colore dello stato dal basso e un'ombra
// morbida che stacca il pannello dal fondo. In qualita' bassa restano tinta, orlo e bagliore.
// F4.3.1: dove Windows lo permette, dietro al pannello c'e' il desktop vero sfocato (DesktopGlass in Main.qml, una
// finestra nativa di DWM per pannello): la rifrazione simulata si spegne e la tinta si alleggerisce (Theme).
Item {
    id: panel
    property alias hovered: hover.hovered
    property color accentColor: "transparent"   // bordo colorato per pannelli che chiedono attenzione
    property real radius: Theme.radius
    default property alias contentData: contentLayer.data

    HoverHandler { id: hover }
    Component.onCompleted: Theme.registerPanel(panel)
    Component.onDestruction: Theme.unregisterPanel(panel)

    // ombra morbida a strati (stacca il pannello dal desktop, da' profondita'): niente effetto sfocato, che su una
    // finestra trasparente lasciava un blocco scuro squadrato sotto il pannello (demo reale del 28/09/2026)
    Repeater {
        model: Theme.highContrast ? 0 : 4
        Rectangle {
            required property int index
            anchors.fill: parent
            anchors.margins: -(index + 1) * 3
            anchors.topMargin: -(index + 1) * 3 + 6
            radius: panel.radius + (index + 1) * 3
            color: Qt.rgba(0, 0, 0, 0.11 - index * 0.022)
        }
    }

    // rifrazione: la luce sfocata dell'HUD dietro al pannello, ritagliata alla sua forma
    ShaderEffectSource {
        id: refraction
        anchors.fill: parent
        visible: false
        live: true
        sourceItem: Theme.richGlass && !Theme.desktopGlass ? Theme.backdrop : null
        property point origin: Qt.point(0, 0)
        sourceRect: Qt.rect(origin.x, origin.y, panel.width, panel.height)
        function sync() {
            if (Theme.backdrop) origin = panel.mapToItem(Theme.backdrop, 0, 0);
        }
    }
    // i pannelli si spostano quando altri compaiono o spariscono: la posizione nel fondo si riallinea da sola
    Timer { interval: 250; repeat: true; running: Theme.richGlass && !Theme.desktopGlass && panel.visible; triggeredOnStart: true
            onTriggered: refraction.sync() }
    Rectangle {
        id: glassMask
        anchors.fill: parent
        radius: panel.radius
        visible: false
        layer.enabled: Theme.richGlass
    }
    MultiEffect {
        anchors.fill: parent
        visible: Theme.richGlass && !Theme.desktopGlass && !Theme.highContrast && Theme.backdrop !== null
        source: refraction
        maskEnabled: true
        maskSource: glassMask
        brightness: 0.08
        saturation: 0.2
        opacity: 0.95
    }

    // tinta del vetro: scura quanto basta per leggere il testo sopra qualunque desktop
    Rectangle {
        anchors.fill: parent
        radius: panel.radius
        gradient: Gradient {
            GradientStop { position: 0.0; color: Theme.glassTop }
            GradientStop { position: 1.0; color: Theme.glassBottom }
        }
    }
    // bagliore del colore dello stato che sale dal basso: il vetro "sente" cosa fa Jake
    Rectangle {
        anchors.fill: parent
        radius: panel.radius
        visible: !Theme.highContrast
        gradient: Gradient {
            GradientStop { position: 0.55; color: "transparent" }
            GradientStop { position: 1.0; color: Qt.rgba(Theme.stateColor.r, Theme.stateColor.g, Theme.stateColor.b, 0.10) }
        }
    }
    // riflesso interno in alto
    Rectangle {
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.margins: 1
        height: Math.min(parent.height / 2, 34)
        radius: panel.radius
        gradient: Gradient {
            GradientStop { position: 0.0; color: Theme.glassHighlight }
            GradientStop { position: 1.0; color: "transparent" }
        }
    }
    // orlo: sottile ovunque, piu' luminoso sul bordo superiore dove "prende luce"
    Rectangle {
        anchors.fill: parent
        radius: panel.radius
        color: "transparent"
        border.width: 1
        border.color: panel.accentColor.a > 0 ? panel.accentColor : Theme.glassBorder
    }
    Rectangle {
        anchors.top: parent.top
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.topMargin: 0.5
        width: parent.width - panel.radius * 2
        height: 1
        visible: !Theme.highContrast
        gradient: Gradient {
            orientation: Gradient.Horizontal
            GradientStop { position: 0.0; color: "transparent" }
            GradientStop { position: 0.5; color: Theme.glassRim }
            GradientStop { position: 1.0; color: "transparent" }
        }
    }

    Item {
        id: contentLayer
        anchors.fill: parent
    }
}
