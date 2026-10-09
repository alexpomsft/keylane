import QtQuick
import QtQuick.Controls.Basic

ComboBox {
    id: c
    font.family: th.fontFamily
    font.pixelSize: th.base + 1
    focusPolicy: Qt.StrongFocus
    signal accepted()
    Keys.onReturnPressed: (e) => { if (popup.visible) e.accepted = false; else accepted() }
    Keys.onEnterPressed: (e) => { if (popup.visible) e.accepted = false; else accepted() }
    implicitHeight: th.s(32)
    contentItem: KText {
        leftPadding: th.s(10)
        rightPadding: c.indicator.width
        text: c.displayText
        verticalAlignment: Text.AlignVCenter
    }
    indicator: Glyph {
        x: c.width - width - th.s(8)
        y: (c.height - height) / 2
        g: "\uf107"
    }
    background: Rectangle {
        radius: th.radius
        color: th.faint
        border.width: c.activeFocus ? 2 : 1
        border.color: c.activeFocus ? th.accent : th.hairline
    }
    delegate: ItemDelegate {
        required property var modelData
        required property int index
        width: c.width
        highlighted: c.highlightedIndex === index
        contentItem: KText {
            text: c.textRole && modelData !== null && typeof modelData === "object" ? String(modelData[c.textRole] ?? "") : String(modelData ?? "")
            color: highlighted ? th.selFg : th.fg
        }
        background: Rectangle { color: highlighted ? th.sel : "transparent" }
    }
    popup: Popup {
        y: c.height + 2
        width: c.width
        implicitHeight: Math.min(contentItem.implicitHeight + 2, th.s(320))
        padding: 1
        contentItem: ListView {
            clip: true
            implicitHeight: contentHeight
            model: c.popup.visible ? c.delegateModel : null
            currentIndex: c.highlightedIndex
            ScrollIndicator.vertical: ScrollIndicator {}
        }
        background: Rectangle {
            color: th.bgSolid
            border.color: th.border
            border.width: 1
            radius: th.radius
        }
    }
}
