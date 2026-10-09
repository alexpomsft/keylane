import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Rectangle {
    id: sb
    property string page: "entries"
    property string groupId: ""
    signal navigate(string page, string groupId)
    signal newGroup(string parentId)
    signal renameGroup(string groupId, string name)
    signal trashGroup(string groupId, string name)
    signal moveGroup(string groupId)
    signal lock()
    signal dismissed()
    function focusNav() { nav.forceActiveFocus() }
    color: th.faint

    readonly property var rootGroup: { const gs = kl.groups; for (let i = 0; i < gs.length; i++) if (gs[i].depth === 0) return gs[i]; return null }
    readonly property var items: {
        const out = [{ kind: "all", id: "", name: "All entries", depth: 0, glyph: "\uf0c9", count: kl.entryCount }]
        const gs = kl.groups
        for (let i = 0; i < gs.length; i++) {
            const g = gs[i]
            if (g.depth === 0 || g.isTrash || g.inTrash) continue
            out.push({ kind: "group", id: g.id, name: g.name, depth: g.depth, glyph: "\uf07b", count: g.count })
        }
        out.push({ kind: "conflicts", id: "", name: "Conflicts", depth: 0, glyph: "\uf071", count: kl.conflictCount })
        out.push({ kind: "trash", id: "", name: "Trash", depth: 0, glyph: "\uf1f8", count: kl.trashModel.count })
        out.push({ kind: "settings", id: "", name: "Settings", depth: 0, glyph: "\uf013", count: 0 })
        return out
    }
    function groupName(id) {
        if (!id) return "All entries"
        const gs = kl.groups
        for (let i = 0; i < gs.length; i++) if (gs[i].id === id) return gs[i].name
        return "All entries"
    }
    function isCurrent(it) {
        if (it.kind === "all") return page === "entries" && !groupId
        if (it.kind === "group") return page === "entries" && groupId === it.id
        return page === it.kind
    }
    function activate(it) { navigate(it.kind === "all" || it.kind === "group" ? "entries" : it.kind, it.id) }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0
        RowLayout {
            Layout.fillWidth: true
            Layout.margins: th.s(14)
            spacing: th.s(8)
            Glyph { g: "\uf084"; color: th.accent; font.pixelSize: th.base + 6 }
            ColumnLayout {
                spacing: 0
                Layout.fillWidth: true
                KText { text: kl.vaultName || "Keylane"; font.bold: true; Layout.fillWidth: true }
                KText {
                    Layout.fillWidth: true
                    font.pixelSize: th.base - 2
                    color: kl.storageState === "error" ? th.danger : kl.storageState === "conflict" ? th.warning : th.muted
                    text: kl.storageState === "saving" ? "Saving…" : kl.storageState === "reconciling" ? "Merging external changes…"
                        : kl.storageState === "conflict" ? kl.conflictCount + " conflict(s) — saving paused"
                        : kl.storageState === "error" ? "Save failed" : "Saved · " + kl.entryCount + " entries"
                }
            }
        }
        Separator { Layout.fillWidth: true }
        ListView {
            id: nav
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.topMargin: th.s(6)
            clip: true
            model: sb.items
            activeFocusOnTab: true
            keyNavigationEnabled: true
            highlightMoveDuration: 0
            boundsBehavior: Flickable.StopAtBounds
            Keys.onReturnPressed: if (currentIndex >= 0) sb.activate(sb.items[currentIndex])
            Keys.onPressed: (e) => {
                if (e.key === Qt.Key_Escape) { sb.dismissed(); e.accepted = true; return }
                if (e.key === Qt.Key_Home) { currentIndex = 0; e.accepted = true; return }
                if (e.key === Qt.Key_End) { currentIndex = count - 1; e.accepted = true; return }
                if (e.modifiers & Qt.ControlModifier && e.key === Qt.Key_J) { incrementCurrentIndex(); e.accepted = true; return }
                if (e.modifiers & Qt.ControlModifier && e.key === Qt.Key_K) { decrementCurrentIndex(); e.accepted = true; return }
                const it = sb.items[currentIndex]
                if (!it) return
                if (e.key === Qt.Key_Insert) { sb.newGroup(it.kind === "group" ? it.id : (sb.rootGroup ? sb.rootGroup.id : "")); e.accepted = true }
                else if ((e.key === Qt.Key_Menu || (e.key === Qt.Key_F10 && e.modifiers & Qt.ShiftModifier)) && it.kind === "group" && currentItem) {
                    menu.target = it; menu.popup(currentItem, th.s(24), currentItem.height); e.accepted = true
                }
                else if (e.key === Qt.Key_F2 && it.kind === "group") { sb.renameGroup(it.id, it.name); e.accepted = true }
                else if (e.key === Qt.Key_Delete && it.kind === "group") { sb.trashGroup(it.id, it.name); e.accepted = true }
            }
            onActiveFocusChanged: if (activeFocus) { for (let i = 0; i < sb.items.length; i++) if (sb.isCurrent(sb.items[i])) { currentIndex = i; break } }
            delegate: Item {
                id: d
                required property var modelData
                required property int index
                readonly property bool cur: sb.isCurrent(modelData)
                readonly property bool focused: nav.activeFocus && nav.currentIndex === index
                width: ListView.view.width
                height: th.s(34) + (gap ? th.s(12) : 0)
                readonly property bool gap: modelData.kind === "conflicts"
                Rectangle {
                    anchors.fill: parent
                    anchors.topMargin: d.gap ? th.s(12) : 0
                    anchors.leftMargin: th.s(6)
                    anchors.rightMargin: th.s(6)
                    radius: th.radius
                    color: d.cur ? th.sel : ma.containsMouse ? th.hover : "transparent"
                    border.width: d.focused ? 2 : 0
                    border.color: th.accent
                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: th.s(10) + Math.max(0, d.modelData.depth - 1) * th.s(14)
                        anchors.rightMargin: th.s(6)
                        spacing: th.s(8)
                        Glyph { g: d.modelData.glyph; color: d.modelData.kind === "conflicts" && d.modelData.count > 0 ? th.warning : d.cur ? th.accent : th.muted }
                        KText { Layout.fillWidth: true; text: d.modelData.name; color: d.cur ? th.selFg : th.fg }
                        IconButton {
                            visible: d.modelData.kind === "group" && (ma.containsMouse || hovered || d.focused)
                            glyph: "\uf141"; tip: "Group actions (Menu key)"
                            implicitWidth: th.s(22); implicitHeight: th.s(22)
                            onClicked: { menu.target = d.modelData; menu.popup() }
                        }
                        KText {
                            visible: d.modelData.kind !== "settings" && d.modelData.count > 0
                            text: d.modelData.count
                            color: d.modelData.kind === "conflicts" ? th.warning : th.muted
                            font.pixelSize: th.base - 1
                        }
                    }
                    MouseArea {
                        id: ma
                        anchors.fill: parent
                        hoverEnabled: true
                        acceptedButtons: Qt.LeftButton | Qt.RightButton
                        z: -1
                        onClicked: (m) => {
                            nav.currentIndex = d.index
                            if (m.button === Qt.RightButton && d.modelData.kind === "group") { menu.target = d.modelData; menu.popup() }
                            else sb.activate(d.modelData)
                        }
                    }
                }
            }
        }
        Separator { Layout.fillWidth: true }
        ColumnLayout {
            Layout.fillWidth: true
            Layout.margins: th.s(8)
            spacing: th.s(2)
            KButton { Layout.fillWidth: true; text: "New group"; glyph: "\uf07b"; kind: "ghost"; onClicked: sb.newGroup(sb.groupId || (sb.rootGroup ? sb.rootGroup.id : "")) }
            KButton { Layout.fillWidth: true; text: "Lock"; glyph: "\uf023"; hint: "Ctrl+L"; kind: "ghost"; onClicked: sb.lock() }
        }
    }

    Menu {
        id: menu
        property var target: null
        padding: th.s(4)
        background: Rectangle { implicitWidth: th.s(270); color: th.bgSolid; border.color: th.border; border.width: 1; radius: th.radius }
        delegate: MenuItem {
            id: mi
            leftPadding: th.s(10); rightPadding: th.s(10); topPadding: th.s(6); bottomPadding: th.s(6)
            contentItem: KText { text: mi.text; color: mi.highlighted ? th.selFg : th.fg; elide: Text.ElideRight }
            background: Rectangle { radius: th.radius; color: mi.highlighted ? th.sel : "transparent" }
        }
        Action { text: "New subgroup…  (Ins)"; onTriggered: sb.newGroup(menu.target.id) }
        Action { text: "Rename…  (F2)"; onTriggered: sb.renameGroup(menu.target.id, menu.target.name) }
        Action { text: "Move to…"; onTriggered: sb.moveGroup(menu.target.id) }
        Action { text: "Use as default for new entries"; onTriggered: kl.saveSettings({ defaultGroup: menu.target.id }) }
        Action { text: "Move to trash  (Del)"; onTriggered: sb.trashGroup(menu.target.id, menu.target.name) }
    }
}
