pragma Singleton
import QtQuick

// Un solo linguaggio visivo per tutto l'HUD: stesso vetro, stesso gradiente, stessi raggi e colori in
// ogni pannello (mai un aspetto diverso per elemento). Il vetro vero con blur di sistema e' F4.3: qui il
// "vetro" e' un riempimento scuro semitrasparente con gradiente e bordo luminoso, leggibile anche sopra
// un desktop chiaro o pieno di testo.
QtObject {
    // F4.7.3: impostato da Main.qml (src/main.cpp legge "Contrasto elevato" di Windows). Stesso materiale e stessa
    // forma di ogni pannello; cambiano solo i colori: nero pieno, testo bianco, bordi e focus ben visibili.
    property bool highContrast: false
    // i pannelli di vetro presenti (GlassPanel si registra da solo): servono al click-through per pannello
    property var panels: []
    function registerPanel(panel) { panels = panels.concat([panel]); }
    function unregisterPanel(panel) { panels = panels.filter(p => p !== panel); }
    // F4.7.4: "Dimensioni testo" di Windows (Accessibilita', 100-225%), impostato da Main.qml; Qt non lo applica da
    // solo al QML. Tutte le dimensioni del testo passano da qui.
    property real textScale: 1.0
    function font(size) { return Math.round(size * textScale) }

    // Vetro (seconda prova reale del 27/09/2026): tinta scura ma non piena - sotto c'e' la rifrazione della luce
    // dell'HUD (GlassPanel). Quasi opaca: il testo delle finestre sotto non deve trapelare (screenshot reale del
    // 26/09/2026, pannelli illeggibili con un vetro troppo trasparente).
    readonly property color glassTop: highContrast ? "#000000" : Qt.rgba(0.11, 0.12, 0.16, 0.93)
    readonly property color glassBottom: highContrast ? "#000000" : Qt.rgba(0.06, 0.07, 0.09, 0.95)
    readonly property color glassBorder: highContrast ? "#ffffff" : Qt.rgba(1, 1, 1, 0.09)
    readonly property color glassRim: Qt.rgba(1, 1, 1, 0.34)
    readonly property color glassHighlight: highContrast ? "transparent" : Qt.rgba(1, 1, 1, 0.07)
    readonly property real radius: 22
    readonly property real smallRadius: 12
    // impostati da Main.qml: il colore dello stato di Jake (tinta dei pannelli) e la luce sfocata dietro di loro
    property color stateColor: "#3ddc84"
    property Item backdrop: null
    // rifrazione e ombre: solo in qualita' alta (batteria, GPU debole, JAKE_HUD_QUALITY=low -> no)
    property bool richGlass: true

    readonly property color text: highContrast ? "#ffffff" : "#f3f4f6"
    readonly property color textMuted: highContrast ? "#ffffff" : "#a1a7b3"
    readonly property color textFaint: highContrast ? "#e5e7eb" : "#6b7280"
    readonly property color accent: highContrast ? "#00ffff" : "#4fd1ff"
    readonly property color ok: "#3ddc84"
    readonly property color warn: "#facc15"
    readonly property color attention: "#fb923c"
    readonly property color danger: "#f87171"
    readonly property color focusRing: highContrast ? "#ffff00" : "#93c5fd"

    readonly property color control: highContrast ? "#000000" : "#262b36"
    readonly property color controlHover: highContrast ? "#1f1f1f" : "#323846"
    readonly property int fontTiny: font(11)
    readonly property int fontSmall: font(12)
    readonly property int fontLabel: font(13)
    readonly property int fontBody: font(14)
}
