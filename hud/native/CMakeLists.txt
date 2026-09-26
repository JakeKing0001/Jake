cmake_minimum_required(VERSION 3.21)

# Un solo manifest guida Python, endpoint /status, eventi e build nativa.
set(JAKE_RELEASE_MANIFEST "${CMAKE_CURRENT_SOURCE_DIR}/../../config/release.json")
file(READ "${JAKE_RELEASE_MANIFEST}" JAKE_RELEASE_JSON)
string(JSON JAKE_PRODUCT_VERSION GET "${JAKE_RELEASE_JSON}" version)
string(JSON JAKE_PROTOCOL_VERSION GET "${JAKE_RELEASE_JSON}" protocol_version)

project(JakeHud VERSION ${JAKE_PRODUCT_VERSION} LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)

# Qt 6 may be installed outside the default CMake search paths on Windows. Rather than pin this
# file to whichever Qt versions happened to be installed when it was written (F0: "rilevamento Qt
# non legato a versioni installate in percorsi fissi"), look it up:
#   1. -DCMAKE_PREFIX_PATH=... or -DQt6_DIR=... passed explicitly (CI, CMakePresets.json) wins.
#   2. QTDIR / Qt6_DIR environment variables, if set (aqtinstall and the Qt online installer both
#      support setting these).
#   3. Otherwise, glob every "<root>/<version>/<kit>" install under the common Qt root
#      directories for a kit that actually ships Qt6Config.cmake, and take the newest version.
if(DEFINED ENV{QTDIR})
    list(APPEND CMAKE_PREFIX_PATH "$ENV{QTDIR}")
endif()
if(DEFINED ENV{Qt6_DIR})
    list(APPEND CMAKE_PREFIX_PATH "$ENV{Qt6_DIR}")
endif()

if(WIN32 AND NOT Qt6_DIR AND NOT DEFINED ENV{QTDIR} AND NOT DEFINED ENV{Qt6_DIR})
    # $ENV{PROGRAMFILES(X86)} non e' sintassi CMake valida: le parentesi nel nome non sono
    # ammesse dentro $ENV{...} (errore reale trovato compilando questo file: "Invalid character
    # ('(') in a variable name"). "Program Files (x86)" resta comunque coperto per un
    #'x86' install perche' CMAKE_PREFIX_PATH di default (non toccato qui) lo include gia'.
    set(_jake_qt_roots
        "C:/Qt"
        "$ENV{LOCALAPPDATA}/Qt"
        "$ENV{USERPROFILE}/Qt"
        "$ENV{PROGRAMFILES}/Qt"
    )
    set(_jake_qt_candidates "")
    foreach(_qt_root IN LISTS _jake_qt_roots)
        if(EXISTS "${_qt_root}")
            file(GLOB_RECURSE _jake_qt_configs
                LIST_DIRECTORIES false
                "${_qt_root}/*/Qt6Config.cmake"
            )
            foreach(_qt_config IN LISTS _jake_qt_configs)
                get_filename_component(_qt_cmake_dir "${_qt_config}" DIRECTORY)
                get_filename_component(_qt_prefix "${_qt_cmake_dir}" DIRECTORY)
                list(APPEND _jake_qt_candidates "${_qt_prefix}")
            endforeach()
        endif()
    endforeach()
    # Ordina per numero di versione (il nome della cartella padre, es. "6.8.2") cosi' l'ultima
    # candidata e' la piu' recente installata; find_package prova comunque tutte le candidate.
    if(_jake_qt_candidates)
        list(REMOVE_DUPLICATES _jake_qt_candidates)
        list(SORT _jake_qt_candidates COMPARE NATURAL)
        list(APPEND CMAKE_PREFIX_PATH ${_jake_qt_candidates})
    endif()
endif()

find_package(Qt6 6.5 REQUIRED COMPONENTS Core Gui Qml Quick QuickControls2 Network)
find_package(Python3 COMPONENTS Interpreter REQUIRED)

qt_standard_project_setup(REQUIRES 6.5)

# F4.1.2 ("generare o condividere lo schema tra Python e C++; niente enum mantenuti a mano"):
# JakeClient.cpp confrontava stringhe letterali scritte a mano, senza garanzia di sincronia con
# core/hud_protocol.py::EventType - questo header viene rigenerato ad ogni build DALLA fonte vera
# (tools/generate_hud_event_types.py), mai committato ne' modificato a mano (vedi .gitignore).
set(JAKE_HUD_EVENT_TYPES_HEADER "${CMAKE_CURRENT_BINARY_DIR}/generated/HudEventTypes.h")
add_custom_command(
    OUTPUT "${JAKE_HUD_EVENT_TYPES_HEADER}"
    COMMAND "${Python3_EXECUTABLE}" -m tools.generate_hud_event_types --output "${JAKE_HUD_EVENT_TYPES_HEADER}"
    WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/../.."
    DEPENDS
        "${CMAKE_CURRENT_SOURCE_DIR}/../../tools/generate_hud_event_types.py"
        "${CMAKE_CURRENT_SOURCE_DIR}/../../core/hud_protocol.py"
    COMMENT "Generating HudEventTypes.h from core/hud_protocol.py::EventType"
    VERBATIM
)
add_custom_target(JakeHudEventTypesHeader DEPENDS "${JAKE_HUD_EVENT_TYPES_HEADER}")

