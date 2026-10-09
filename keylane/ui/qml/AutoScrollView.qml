import QtQuick
import QtQuick.Controls.Basic

// ScrollView that keeps the keyboard-focused control in view and pages with PgUp/PgDn.
ScrollView {
    id: sv
    clip: true
    contentWidth: availableWidth
    focusPolicy: Qt.ClickFocus

    readonly property Flickable flick: contentItem

    function reveal(item) {
        if (!item || !flick || !flick.contentItem) return
        let p = item
        while (p && p !== flick.contentItem) p = p.parent
        if (!p) return
        const y = item.mapToItem(flick.contentItem, 0, 0).y
        const m = th.s(24)
        const maxY = Math.max(0, flick.contentHeight - flick.height)
        if (y - m < flick.contentY) flick.contentY = Math.max(0, y - m)
        else if (y + item.height + m > flick.contentY + flick.height)
            flick.contentY = Math.min(maxY, y + item.height + m - flick.height)
    }
    function page(dir) {
        const maxY = Math.max(0, flick.contentHeight - flick.height)
        flick.contentY = Math.max(0, Math.min(maxY, flick.contentY + dir * flick.height * 0.85))
    }

    Connections {
        target: sv.Window.window
        function onActiveFocusItemChanged() { if (sv.visible) Qt.callLater(sv.reveal, sv.Window.window.activeFocusItem) }
    }
    Keys.onPressed: (e) => {
        if (e.key === Qt.Key_PageDown) { page(1); e.accepted = true }
        else if (e.key === Qt.Key_PageUp) { page(-1); e.accepted = true }
        else if (activeFocus && e.key === Qt.Key_Down) { flick.contentY = Math.min(Math.max(0, flick.contentHeight - flick.height), flick.contentY + th.s(40)); e.accepted = true }
        else if (activeFocus && e.key === Qt.Key_Up) { flick.contentY = Math.max(0, flick.contentY - th.s(40)); e.accepted = true }
    }
}
