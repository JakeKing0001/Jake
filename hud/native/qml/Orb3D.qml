import QtQuick
import QtQuick3D
import QtQuick3D.Particles3D

// F4.4.7/F4.4.8 - Orb 3D: scena Qt Quick 3D reale (camera prospettica, nucleo volumetrico emissivo, guscio
// luminoso) avvolta da una massa di particelle viva. Tutto deriva dallo stato reale del core (JakeClient.state),
// dal livello audio reale (microfono in ascolto, voce di Jake mentre parla) e dall'esito dell'ultima azione
// (JakeClient.lastOutcome): nessun successo anticipato, nessuna attesa nascosta.
//
// Perche' non sembri "una sfera con tanti puntini" (osservazione reale dell'utente, 27/09/2026): prima le
// particelle nascevano su un'unica superficie con una velocita' casuale minima e ruotavano tutte insieme al nodo
// padre, come un corpo rigido. Ora:
//  - tre strati a profondita' diverse (guscio esterno, volume intermedio pieno, alone interno), fuori dal nodo che
//    ruota: nessuna rotazione rigida condivisa;
//  - ogni strato orbita per conto suo (PointRotator3D) con velocita' e asse propri: rotazione differenziale, e
//    in THINKING un vortice con l'interno piu' veloce dell'esterno;
//  - componente radiale per particella (TargetDirection3D dal centro) e una spinta viva dal centro (Repeller3D)
//    che segue il livello audio;
//  - moto individuale che cambia nel tempo (Wander3D: ampiezza e ritmo unici per ogni particella);
//  - in EXECUTING un flusso ordinato verso l'alto (Gravity3D), in ERROR dispersione e tremolio controllati.
//
// Interrupt-safe (F4.4.3): ogni parametro ha un Behavior, un cambio di stato riparte dal valore CORRENTE. Reduced
// motion (F4.4.5, "Effetti animazione" di Windows spento): niente orbite, deriva ne' spinte - restano forma, colore,
// comparsa/scomparsa delle particelle e l'etichetta testuale.
Item {
    id: root
    property string state: "IDLE"
    property string outcome: ""
    property int outcomeSerial: 0     // cresce a ogni esito: due successi di fila = due reazioni
    property real level: 0            // 0..1, livello audio reale (mai audio), se disponibile
    property bool reducedMotion: false
    property string quality: "high"    // "high" | "low"
    property alias hovered: hoverHandler.hovered
    width: 220
    height: 220

    HoverHandler { id: hoverHandler }

    readonly property bool audioState: state === "LISTENING" || state === "SPEAKING" || state === "DICTATION"
    readonly property real audio: audioState ? level : 0

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
        case "LISTENING": return 1.08 + audio * 0.3;
        case "SPEAKING": return 1.04 + audio * 0.22;
        case "DICTATION": return 1.06 + audio * 0.25;
        case "THINKING": return 0.9;
        case "EXECUTING": return 1.0;
        case "ERROR": return 1.12;
        case "PAUSED": return 0.88;
        default: return 1.0;
        }
    }
    // orbita dello strato esterno e di quello interno (gradi al secondo): segni opposti = contro-rotazione viva,
    // stesso segno e interno piu' veloce = vortice
    readonly property real targetOuterOrbit: {
        switch (state) {
        case "THINKING": return 95;
        case "EXECUTING": return 55;
        case "LISTENING": case "DICTATION": return 22 + audio * 40;
        case "SPEAKING": return 26 + audio * 30;
        case "WAITING": return 7;
        case "PAUSED": return 3;
        case "ERROR": return 4;
        default: return 14;
        }
    }
    readonly property real targetInnerOrbit: {
        switch (state) {
        case "THINKING": return 170;
        case "EXECUTING": return 55;
        case "LISTENING": case "DICTATION": return -16 - audio * 30;
        case "SPEAKING": return -18 - audio * 24;
        case "WAITING": return -5;
        case "PAUSED": return -2;
        case "ERROR": return -6;
        default: return -10;
        }
    }
    // velocita' radiale alla nascita (unita'/s): >0 verso l'esterno, <0 verso il nucleo (spirale del vortice)
    readonly property real targetRadial: {
        switch (state) {
        case "THINKING": return -14;
        case "EXECUTING": return 0;
        case "LISTENING": case "DICTATION": return 5 + audio * 70;
        case "SPEAKING": return 4 + audio * 55;
        case "WAITING": return 2;
        case "ERROR": return 30;
        default: return 4;
        }
    }
    // moto individuale: ampiezza (unita') e ritmo (cicli al secondo) unici per particella
    readonly property real targetWander: {
        switch (state) {
        case "THINKING": return 12;
        case "EXECUTING": return 3;
        case "LISTENING": case "DICTATION": return 8 + audio * 26;
        case "SPEAKING": return 7 + audio * 20;
        case "WAITING": return 5;
        case "ERROR": return 28;
        case "PAUSED": return 2;
        default: return 7;
        }
    }
    readonly property real targetWanderPace: state === "ERROR" ? 2.2 : state === "THINKING" ? 0.9
        : audioState ? 0.45 + audio : state === "WAITING" ? 0.18 : 0.3
    readonly property real targetFlow: state === "EXECUTING" ? 16 : 0       // flusso ordinato verso l'alto, dentro il campo
    readonly property real breathPeriod: state === "WAITING" ? 2600 : state === "IDLE" ? 4200 : 1800

    property real shell: targetShell
    property real outerOrbit: reducedMotion ? 0 : targetOuterOrbit
    property real innerOrbit: reducedMotion ? 0 : targetInnerOrbit
    property real radial: reducedMotion ? 0 : targetRadial + outcomeRadial
    property real wander: reducedMotion ? 0 : targetWander
    property real wanderPace: targetWanderPace
    property real flow: reducedMotion ? 0 : targetFlow
    property color glowColor: stateColor
    Behavior on shell { NumberAnimation { duration: 380; easing.type: Easing.OutCubic } }
    Behavior on outerOrbit { NumberAnimation { duration: 700; easing.type: Easing.InOutQuad } }
    Behavior on innerOrbit { NumberAnimation { duration: 700; easing.type: Easing.InOutQuad } }
    Behavior on radial { NumberAnimation { duration: 260 } }
    Behavior on wander { NumberAnimation { duration: 400 } }
    Behavior on wanderPace { NumberAnimation { duration: 400 } }
    Behavior on flow { NumberAnimation { duration: 600 } }
    Behavior on glowColor { ColorAnimation { duration: 350 } }

    // ---- esito dell'ultima azione (F4.4.1) ----------------------------------------------------------
    // success: breve compressione verso il nucleo e poi espansione; warning: un impulso verso l'esterno;
    // error: esplosione di dispersione con tremolio del nucleo. Sempre con un rientro, mai uno stato bloccato.
    property real outcomeKick: 0            // >0 compatta, <0 espande (raggio del guscio)
    property real outcomeRadial: 0          // spinta radiale aggiunta alle particelle nuove
    property real glitch: 0                 // 0..1, tremolio controllato del nucleo (solo error)
    Behavior on outcomeKick { NumberAnimation { duration: 320; easing.type: Easing.OutQuad } }
    Behavior on outcomeRadial { NumberAnimation { duration: 220 } }
    Behavior on glitch { NumberAnimation { duration: 500 } }
    // dopo il giro corrente di binding: outcome e outcomeSerial cambiano insieme ma in ordine non garantito
    onOutcomeSerialChanged: Qt.callLater(react)
    function react() {
        if (outcome.length === 0 || reducedMotion) return;
        if (outcome === "success") {
            outcomeKick = 0.16; outcomeRadial = -45; glitch = 0;
        } else if (outcome === "warning") {
            outcomeKick = -0.1; outcomeRadial = 35; glitch = 0;
        } else {
            outcomeKick = -0.14; outcomeRadial = 70; glitch = 1;
        }
        kickRelease.restart();
    }
    Timer {
        id: kickRelease
        interval: 380
        // success: dopo la compressione una piccola espansione oltre il riposo, poi il rientro
        onTriggered: { root.outcomeKick = root.outcome === "success" ? -0.05 : 0; root.outcomeRadial = 0; kickReset.restart(); }
    }
    Timer { id: kickReset; interval: 420; onTriggered: { root.outcomeKick = 0; root.glitch = 0; } }

    property real angle: 0
    property real breath: 0
    property real clock: 0
    FrameAnimation {
        running: !root.reducedMotion && root.visible
        onTriggered: {
            root.clock += frameTime;
            root.angle = (root.angle + (8 + root.outerOrbit * 0.2) * frameTime) % 360;
            root.breath = Math.sin(root.clock * 2 * Math.PI * 1000 / root.breathPeriod);
        }
    }
    // l'asse delle orbite oscilla lentamente: il moto non si ripete uguale
    readonly property vector3d outerAxis: Qt.vector3d(Math.sin(clock * 0.21) * 0.35, 1, Math.cos(clock * 0.17) * 0.25)
    readonly property vector3d innerAxis: Qt.vector3d(0.45 + Math.sin(clock * 0.33) * 0.3, 1, -0.3)

    readonly property real coreScale: 0.6 * (1 + (reducedMotion ? 0 : breath * 0.04 + audio * 0.08)) * (1 - outcomeKick * 0.6)
    readonly property real shellRadius: 80 * (shell - outcomeKick)
    readonly property real coreJitter: glitch > 0 ? (Math.random() - 0.5) * 6 * glitch : 0

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
        font.pixelSize: Theme.fontLabel
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
        PointLight { position: Qt.vector3d(0, 0, 60); color: root.glowColor; brightness: 1.1 + root.audio * 0.8 }

        // nucleo volumetrico: sfera emissiva + guscio traslucido che da' profondita'
        Node {
            id: core
            eulerRotation.y: root.angle
            eulerRotation.x: 18
            position: Qt.vector3d(root.coreJitter, -root.coreJitter * 0.6, 0)
            Model {
                source: "#Sphere"
                scale: Qt.vector3d(root.coreScale, root.coreScale, root.coreScale)
                materials: PrincipledMaterial {
                    baseColor: root.glowColor
                    emissiveFactor: Qt.vector3d(root.glowColor.r * (0.45 + root.audio * 0.4), root.glowColor.g * (0.45 + root.audio * 0.4),
                                                root.glowColor.b * (0.45 + root.audio * 0.4))
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
        }

        // massa di particelle: NON figlia del nodo che ruota (niente rotazione rigida condivisa)
        ParticleSystem3D {
            id: particles
            running: root.visible
            readonly property bool high: root.quality === "high"

            SpriteParticle3D {
                id: outerSprite
                sprite: Texture { source: "assets/particle.png" }
                maxAmount: particles.high ? 1200 : 420
                color: root.glowColor
                colorVariation: Qt.vector4d(0.14, 0.14, 0.14, 0.25)
                fadeInDuration: 350
                fadeOutDuration: 600
                billboard: true
                blendMode: SpriteParticle3D.Screen
            }
            SpriteParticle3D {
                id: midSprite
                sprite: Texture { source: "assets/particle.png" }
                maxAmount: particles.high ? 520 : 0          // qualita' bassa: solo guscio e alone
                color: Qt.rgba(root.glowColor.r, root.glowColor.g, root.glowColor.b, 0.55)
                colorVariation: Qt.vector4d(0.1, 0.1, 0.1, 0.3)
                fadeInDuration: 500
                fadeOutDuration: 800
                billboard: true
                blendMode: SpriteParticle3D.Screen
            }
            SpriteParticle3D {
                id: innerSprite
                sprite: Texture { source: "assets/particle.png" }
                maxAmount: particles.high ? 420 : 160
                color: Qt.lighter(root.glowColor, 1.35)
                colorVariation: Qt.vector4d(0.08, 0.08, 0.08, 0.2)
                fadeInDuration: 250
                fadeOutDuration: 450
                billboard: true
                blendMode: SpriteParticle3D.Screen
            }

            // guscio esterno: superficie, particelle piccole e fitte
            ParticleEmitter3D {
                particle: outerSprite
                shape: ParticleShape3D { type: ParticleShape3D.Sphere; fill: false
                    extents: Qt.vector3d(root.shellRadius, root.shellRadius, root.shellRadius) }
                emitRate: (particles.high ? 440 : 150) * (1 + root.audio * 0.8) * (root.state === "THINKING" ? 1.3 : 1)
                // vita breve: il colore nasce con la particella, un cambio di stato deve vedersi in fretta
                lifeSpan: 1900
                lifeSpanVariation: 600
                particleScale: particles.high ? 1.5 : 2.1
                particleScaleVariation: 0.8
                velocity: TargetDirection3D {
                    position: Qt.vector3d(0, 0, 0)
                    normalized: true
                    magnitude: -root.radial
                    magnitudeVariation: Math.abs(root.radial) * 0.6 + 2
                }
            }
            // volume intermedio: sfera PIENA, particelle piu' grandi e tenui -> profondita', non una superficie
            ParticleEmitter3D {
                particle: midSprite
                enabled: particles.high
                shape: ParticleShape3D { type: ParticleShape3D.Sphere; fill: true
                    extents: Qt.vector3d(root.shellRadius * 0.82, root.shellRadius * 0.82, root.shellRadius * 0.82) }
                emitRate: 150 * (1 + root.audio * 0.5)
                lifeSpan: 2600
                lifeSpanVariation: 700
                particleScale: 2.6
                particleScaleVariation: 1.2
                velocity: TargetDirection3D {
                    position: Qt.vector3d(0, 0, 0)
                    normalized: true
                    magnitude: -root.radial * 0.5
                    magnitudeVariation: Math.abs(root.radial) * 0.4 + 1.5
                }
            }
            // alone interno: vicino al nucleo, luminoso, veloce nel vortice
            ParticleEmitter3D {
                particle: innerSprite
                shape: ParticleShape3D { type: ParticleShape3D.Sphere; fill: false
                    extents: Qt.vector3d(root.shellRadius * 0.52, root.shellRadius * 0.52, root.shellRadius * 0.52) }
                emitRate: (particles.high ? 170 : 70) * (1 + root.audio)
                lifeSpan: 1800
                lifeSpanVariation: 500
                particleScale: particles.high ? 1.2 : 1.8
                particleScaleVariation: 0.6
                velocity: TargetDirection3D {
                    position: Qt.vector3d(0, 0, 0)
                    normalized: true
                    magnitude: -root.radial * 0.35
                    magnitudeVariation: 2
                }
            }

            // orbite differenziali: ogni strato ruota per conto suo, con un asse che deriva nel tempo
            PointRotator3D {
                particles: [outerSprite]
                pivotPoint: Qt.vector3d(0, 0, 0)
                direction: root.outerAxis
                magnitude: root.outerOrbit
            }
            PointRotator3D {
                particles: [midSprite]
                pivotPoint: Qt.vector3d(0, 0, 0)
                direction: Qt.vector3d(-root.outerAxis.x, 1, root.innerAxis.z)
                magnitude: (root.outerOrbit + root.innerOrbit) * 0.5 + (root.state === "IDLE" ? 6 : 0)
            }
            PointRotator3D {
                particles: [innerSprite]
                pivotPoint: Qt.vector3d(0, 0, 0)
                direction: root.innerAxis
                magnitude: root.innerOrbit
            }
            // moto individuale: ampiezza e ritmo diversi per ogni particella
            Wander3D {
                particles: [outerSprite, midSprite, innerSprite]
                globalAmount: Qt.vector3d(root.wander * 0.25, root.wander * 0.25, root.wander * 0.25)
                globalPace: Qt.vector3d(0.1, 0.13, 0.08)
                uniqueAmount: Qt.vector3d(root.wander, root.wander, root.wander)
                uniquePace: Qt.vector3d(root.wanderPace, root.wanderPace * 1.3, root.wanderPace * 0.8)
                uniqueAmountVariation: 0.6
                uniquePaceVariation: 0.5
                fadeInDuration: 400
            }
            // spinta viva dal centro: segue il livello audio in tempo reale (anche sulle particelle gia' nate)
            Repeller3D {
                particles: [outerSprite, midSprite]
                radius: root.shellRadius * 0.6
                outerRadius: root.shellRadius * 1.4
                strength: root.reducedMotion ? 0 : root.audio * 90 + (root.state === "ERROR" ? 25 : 0)
            }
            // EXECUTING: flusso ordinato verso l'alto
            Gravity3D {
                particles: [outerSprite, midSprite]
                direction: Qt.vector3d(0, 1, 0)
                magnitude: root.flow
            }
        }
    }
}
