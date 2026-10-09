import QtQuick
import QtQuick.Window
import org.kde.layershell 1.0 as LayerShell

// Overlay layer surface on the compositor's active output. Never tiles.
Window {
    id: w
    visible: false
    color: "transparent"
    title: "Keylane"
    LayerShell.Window.scope: "keylane"
    LayerShell.Window.layer: LayerShell.Window.LayerOverlay
    LayerShell.Window.anchors: LayerShell.Window.AnchorTop | LayerShell.Window.AnchorBottom | LayerShell.Window.AnchorLeft | LayerShell.Window.AnchorRight
    LayerShell.Window.keyboardInteractivity: LayerShell.Window.KeyboardInteractivityExclusive
    LayerShell.Window.exclusionZone: -1
    LayerShell.Window.screenConfiguration: LayerShell.Window.ScreenFromCompositor

    function open(mode, text) {
        if (!visible) {
            intro.restart()
            visible = true
        }
        requestActivate()
        card.open(mode, text)
    }
    function close_() {
        card.reset()
        visible = false
    }

    Rectangle {
        anchors.fill: parent
        color: th.scrim
        MouseArea { anchors.fill: parent; onClicked: kl.hideLauncher() }
    }
    LauncherCard {
        id: card
        objectName: "card"
        anchors.horizontalCenter: parent.horizontalCenter
        y: Math.round(parent.height * 0.2)
        focus: true
        MouseArea { anchors.fill: parent; z: -1; onClicked: {} }  // swallow clicks on the card
        ParallelAnimation {
            id: intro
            NumberAnimation { target: card; property: "opacity"; from: 0; to: 1; duration: 90; easing.type: Easing.OutCubic }
            NumberAnimation { target: card; property: "scale"; from: 0.985; to: 1; duration: 110; easing.type: Easing.OutCubic }
        }
    }
}
