import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Popup {
    id: p
    property string heading: ""
    property var callback: null
    modal: true
    focus: true
    anchors.centerIn: Overlay.overlay
    width: Math.min(th.s(420), Overlay.overlay ? Overlay.overlay.width - th.s(40) : th.s(420))
    padding: th.s(20)
    Overlay.modal: Rectangle { color: th.alpha(th.bgSolid, 0.6) }
    background: Rectangle { color: th.bgSolid; border.color: th.border; border.width: th.s(2); radius: th.radius }
    function ask(h, label, value, cb) {
        heading = h; field.label = label; field.text = value; callback = cb
        open(); Qt.callLater(field.focusField)
    }
    function accept() { if (!field.text.trim()) return; const cb = callback; callback = null; const v = field.text; close(); if (cb) cb(v) }
    onClosed: { callback = null; field.text = "" }
    contentItem: ColumnLayout {
        spacing: th.s(12)
        KText { text: p.heading; font.pixelSize: th.base + 4; font.bold: true }
        KField { id: field; Layout.fillWidth: true; onAccepted: p.accept(); input.Keys.onEscapePressed: p.close() }
        RowLayout {
            Item { Layout.fillWidth: true }
            KButton { text: "Cancel"; kind: "ghost"; onClicked: p.close() }
            KButton { text: "OK"; kind: "primary"; hint: "↵"; enabled: field.text.trim() !== ""; onClicked: p.accept() }
        }
    }
}
