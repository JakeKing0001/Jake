cmake_minimum_required(VERSION 3.21)
project(JakeHud LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)

find_package(Qt6 6.5 REQUIRED COMPONENTS Core Gui Qml Quick QuickControls2 Network)

qt_standard_project_setup(REQUIRES 6.5)

qt_add_executable(JakeHud
    src/main.cpp
    src/JakeClient.h
    src/JakeClient.cpp
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

target_include_directories(JakeHud PRIVATE src)

target_link_libraries(JakeHud PRIVATE
    Qt6::Core
    Qt6::Gui
    Qt6::Qml
    Qt6::Quick
    Qt6::QuickControls2
    Qt6::Network
)

set_target_properties(JakeHud PROPERTIES
    WIN32_EXECUTABLE ON
)
