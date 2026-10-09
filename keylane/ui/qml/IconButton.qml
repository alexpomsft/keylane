import QtQuick
import QtQuick.Controls.Basic

// Square glyph button with tooltip; used for copy/reveal/regenerate.
ToolButton {
    id: b
    property string glyph: ""
    property string tip: ""
    property color tint: th.muted
    focusPolicy: Qt.TabFocus
    hoverEnabled: true
    implicitWidth: th.s(28)
    implicitHeight: th.s(28)
    Accessible.name: tip
    ToolTip.visible: hovered && tip !== ""
    ToolTip.text: tip
    ToolTip.delay: 400
    Keys.onReturnPressed: clicked()
    Keys.onSpacePressed: clicked()
    contentItem: Glyph { g: b.glyph; color: b.hovered || b.activeFocus || b.checked ? th.fg : b.tint }
    background: Rectangle {
        radius: th.radius
        color: b.down ? th.alpha(th.fg, 0.16) : (b.hovered || b.checked) ? th.hover : "transparent"
        border.width: b.activeFocus ? 2 : 0
        border.color: th.accent
    }
}