# F4.1 (contratto HUD): il riduttore degli eventi e' una libreria senza rete ne' QML, usata sia
# dall'HUD sia dal test di contratto che esegue la stessa suite di fixture del riferimento Python.
add_library(JakeHudProtocol STATIC src/HudEventReducer.h src/HudEventReducer.cpp)
add_dependencies(JakeHudProtocol JakeHudEventTypesHeader)
target_include_directories(JakeHudProtocol PUBLIC src "${CMAKE_CURRENT_BINARY_DIR}/generated")
target_compile_definitions(JakeHudProtocol PUBLIC JAKE_PROTOCOL_VERSION=${JAKE_PROTOCOL_VERSION})
target_link_libraries(JakeHudProtocol PUBLIC Qt6::Core)

qt_add_executable(JakeHud
    src/main.cpp
    src/JakeClient.h
    src/JakeClient.cpp
    src/OverlayStyler.h
    src/OverlayStyler.cpp
)

# Modulo QML "JakeHud" (vedi "import JakeHud" in qml/Main.qml ed engine.loadFromModule in
# src/main.cpp): registra sia i file .qml sia JakeClient (QML_ELEMENT in src/JakeClient.h).
qt_add_qml_module(JakeHud
    URI JakeHud
    VERSION 1.0
    QML_FILES
        qml/Main.qml
        qml/Orb.qml
        qml/CommandBar.qml
        qml/ConversationPanel.qml
        qml/StatusPanel.qml
        qml/QuickActions.qml
)

add_dependencies(JakeHud JakeHudEventTypesHeader)
target_include_directories(JakeHud PRIVATE src "${CMAKE_CURRENT_BINARY_DIR}/generated")

target_compile_definitions(JakeHud PRIVATE
    JAKE_PRODUCT_VERSION="${JAKE_PRODUCT_VERSION}"
)

target_link_libraries(JakeHud PRIVATE
    JakeHudProtocol
    Qt6::Core
    Qt6::Gui
    Qt6::Qml
    Qt6::Quick
    Qt6::QuickControls2
    Qt6::Network
)

if(WIN32)
    # Un exe collegato dinamicamente a Qt non e' distribuibile da solo: senza le DLL e i plugin
    # accanto al binario Windows mostra "Qt6*.dll non trovata". Ricavare windeployqt dalla
    # stessa installazione che ha fornito Qt6::qmake evita di usare per errore un'altra versione.
    get_target_property(JAKE_QMAKE_EXECUTABLE Qt6::qmake IMPORTED_LOCATION)
    get_filename_component(JAKE_QT_BIN_DIR "${JAKE_QMAKE_EXECUTABLE}" DIRECTORY)
    find_program(
        JAKE_WINDEPLOYQT_EXECUTABLE
        NAMES windeployqt
        HINTS "${JAKE_QT_BIN_DIR}"
        REQUIRED
        NO_DEFAULT_PATH
    )
    add_custom_command(
        TARGET JakeHud POST_BUILD
        COMMAND "${JAKE_WINDEPLOYQT_EXECUTABLE}"
            --release
            --no-translations
            --qmldir "${CMAKE_CURRENT_SOURCE_DIR}/qml"
            "$<TARGET_FILE:JakeHud>"
        COMMENT "Deploying the Qt runtime next to JakeHud.exe"
        VERBATIM
    )
endif()

set_target_properties(JakeHud PROPERTIES
    WIN32_EXECUTABLE ON
)

# F4.1: "client Python finto e JakeClient C++ superano la stessa suite di fixture".
enable_testing()
qt_add_executable(JakeHudContractTests tests/contract_tests.cpp)
target_link_libraries(JakeHudContractTests PRIVATE JakeHudProtocol Qt6::Core)
set_target_properties(JakeHudContractTests PROPERTIES WIN32_EXECUTABLE OFF)
add_test(NAME hud_contract
    COMMAND JakeHudContractTests "${CMAKE_CURRENT_SOURCE_DIR}/../../tests/fixtures/hud_contract.json")
# Le DLL di Qt accanto al binario le copia windeployqt (POST_BUILD di JakeHud, stessa cartella).
add_dependencies(JakeHudContractTests JakeHud)
