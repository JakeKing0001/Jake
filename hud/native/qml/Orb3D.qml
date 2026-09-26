import QtQuick
import QtQuick3D
import QtQuick3D.Particles3D

// F4.4.7/F4.4.8 - Orb 3D: scena Qt Quick 3D reale (camera prospettica, nucleo volumetrico emissivo, guscio
// luminoso) con una particle shell 3D sulla superficie di una sfera. Tutto deriva dallo stato reale del
// core (JakeClient.state) e dall'esito dell'ultima azione (JakeClient.lastOutcome): nessun successo
// anticipato, nessuna attesa nascosta.
//
// Interrupt-safe (F4.4.3): la rotazione e' integrata a ogni frame (angolo += velocita' * dt) e ogni
// parametro ha un Behavior: un cambio di stato riparte dal valore CORRENTE verso il nuovo bersaglio, mai
// un salto o un'animazione che deve prima finire. Reduced motion (F4.4.5): niente rotazione, respiro ne'
// deriva delle particelle; resta forma + colore + etichetta testuale.
Item {
    id: root
    property string state: "IDLE"
    property string outcome: ""
    property real level: 0            // 0..1, livello audio reale (mai audio), se disponibile
    property bool reducedMotion: false
    property string quality: "high"    // "high" | "low"
    property alias hovered: hoverHandler.hovered
    width: 220
    height: 220

    HoverHandler { id: hoverHandler }

    // ---- parametri per stato ----------------------------------------------------------------------
    readonly property color stateColor: {
        switch (state) {
        case "LISTENING": return "#4fd1ff";
        case "THINKING": return "#a78bfa";
        case "EXECUTING": return "#facc15";
        case "SPEAKING": return "#38bdf8";
        case "WAITING": return "#fb923c";
        case "DICTATION": return "#f472b6";
        case "ERROR": return "#f87171";
        case "PAUSED": return "#6b7280";
        default: return "#3ddc84";
        }
    }
    readonly property real targetShell: {
        switch (state) {
        case "LISTENING": return 1.12 + level * 0.25;
        case "SPEAKING": return 1.06 + level * 0.18;
        case "THINKING": return 0.92;
        case "EXECUTING": return 1.02;
        case "WAITING": return 1.0;
        case "ERROR": return 1.08;
        case "PAUSED": return 0.9;
        default: return 1.0;
        }
    }
    readonly property real targetSpin: {       // gradi al secondo
        switch (state) {
        case "THINKING": return 70;
        case "EXECUTING": return 40;
        case "LISTENING": return 18;
        case "SPEAKING": return 22;
        case "WAITING": return 6;
        case "PAUSED": return 0;
        case "ERROR": return 0;
        default: return 10;
        }
    }
    readonly property real targetDrift: {      // velocita' casuale delle particelle (organica)
        switch (state) {
        case "THINKING": return 26;
        case "EXECUTING": return 14;
        case "ERROR": return 40;
        case "WAITING": return 4;
        default: return 8;
        }
    }
    readonly property real breathPeriod: state === "WAITING" ? 2600 : state === "IDLE" ? 4200 : 1800

    property real shell: targetShell
    property real spin: reducedMotion ? 0 : targetSpin
    property real drift: reducedMotion ? 0 : targetDrift
    property color glowColor: stateColor
    property real outcomeKick: 0                // >0 compatta (success), <0 disperde (error)
    Behavior on shell { NumberAnimation { duration: 450; easing.type: Easing.OutCubic } }
    Behavior on spin { NumberAnimation { duration: 700; easing.type: Easing.InOutQuad } }
    Behavior on drift { NumberAnimation { duration: 500 } }
    Behavior on glowColor { ColorAnimation { duration: 350 } }
    Behavior on outcomeKick { NumberAnimation { duration: 420; easing.type: Easing.OutQuad } }

    property real angle: 0
    property real breath: 0
    FrameAnimation {
        running: !root.reducedMotion && root.visible
        onTriggered: {
            root.angle = (root.angle + root.spin * frameTime) % 360;
            root.breath = Math.sin(elapsedTime * 2 * Math.PI * 1000 / root.breathPeriod);
        }
    }

    onOutcomeChanged: {
        if (outcome.length === 0) return;
        outcomeKick = outcome === "success" ? 0.12 : outcome === "error" ? -0.18 : 0.05;
        kickReset.restart();
    }
    Timer { id: kickReset; interval: 900; onTriggered: root.outcomeKick = 0 }

    readonly property real coreScale: 0.62 * (1 + (reducedMotion ? 0 : breath * 0.035)) * (1 - outcomeKick * 0.5)
    readonly property real shellRadius: 78 * (shell - outcomeKick)

    readonly property string stateLabel: {
        switch (state) {
        case "LISTENING": return qsTr("In ascolto");
        case "THINKING": return qsTr("Sto pensando");
        case "EXECUTING": return qsTr("Sto eseguendo");
        case "SPEAKING": return qsTr("Sto parlando");
        case "WAITING": return qsTr("Attendo conferma");
        case "DICTATION": return qsTr("Dettatura");
        case "ERROR": return qsTr("Errore");
        case "PAUSED": return qsTr("In pausa");
        default: return qsTr("Pronto");
        }
    }
    Accessible.role: Accessible.Indicator
    Accessible.name: qsTr("Stato di Jake: %1").arg(stateLabel)

    Text {
        anchors.bottom: parent.bottom
        anchors.horizontalCenter: parent.horizontalCenter
        text: root.stateLabel
        color: "#e5e7eb"
        font.pixelSize: 13
        style: Text.Raised
        styleColor: "#80000000"
        z: 1
    }

    View3D {
        id: view
        anchors.fill: parent
        anchors.bottomMargin: 18
        environment: SceneEnvironment {
            backgroundMode: SceneEnvironment.Transparent
            antialiasingMode: root.quality === "high" ? SceneEnvironment.MSAA : SceneEnvironment.NoAA
            antialiasingQuality: SceneEnvironment.High
        }

        PerspectiveCamera { position: Qt.vector3d(0, 0, 320); clipNear: 1; clipFar: 1000 }
        DirectionalLight { eulerRotation.x: -30; eulerRotation.y: -40; brightness: 1.2 }
        PointLight { position: Qt.vector3d(0, 0, 60); color: root.glowColor; brightness: 1.1 }

        Node {
            id: orb
            eulerRotation.y: root.angle
            eulerRotation.x: 18

            // nucleo volumetrico: sfera emissiva opaca + guscio traslucido che da' profondita'
            Model {
                source: "#Sphere"
                scale: Qt.vector3d(root.coreScale, root.coreScale, root.coreScale)
                materials: PrincipledMaterial {
                    baseColor: root.glowColor
                    emissiveFactor: Qt.vector3d(root.glowColor.r * 0.45, root.glowColor.g * 0.45, root.glowColor.b * 0.45)
                    roughness: 0.35
                    metalness: 0.0
                }
            }
            Model {
                source: "#Sphere"
                scale: Qt.vector3d(root.coreScale * 1.35, root.coreScale * 1.35, root.coreScale * 1.35)
                materials: PrincipledMaterial {
                    baseColor: Qt.rgba(root.glowColor.r, root.glowColor.g, root.glowColor.b, 0.18)
                    emissiveFactor: Qt.vector3d(root.glowColor.r * 0.35, root.glowColor.g * 0.35, root.glowColor.b * 0.35)
                    alphaMode: PrincipledMaterial.Blend
                    cullMode: Material.FrontFaceCulling
                }
            }

            // particle shell 3D sulla superficie di una sfera
            ParticleSystem3D {
                id: particles
                running: root.visible
                SpriteParticle3D {
                    id: sprite
                    sprite: Texture { source: "assets/particle.png" }
                    maxAmount: root.quality === "high" ? 1400 : 450
                    color: root.glowColor
                    colorVariation: Qt.vector4d(0.12, 0.12, 0.12, 0.2)
                    fadeInDuration: 300
                    fadeOutDuration: 500
                    billboard: true
                    blendMode: SpriteParticle3D.Screen
                }
                ParticleEmitter3D {
                    particle: sprite
                    shape: ParticleShape3D {
                        type: ParticleShape3D.Sphere
                        fill: false
                        extents: Qt.vector3d(root.shellRadius, root.shellRadius, root.shellRadius)
                    }
                    emitRate: (root.quality === "high" ? 520 : 170) * (root.state === "THINKING" ? 1.4 : 1.0)
                    lifeSpan: 2600
                    lifeSpanVariation: 700
                    particleScale: root.quality === "high" ? 1.6 : 2.2
                    particleScaleVariation: 0.7
                    velocity: VectorDirection3D {
                        direction: root.state === "EXECUTING" ? Qt.vector3d(0, root.drift * 1.5, 0) : Qt.vector3d(0, 0, 0)
                        directionVariation: Qt.vector3d(root.drift, root.drift, root.drift)
                    }
                }
            }
        }
    }
}
