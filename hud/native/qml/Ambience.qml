import QtQuick
import QtQuick.Shapes
import QtQuick.Particles
import QtQuick.Effects
import JakeHud

// Ambiente dell'HUD (seconda prova reale del 27/09/2026: "manca profondita', manca la liquid dust"). Dietro a tutto:
// un'aura del colore dello stato centrata sull'orb e una polvere luminosa sottile che vive nello spazio attorno.
// Anche qui lo stato si riconosce dal MOVIMENTO: a riposo la polvere deriva appena; in ascolto converge verso l'orb;
// mentre pensa gira in turbolenza; mentre esegue sale; mentre parla pulsa con la voce; in errore si disperde; in pausa
// quasi si ferma. Movimento ridotto (Windows): polvere ferma e aura fissa, resta solo il colore.
Item {
    id: root
    property string state: "IDLE"
    property real level: 0              // inviluppo audio gia' levigato dall'orb (0..1)
    property color tint: "#3ddc84"
    property point center: Qt.point(width / 2, height / 2)
    property real orbRadius: 180
    property bool reducedMotion: false
    property bool rich: true            // qualita' alta: piu' polvere

    readonly property bool audioState: state === "LISTENING" || state === "SPEAKING" || state === "DICTATION"
    property real pulse: 0
    SequentialAnimation on pulse {
        running: !root.reducedMotion
        loops: Animation.Infinite
        NumberAnimation { to: 1; duration: root.state === "IDLE" ? 4200 : 1600; easing.type: Easing.InOutSine }
        NumberAnimation { to: 0; duration: root.state === "IDLE" ? 4200 : 1600; easing.type: Easing.InOutSine }
    }
    readonly property real auraStrength: (state === "PAUSED" ? 0.08 : state === "IDLE" ? 0.16 : 0.26)
        + pulse * 0.05 + level * 0.22

    // aura: due aloni concentrici, il piu' largo molto tenue
    Shape {
        anchors.fill: parent
        ShapePath {
            strokeWidth: -1
            fillGradient: RadialGradient {
                centerX: root.center.x; centerY: root.center.y
                focalX: root.center.x; focalY: root.center.y
                // mai oltre il bordo della finestra: il gradiente deve arrivare a zero prima, o si vede lo spigolo
                centerRadius: Math.min(root.orbRadius * (1.9 + root.level * 0.5), root.center.x, root.width - root.center.x,
                                       root.center.y, root.height - root.center.y)
                GradientStop { position: 0.0; color: Qt.rgba(root.tint.r, root.tint.g, root.tint.b, root.auraStrength) }
                GradientStop { position: 0.45; color: Qt.rgba(root.tint.r, root.tint.g, root.tint.b, root.auraStrength * 0.35) }
                GradientStop { position: 1.0; color: "transparent" }
            }
            startX: 0; startY: 0
            PathLine { x: root.width; y: 0 }
            PathLine { x: root.width; y: root.height }
            PathLine { x: 0; y: root.height }
            PathLine { x: 0; y: 0 }
        }
    }

    // la polvere e' bianca e la colora dal vivo la colorizzazione del livello: il colore di una particella 2D si decide
    // alla nascita e la polvere vive 7 s, quindi senza questo un cambio di stato (un errore!) restava del colore vecchio
    Item {
        id: dustLayer
        anchors.fill: parent
        layer.enabled: true
        layer.effect: MultiEffect {
            colorization: 1.0
            colorizationColor: Qt.lighter(root.tint, 1.25)
        }
        ParticleSystem {
            id: dust
            anchors.fill: parent
            running: !root.reducedMotion && root.visible
        }
        ImageParticle {
            system: dust
            source: "assets/particle.png"
            color: "white"
            alpha: 0.5
            alphaVariation: 0.3
            entryEffect: ImageParticle.Fade
        }
    }
    // polvere su tutta la scena, fitta vicino all'orb e rada ai bordi
    Emitter {
        system: dust
        anchors.fill: parent
        shape: EllipseShape { fill: true }
        emitRate: (root.rich ? 26 : 10) * (root.state === "PAUSED" ? 0.3 : 1) * (1 + root.level * 1.5)
        lifeSpan: 7000
        lifeSpanVariation: 2500
        size: 3
        sizeVariation: 3
        endSize: 1
        velocity: AngleDirection {
            angleVariation: 360
            magnitude: root.state === "ERROR" ? 60 : root.state === "PAUSED" ? 2 : 7
            magnitudeVariation: root.state === "ERROR" ? 40 : 6
        }
    }
    // pensiero: turbolenza; errore: dispersione piu' forte
    Turbulence {
        system: dust
        anchors.fill: parent
        strength: root.state === "THINKING" ? 70 : root.state === "ERROR" ? 90 : root.state === "TRANSCRIBING" ? 30 : 8
    }
    // ascolto e trascrizione: la polvere converge verso l'orb (piu' forte quando la voce e' alta)
    Attractor {
        system: dust
        anchors.fill: parent
        pointX: root.center.x
        pointY: root.center.y
        affectedParameter: Attractor.Acceleration
        proportionalToDistance: Attractor.Linear
        strength: root.state === "LISTENING" || root.state === "DICTATION" ? 0.25 + root.level * 0.6
            : root.state === "TRANSCRIBING" ? 0.45 : 0
    }
    // esecuzione: flusso ordinato verso l'alto
    Gravity {
        system: dust
        anchors.fill: parent
        angle: 270
        magnitude: root.state === "EXECUTING" ? 22 : 0
    }
    // voce di Jake: la polvere vicina all'orb viene spinta fuori a ogni sillaba
    Friction {
        system: dust
        anchors.fill: parent
        factor: root.state === "WAITING" || root.state === "PAUSED" ? 0.8 : 0.05
    }
    Affector {
        system: dust
        anchors.fill: parent
        enabled: root.state === "SPEAKING" && root.level > 0.05
        onAffectParticles: (particles, dt) => {
            for (let i = 0; i < particles.length; ++i) {
                const p = particles[i];
                const dx = p.x - root.center.x, dy = p.y - root.center.y;
                const d = Math.max(20, Math.sqrt(dx * dx + dy * dy));
                if (d > root.orbRadius * 2.2) continue;
                const push = root.level * 90 * dt * (1 - d / (root.orbRadius * 2.2));
                p.vx += dx / d * push * 10;
                p.vy += dy / d * push * 10;
                p.update = true;
            }
        }
    }
}
