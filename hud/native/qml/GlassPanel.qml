pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Effects
import JakeHud

// Pannello di vetro condiviso (vedi Theme.qml): ogni pannello dell'HUD e' un GlassPanel, cosi' il materiale resta
// uno solo. `hovered` serve al click-through per pannello di Main.qml (F4.2.1).
//
// Vetro liquido ispirato all'implementazione Python HUD: blur del desktop (via DWM quando disponibile),
// tinta, gradiente multistop, grana sottile, riflesso speculare, ombra interna, bordo luminoso
// e highlight superiore per effetto vetro curvo che cattura la luce.
Item {
    id: panel
    property alias hovered: hover.hovered
    property color accentColor: "transparent"   // bordo colorato per pannelli che chiedono attenzione
    property real radius: Theme.radius
    default property alias contentData: contentLayer.data
    readonly property bool richEffects: Theme.richGlass && !Theme.highContrast && panel.visible
    readonly property bool refractionEnabled: richEffects && !Theme.desktopGlass && Theme.backdrop !== null

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
        live: panel.refractionEnabled
        sourceItem: panel.refractionEnabled ? Theme.backdrop : null
        property point origin: Qt.point(0, 0)
        sourceRect: Qt.rect(origin.x, origin.y, panel.width, panel.height)
        function sync() {
            if (Theme.backdrop) origin = panel.mapToItem(Theme.backdrop, 0, 0);
        }
    }
    // i pannelli si spostano quando altri compaiono o spariscono: la posizione nel fondo si riallinea da sola
    Timer { interval: 250; repeat: true; running: panel.refractionEnabled; triggeredOnStart: true
            onTriggered: refraction.sync() }
    Rectangle {
        id: glassMask
        anchors.fill: parent
        radius: panel.radius
        visible: false
        layer.enabled: panel.refractionEnabled
    }
    MultiEffect {
        anchors.fill: parent
        visible: panel.refractionEnabled
        source: panel.refractionEnabled ? refraction : null
        maskEnabled: true
        maskSource: glassMask
        brightness: 0.08
        saturation: 0.2
        opacity: 0.95
    }

    // La tinta leggibile non e' un effetto opzionale: resta anche a batteria e in contrasto elevato.
    Rectangle {
        id: tintGradientLayer
        objectName: "glassTint"
        anchors.fill: parent
        radius: panel.radius
        gradient: Gradient {
            GradientStop { position: 0.0; color: Theme.glassTop }
            GradientStop { position: 1.0; color: Theme.glassBottom }
        }
    }

    // Un'unica superficie statica: gradiente multistop, grana, riflesso e ombra interna. Canvas usa solo
    // QtQuick (niente OpacityMask Qt5Compat o RadialGradient assegnato a Rectangle.gradient).
    // Si ridisegna solo al resize/cambio raggio, non a ogni frame dell'orb; il Loader libera la texture
    // quando il pannello e' nascosto, la qualita' scende o il contrasto elevato viene attivato.
    Loader {
        id: surfaceLoader
        objectName: "glassSurface"
        anchors.fill: parent
        active: panel.richEffects
        sourceComponent: Canvas {
            id: surface
            objectName: "glassSurfaceCanvas"
            property real cornerRadius: panel.radius
            onWidthChanged: requestPaint()
            onHeightChanged: requestPaint()
            onCornerRadiusChanged: requestPaint()
            onPaint: {
                const ctx = getContext("2d");
                ctx.reset();
                if (width <= 0 || height <= 0) return;
                const r = Math.max(0, Math.min(cornerRadius, width / 2, height / 2));
                ctx.beginPath();
                ctx.roundedRect(0, 0, width, height, r, r);
                ctx.clip();

                const tint = ctx.createLinearGradient(0, 0, 0, height);
                tint.addColorStop(0, Qt.rgba(0.29, 0.57, 1.0, 0.086));
                tint.addColorStop(0.55, Qt.rgba(0.14, 0.25, 0.53, 0.071));
                tint.addColorStop(1, Qt.rgba(0.05, 0.11, 0.29, 0.118));
                ctx.fillStyle = tint;
                ctx.fillRect(0, 0, width, height);
                ctx.fillStyle = Qt.rgba(0.04, 0.07, 0.17, 0.133);
                ctx.fillRect(0, 0, width, height);

                // Coordinate normalizzate: il riflesso segue anche pannelli molto larghi o molto alti.
                ctx.save();
                ctx.scale(width, height);
                const light = ctx.createRadialGradient(0.2, -0.1, 0, 0.2, -0.1, 0.75);
                light.addColorStop(0, Qt.rgba(1, 1, 1, 0.06));
                light.addColorStop(0.5, Qt.rgba(1, 1, 1, 0.02));
                light.addColorStop(1, "transparent");
                ctx.fillStyle = light;
                ctx.fillRect(0, 0, 1, 1);
                const shade = ctx.createRadialGradient(1.1, 1.2, 0, 1.1, 1.2, 0.8);
                shade.addColorStop(0, Qt.rgba(0.01, 0.02, 0.08, 0.27));
                shade.addColorStop(0.55, Qt.rgba(0.01, 0.02, 0.08, 0.09));
                shade.addColorStop(1, "transparent");
                ctx.fillStyle = shade;
                ctx.fillRect(0, 0, 1, 1);
                ctx.restore();

                // Grana deterministica e limitata: nessun asset mancante, niente rumore animato o loop per pixel.
                let seed = 17;
                ctx.fillStyle = Qt.rgba(1, 1, 1, 0.018);
                for (let i = 0; i < 512; ++i) {
                    seed = (seed * 16807) % 2147483647;
                    const x = seed % Math.ceil(width);
                    seed = (seed * 16807) % 2147483647;
                    ctx.fillRect(x, seed % Math.ceil(height), 1, 1);
                }
            }
        }
    }

    // bagliore del colore dello stato che sale dal basso: il vetro "sente" cosa fa Jake
    Rectangle {
        id: stateGlowLayer
        anchors.fill: parent
        radius: panel.radius
        visible: !Theme.highContrast
        gradient: Gradient {
            GradientStop { position: 0.55; color: "transparent" }
            GradientStop { position: 1.0; color: Qt.rgba(Theme.stateColor.r, Theme.stateColor.g, Theme.stateColor.b, 0.10) }
        }
    }

    // riflesso interno in alto (leggera rifrazione della luce)
    Rectangle {
        id: innerHighlightLayer
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.margins: 1
        height: Math.min(parent.height / 2, 34)
        radius: panel.radius
        visible: !Theme.highContrast
        gradient: Gradient {
            GradientStop { position: 0.0; color: Theme.glassHighlight }
            GradientStop { position: 1.0; color: "transparent" }
        }
    }

    // orlo: sottile ovunque, piu' luminoso sul bordo superiore dove "prende luce"
    // L'highlight separato sotto aggiunge luce all'orlo superiore.
    Rectangle {
        id: borderLayer
        objectName: "glassBorder"
        anchors.fill: parent
        radius: panel.radius
        color: "transparent"
        border.width: 1
        // In contrasto elevato un accento scuro non deve mai cancellare il contorno.
        border.color: Theme.highContrast ? Theme.glassBorder :
                      panel.accentColor.a > 0 ? panel.accentColor : Qt.rgba(0.61, 0.82, 1.0, 0.55)
    }
    // Linea di luce superiore sottile (evidenzia il bordo curvo visto controluce)
    Rectangle {
        id: topHighlightLayer
        anchors.top: parent.top
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.topMargin: 0.5
        width: Math.max(0, parent.width - panel.radius * 2)
        height: 1
        visible: !Theme.highContrast
        gradient: Gradient {
            orientation: Gradient.Horizontal
            GradientStop { position: 0.0; color: "transparent" }
            GradientStop { position: 0.5; color: Qt.rgba(1.0, 1.0, 1.0, 0.25) }   // HIGHLIGHT equivalente
            GradientStop { position: 1.0; color: "transparent" }
        }
    }

    Item {
        id: contentLayer
        anchors.fill: parent
    }
}
