import QtQuick
import QtQuick.Controls.Basic

Slider {
    id: s
    focusPolicy: Qt.StrongFocus
    background: Rectangle {
        x: s.leftPadding
        y: s.topPadding + s.availableHeight / 2 - height / 2
        width: s.availableWidth
        height: th.s(4)
        radius: height / 2
        color: th.hairline
        Rectangle {
            width: s.visualPosition * parent.width
            height: parent.height
            radius: parent.radius
            color: th.accent
        }
    }
    handle: Rectangle {
        x: s.leftPadding + s.visualPosition * (s.availableWidth - width)
        y: s.topPadding + s.availableHeight / 2 - height / 2
        width: th.s(16)
        height: width
        radius: width / 2
        color: s.pressed ? Qt.lighter(th.accent, 1.15) : th.accent
        border.width: s.activeFocus ? 3 : 0
        border.color: th.alpha(th.fg, 0.85)
    }
}
