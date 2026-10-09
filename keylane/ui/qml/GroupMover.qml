import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Popup {
    id: p
    property string groupId: ""
    property var targets: []
    property string groupName: ""
    signal message(string kind, string text)
    modal: true
    focus: true
    anchors.centerIn: Overlay.overlay
    width: Math.min(th.s(420), Overlay.overlay ? Overlay.overlay.width - th.s(40) : th.s(420))
    padding: th.s(20)
    Overlay.modal: Rectangle { color: th.alpha(th.bgSolid, 0.6) }
    background: Rectangle { color: th.bgSolid; border.color: th.border; border.width: th.s(2); radius: th.radius }
    function openFor(gid) {
        groupId = gid
        const gs = kl.groups
        let own = null
        for (const g of gs) if (g.id === gid) own = g
        groupName = own ? own.name : ""
        const out = []
        for (const g of gs) {
            if (g.isTrash || g.inTrash || g.id === gid) continue
            if (own && (g.path + "/").startsWith(own.path + "/")) continue
            out.push({ id: g.id, text: g.depth === 0 ? "(top level)" : g.path })
        }
        targets = out
        combo.currentIndex = 0
        open(); Qt.callLater(() => combo.forceActiveFocus())
    }
    contentItem: ColumnLayout {
        spacing: th.s(12)
        KText { text: "Move “" + p.groupName + "” to…"; font.pixelSize: th.base + 4; font.bold: true; Layout.fillWidth: true; elide: Text.ElideRight }
        KCombo { id: combo; Layout.fillWidth: true; model: p.targets; textRole: "text"; onAccepted: moveBtn.clicked() }
        RowLayout {
            Item { Layout.fillWidth: true }
            KButton { text: "Cancel"; kind: "ghost"; onClicked: p.close() }
            KButton {
                id: moveBtn
                text: "Move"; kind: "primary"; hint: "↵"
                onClicked: {
                    const t = p.targets[combo.currentIndex]
                    p.close()
                    if (t) kl.moveGroup(p.groupId, t.id, function (r) { if (!r.ok) p.message("error", r.message) })
                }
            }
        }
    }
}
