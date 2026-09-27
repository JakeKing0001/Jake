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
    // F4.3 (qualita' adattiva): se in qualita' alta i fotogrammi restano sotto ~40 fps per 3 s (GPU debole, sistema
    // carico) l'orb scende da solo alla qualita' bassa e ci resta per la sessione; mai il contrario da solo.
    property bool autoLowered: false
    readonly property string effectiveQuality: quality === "high" && !autoLowered ? "high" : "low"
    property real slowSeconds: 0
    property alias hovered: hoverHandler.hovered
    width: 220
    height: 220

    HoverHandler { id: hoverHandler }

    // Audio SOLO quando la frase e' rivolta a Jake (ascolto del comando, dettatura) o quando Jake parla: in IDLE il
    // microfono puo' essere aperto per la parola di attivazione, ma TV, musica e voci intorno non muovono l'orb.
    readonly property bool audioState: state === "LISTENING" || state === "SPEAKING" || state === "DICTATION"
    readonly property real rawAudio: audioState ? Math.max(0, Math.min(1, level)) : 0
    // inviluppo del livello (prova reale del 27/09/2026: reazione quasi invisibile e "a scatti"): attacco rapido
    // (~60 ms) perche' la voce si veda subito, rilascio morbido (~450 ms) perche' il silenzio riporti l'orb alla sua
    // base senza cadute secche; aggiornato a ogni fotogramma, non a ogni pacchetto (~10 al secondo).
    property real audio: 0

    // ---- parametri per stato: ogni stato si riconosce dal MOVIMENTO, non solo dal colore -------------------
    readonly property color stateColor: {
        switch (state) {
        case "LISTENING": return "#4fd1ff";
        case "TRANSCRIBING": return "#7dd3fc";
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
    // raggio del guscio (1 = riposo): l'ascolto si apre, la trascrizione raccoglie, il pensiero stringe
    readonly property real targetShell: {
        switch (state) {
        case "LISTENING": return 1.12 + audio * 0.3;
        case "DICTATION": return 1.08 + audio * 0.35;
        case "SPEAKING": return 1.02 + audio * 0.28;
        case "TRANSCRIBING": return 0.8;
        case "THINKING": return 0.86;
        case "EXECUTING": return 0.98;
        case "WAITING": return 0.94;
        case "ERROR": return 1.2;
        case "PAUSED": return 0.85;
        default: return 1.0;
        }
    }
    // orbita dello strato esterno e interno (gradi al secondo): segni opposti = contro-rotazione organica,
    // stesso segno con l'interno piu' veloce = vortice
    readonly property real targetOuterOrbit: {
        switch (state) {
        case "THINKING": return 150;
        case "TRANSCRIBING": return 70;
        case "EXECUTING": return 75;
        case "LISTENING": case "DICTATION": return 40 + audio * 90;
        case "SPEAKING": return 32 + audio * 70;
        case "WAITING": return 9;
        case "PAUSED": return 4;
        case "ERROR": return 6;
        default: return 30;
        }
    }
    readonly property real targetInnerOrbit: {
        switch (state) {
        case "THINKING": return 300;
        case "TRANSCRIBING": return 110;
        case "EXECUTING": return 75;
        case "LISTENING": case "DICTATION": return -32 - audio * 70;
        case "SPEAKING": return -24 - audio * 50;
        case "WAITING": return -6;
        case "PAUSED": return -3;
        case "ERROR": return -10;
        default: return -22;
        }
    }
    // velocita' radiale alla nascita (unita'/s): >0 verso l'esterno, <0 verso il nucleo
    readonly property real targetRadial: {
        switch (state) {
        case "TRANSCRIBING": return -38;
        case "THINKING": return -18;
        case "EXECUTING": return 0;
        case "LISTENING": case "DICTATION": return 10 + audio * 42;
        case "SPEAKING": return 6 + audio * 38;
        case "WAITING": return 2;
        case "ERROR": return 45;
        default: return 8;
        }
    }
    // moto individuale: ampiezza (unita') e ritmo (cicli al secondo) unici per ogni particella
    readonly property real targetWander: {
        switch (state) {
        case "THINKING": return 14;
        case "TRANSCRIBING": return 6;
        case "EXECUTING": return 4;
        case "LISTENING": case "DICTATION": return 16 + audio * 40;
        case "SPEAKING": return 12 + audio * 45;
        case "WAITING": return 4;
        case "ERROR": return 45;
        case "PAUSED": return 3;
        default: return 14;
        }
    }
    readonly property real targetWanderPace: state === "ERROR" ? 3.0 : state === "THINKING" ? 1.2
        : audioState ? 0.6 + audio * 1.6 : state === "WAITING" ? 0.12 : state === "TRANSCRIBING" ? 0.5 : 0.35
    readonly property real targetFlow: state === "EXECUTING" ? 24 : 0       // flusso ordinato verso l'alto
    readonly property real breathPeriod: state === "WAITING" ? 3400 : state === "IDLE" ? 4200 : 1800

    property real shell: targetShell
    property real outerOrbit: reducedMotion ? 0 : targetOuterOrbit
    property real innerOrbit: reducedMotion ? 0 : targetInnerOrbit
    property real radial: reducedMotion ? 0 : targetRadial + outcomeRadial
    property real wander: reducedMotion ? 0 : targetWander
    property real wanderPace: targetWanderPace
    property real flow: reducedMotion ? 0 : targetFlow
    property color glowColor: stateColor
    // le grandezze guidate dall'audio seguono gia' l'inviluppo: animarle ancora le renderebbe molli
    Behavior on shell { enabled: !root.audioState; NumberAnimation { duration: 380; easing.type: Easing.OutCubic } }
    Behavior on outerOrbit { enabled: !root.audioState; NumberAnimation { duration: 700; easing.type: Easing.InOutQuad } }
    Behavior on innerOrbit { enabled: !root.audioState; NumberAnimation { duration: 700; easing.type: Easing.InOutQuad } }
    Behavior on radial { enabled: !root.audioState; NumberAnimation { duration: 260 } }
    Behavior on wander { enabled: !root.audioState; NumberAnimation { duration: 400 } }
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
            outcomeKick = 0.2; outcomeRadial = -70; glitch = 0;
        } else if (outcome === "warning") {
            outcomeKick = -0.14; outcomeRadial = 60; glitch = 0;
        } else {
            outcomeKick = -0.18; outcomeRadial = 110; glitch = 1;
        }
        kickRelease.restart();
    }
    Timer {
        id: kickRelease
        interval: 380
        // success: dopo la compressione una piccola espansione oltre il riposo, poi il rientro
        onTriggered: { root.outcomeKick = root.outcome === "success" ? -0.08 : 0; root.outcomeRadial = 0; kickReset.restart(); }
    }
    Timer { id: kickReset; interval: 420; onTriggered: { root.outcomeKick = 0; root.glitch = 0; } }

    property real angle: 0
    property real breath: 0
    property real clock: 0
    FrameAnimation {
        running: !root.reducedMotion && root.visible
        onTriggered: {
            root.clock += frameTime;
            if (root.effectiveQuality === "high" && frameTime > 0 && frameTime < 1) {
                root.slowSeconds = frameTime > 1 / 40 ? root.slowSeconds + frameTime : Math.max(0, root.slowSeconds - frameTime);
                if (root.slowSeconds > 3) {
                    root.autoLowered = true;
                    console.warn("Orb 3D: fotogrammi lenti per 3 s, passo alla qualita' bassa");
                }
            }
            const target = root.rawAudio;
            const tau = target > root.audio ? 0.06 : 0.45;
            root.audio += (target - root.audio) * (1 - Math.exp(-Math.min(frameTime, 0.1) / tau));
            root.angle = (root.angle + (8 + root.outerOrbit * 0.2) * frameTime) % 360;
            root.breath = Math.sin(root.clock * 2 * Math.PI * 1000 / root.breathPeriod);
        }
    }
    // l'asse delle orbite oscilla lentamente: il moto non si ripete uguale
    readonly property vector3d outerAxis: Qt.vector3d(Math.sin(clock * 0.21) * 0.45, 1, Math.cos(clock * 0.17) * 0.35)
    readonly property vector3d innerAxis: Qt.vector3d(0.5 + Math.sin(clock * 0.33) * 0.35, 1, -0.35)

    // nucleo piu' piccolo e piu' luminoso: la presenza e' la massa di particelle, non una sfera solida
    readonly property real coreScale: 0.42 * (1 + (reducedMotion ? 0 : breath * 0.05 + audio * 0.25)) * (1 - outcomeKick * 0.6)
    readonly property real shellRadius: 82 * (shell - outcomeKick)
    readonly property real coreJitter: glitch > 0 ? (Math.random() - 0.5) * 8 * glitch : 0

    property string status: ""       // frase che accompagna lo stato (es. "Sto aspettando il modello locale...")
    readonly property string stateLabel: {
        if (status.length > 0) return status;
        switch (state) {
        case "LISTENING": return qsTr("In ascolto");
        case "TRANSCRIBING": return qsTr("Sto capendo");
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
            antialiasingMode: root.effectiveQuality === "high" ? SceneEnvironment.MSAA : SceneEnvironment.NoAA
            antialiasingQuality: SceneEnvironment.High
        }

        PerspectiveCamera { position: Qt.vector3d(0, 0, 320); clipNear: 1; clipFar: 1000 }
        DirectionalLight { eulerRotation.x: -30; eulerRotation.y: -40; brightness: 1.2 }
        PointLight { position: Qt.vector3d(0, 0, 60); color: root.glowColor; brightness: 1.1 + root.audio * 1.8 }

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
                    emissiveFactor: Qt.vector3d(root.glowColor.r * (0.55 + root.audio * 0.9), root.glowColor.g * (0.55 + root.audio * 0.9),
                                                root.glowColor.b * (0.55 + root.audio * 0.9))
                    roughness: 0.35
                    metalness: 0.0
                }
            }
            Model {
                source: "#Sphere"
                scale: Qt.vector3d(root.coreScale * 1.35, root.coreScale * 1.35, root.coreScale * 1.35)
                materials: PrincipledMaterial {
                    baseColor: Qt.rgba(root.glowColor.r, root.glowColor.g, root.glowColor.b, 0.1)
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
            readonly property bool high: root.effectiveQuality === "high"

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
                emitRate: (particles.high ? 460 : 160) * (1 + root.audio * 1.8) * (root.state === "THINKING" ? 1.3 : 1)
                // abbastanza lunga da vedere la traiettoria, abbastanza breve da cambiare colore col nuovo stato
                lifeSpan: 2300
                lifeSpanVariation: 700
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
                emitRate: 170 * (1 + root.audio * 1.2)
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
                emitRate: (particles.high ? 180 : 80) * (1 + root.audio * 2)
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
                strength: root.reducedMotion ? 0 : root.audio * 150 + (root.state === "ERROR" ? 50 : 0)
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
