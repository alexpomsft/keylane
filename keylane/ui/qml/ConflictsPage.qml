import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Resolve sync conflicts: side-by-side, secrets masked, per-field "combine" picks.
Item {
    id: cp
    property var confirmDialog: null
    property string cid: ""
    property var detail: null
    property bool reveal: false
    property var picks: ({})      // field key -> "local" | "remote"
    property string error: ""
    signal message(string kind, string text)
    signal openEntry(string id)

    function focusList() {
        if (cl.count && cl.currentIndex < 0) { cl.currentIndex = 0; load(kl.conflicts[0].id) }
        cl.forceActiveFocus()
    }
    function selectEntry(entryId) {
        const cs = kl.conflicts
        for (let i = 0; i < cs.length; i++) if (cs[i].entryId === entryId) { cl.currentIndex = i; load(cs[i].id); return }
    }
    function load(id) {
        cid = id; detail = null; reveal = false; picks = ({}); error = ""
        if (!id) return
        kl.conflictDetail(id, function (r) {
            if (cp.cid !== id) return
            if (r.ok) cp.detail = r; else cp.error = r.message
        })
    }
    onVisibleChanged: if (visible && !cid && kl.conflicts.length) load(kl.conflicts[0].id)
    Connections {
        target: kl
        function onIndexChanged() {
            if (!cp.cid) return
            if (!kl.conflicts.some(c => c.id === cp.cid)) cp.load(kl.conflicts.length ? kl.conflicts[0].id : "")
        }
    }
    readonly property bool isEntry: detail && detail.kind === "entry"
    readonly property bool bothExist: detail && detail.local && detail.remote
    readonly property var rows: {
        if (!detail || !isEntry) return []
        const L = detail.local || {}, R = detail.remote || {}
        const base = [["title", "Title"], ["username", "Username"], ["password", "Password"], ["url", "URL"], ["tags", "Tags"], ["notes", "Notes"], ["groupId", "Group"]]
        const out = base.map(([k, label]) => ({ key: k, label: label, l: String(L[k] ?? ""), r: String(R[k] ?? ""), secret: k === "password" }))
        const keys = new Set()
        for (const f of (L.fields || [])) keys.add(f.key)
        for (const f of (R.fields || [])) keys.add(f.key)
        for (const k of keys) {
            const lf = (L.fields || []).find(f => f.key === k), rf = (R.fields || []).find(f => f.key === k)
            out.push({ key: "field:" + k, label: k, l: lf ? lf.value : "", r: rf ? rf.value : "", secret: (lf && lf.protected) || (rf && rf.protected), lMissing: !lf, rMissing: !rf })
        }
        return out
    }
    function groupPath(id) { for (const g of kl.groups) if (g.id === id) return g.depth === 0 ? "(top level)" : g.path; return id ? "(unknown group)" : "" }
    function shown(row, v) { if (row.key === "groupId") return groupPath(v); if (row.secret && !reveal) return v ? "••••••••••••" : ""; return v }
    function resolve(choice, custom) {
        kl.resolveConflict(cid, choice, custom || null, function (r) {
            if (!r.ok) { cp.error = r.message; return }
            cp.message("success", kl.conflictCount === 0 ? "All conflicts resolved. Saving resumed." : "Resolved. " + kl.conflictCount + " left.")
        })
    }
    function combined() {
        const L = detail.local, R = detail.remote
        const out = Object.assign({}, L)
        const fields = []
        for (const row of rows) {
            const side = picks[row.key] || "local"
            const src = side === "local" ? L : R
            if (row.key.startsWith("field:")) {
                const k = row.key.slice(6)
                const f = (src.fields || []).find(x => x.key === k)
                if (f) fields.push({ key: f.key, value: f.value, protected: f.protected })
            } else out[row.key] = src[row.key]
        }
        out.fields = fields
        return out
    }
    function pick(key, side) { const p = Object.assign({}, picks); p[key] = side; picks = p }

    RowLayout {
        anchors.fill: parent
        spacing: 0
        ColumnLayout {
            Layout.preferredWidth: cp.width < th.s(900) ? th.s(210) : th.s(300)
            Layout.maximumWidth: Layout.preferredWidth
            Layout.fillWidth: false
            Layout.fillHeight: true
            spacing: 0
            KText { Layout.margins: th.s(14); text: "Conflicts"; font.pixelSize: th.base + 4; font.bold: true }
            KText {
                Layout.leftMargin: th.s(14); Layout.rightMargin: th.s(14); Layout.bottomMargin: th.s(10); Layout.fillWidth: true
                wrapMode: Text.Wrap; color: th.muted; font.pixelSize: th.base - 1
                text: kl.conflictCount ? "Another device changed the same items. Saving is paused until every conflict is resolved; nothing has been lost." : ""
            }
            ListView {
                id: cl
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                model: kl.conflicts
                activeFocusOnTab: true
                keyNavigationEnabled: true
                onCurrentIndexChanged: if (activeFocus && currentIndex >= 0) cp.load(kl.conflicts[currentIndex].id)
                delegate: Rectangle {
                    required property var modelData
                    required property int index
                    readonly property bool cur: modelData.id === cp.cid
                    width: ListView.view.width
                    height: th.s(50)
                    color: cur ? th.sel : cma.containsMouse ? th.hover : "transparent"
                    Rectangle { visible: parent.cur; width: th.s(3); height: parent.height; color: th.accent }
                    ColumnLayout {
                        anchors.fill: parent; anchors.leftMargin: th.s(14); anchors.rightMargin: th.s(10); spacing: 1
                        KText { Layout.fillWidth: true; elide: Text.ElideRight; text: modelData.title || "(untitled)"; color: parent.parent.cur ? th.selFg : th.fg }
                        KText { Layout.fillWidth: true; text: modelData.reasonText; color: th.muted; font.pixelSize: th.base - 1; elide: Text.ElideRight }
                    }
                    MouseArea { id: cma; anchors.fill: parent; hoverEnabled: true; onClicked: { cl.currentIndex = index; cp.load(modelData.id) } }
                }
            }
        }
        Separator { Layout.fillHeight: true; implicitWidth: 1 }
        ColumnLayout {
            Layout.fillWidth: true
            Layout.preferredWidth: th.s(400)
            Layout.minimumWidth: 0
            Layout.fillHeight: true
            Layout.margins: th.s(18)
            spacing: th.s(12)
            KText {
                visible: !cp.detail && !cp.error
                Layout.fillWidth: true
                Layout.fillHeight: kl.conflictCount === 0
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
                wrapMode: Text.Wrap
                text: kl.conflictCount ? "Select a conflict." : "\uf00c  No conflicts. Everything is in sync."
                color: th.muted
            }
            Banner { Layout.fillWidth: true; text: cp.error }
            ColumnLayout {
                visible: !!cp.detail
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: th.s(12)
                KText { text: cp.detail ? (cp.detail.title || "(untitled)") : ""; font.pixelSize: th.base + 6; font.bold: true; Layout.fillWidth: true }
                KText { text: cp.detail ? cp.detail.reasonText : ""; color: th.warning; Layout.fillWidth: true; wrapMode: Text.Wrap }

                // Non-entry conflicts (groups, settings, delete-vs-edit)
                GridLayout {
                    visible: !!cp.detail && (!cp.isEntry || !cp.bothExist)
                    columns: 2
                    columnSpacing: th.s(18)
                    Layout.fillWidth: true
                    SectionLabel { text: "This device" }
                    SectionLabel { text: "Other device" }
                    KText { Layout.fillWidth: true; wrapMode: Text.Wrap; text: cp.describe(cp.detail ? cp.detail.local : null) }
                    KText { Layout.fillWidth: true; wrapMode: Text.Wrap; text: cp.describe(cp.detail ? cp.detail.remote : null) }
                }

                ScrollView {
                    visible: cp.isEntry && cp.bothExist
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    contentWidth: availableWidth
                    clip: true
                    GridLayout {
                        width: parent.width
                        columns: 3
                        columnSpacing: th.s(12)
                        rowSpacing: th.s(4)
                        KText { text: ""; Layout.preferredWidth: th.s(110) }
                        SectionLabel { text: "This device"; Layout.fillWidth: true; Layout.preferredWidth: 1 }
                        SectionLabel { text: "Other device"; Layout.fillWidth: true; Layout.preferredWidth: 1 }
                        Repeater {
                            model: cp.rows.length * 3
                            delegate: Loader {
                                required property int index
                                readonly property var row: cp.rows[Math.floor(index / 3)]
                                readonly property int col: index % 3
                                Layout.fillWidth: col > 0
                                Layout.preferredWidth: col === 0 ? th.s(110) : 1
                                sourceComponent: col === 0 ? labelC : cellC
                                property bool differs: row && row.l !== row.r
                            }
                        }
                    }
                }
                Component {
                    id: labelC
                    KText { text: parent.row.label; color: parent.differs ? th.warning : th.muted; font.bold: parent.differs }
                }
                Component {
                    id: cellC
                    Rectangle {
                        readonly property var row: parent.row
                        readonly property string side: parent.col === 1 ? "local" : "remote"
                        readonly property bool chosen: parent.differs && (cp.picks[row.key] || "local") === side
                        activeFocusOnTab: parent.differs
                        Accessible.role: Accessible.RadioButton
                        Accessible.name: row.label + ", " + (side === "local" ? "this device" : "other device")
                        Accessible.checked: chosen
                        Keys.onSpacePressed: cp.pick(row.key, side)
                        Keys.onReturnPressed: cp.pick(row.key, side)
                        Keys.onLeftPressed: cp.pick(row.key, "local")
                        Keys.onRightPressed: cp.pick(row.key, "remote")
                        implicitHeight: ct.implicitHeight + th.s(10)
                        radius: th.radius
                        color: chosen ? th.alpha(th.accent, 0.12) : parent.differs ? th.faint : "transparent"
                        border.width: activeFocus ? 2 : chosen ? 1 : 0
                        border.color: activeFocus ? th.fg : th.accent
                        KText {
                            id: ct
                            anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                            anchors.margins: th.s(6)
                            text: { const v = side === "local" ? row.l : row.r; const missing = side === "local" ? row.lMissing : row.rMissing; return missing ? "(absent)" : (cp.shown(row, v) || "—") }
                            wrapMode: Text.Wrap
                            maximumLineCount: row.key === "notes" ? 6 : 2
                            color: parent.chosen ? th.fg : parent.parent.differs ? th.fg : th.muted
                        }
                        MouseArea { anchors.fill: parent; enabled: parent.parent.differs; cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor; onClicked: cp.pick(row.key, side) }
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    spacing: th.s(8)
                    KButton { visible: cp.isEntry; text: cp.reveal ? "Hide secrets" : "Reveal secrets"; kind: "ghost"; onClicked: cp.reveal = !cp.reveal }
                    KText { visible: cp.isEntry && cp.bothExist; text: "Pick highlighted values (click, or Tab + Space) to combine."; Layout.fillWidth: true; Layout.preferredWidth: 1; elide: Text.ElideRight; color: th.muted; font.pixelSize: th.base - 1 }
                }
                Flow {
                    Layout.fillWidth: true
                    spacing: th.s(8)
                    KButton { text: cp.detail && !cp.detail.local ? "Keep deleted" : "Keep this device's"; onClicked: cp.resolve("local") }
                    KButton { text: cp.detail && !cp.detail.remote ? "Accept deletion" : "Keep other device's"; onClicked: cp.resolve("remote") }
                    KButton { visible: cp.isEntry && cp.bothExist; text: "Keep both"; onClicked: cp.resolve("both") }
                    KButton { visible: cp.isEntry && cp.bothExist; text: "Save combined"; kind: "primary"; enabled: Object.keys(cp.picks).length > 0; onClicked: cp.resolve("custom", cp.combined()) }
                }
            }
        }
    }
    function describe(x) {
        if (x === null || x === undefined) return "Deleted"
        if (x.title !== undefined) return [x.title, x.username, x.url].filter(v => v).join("\n")
        if (x.name !== undefined) return "Group “" + x.name + "”" + (x.notes ? "\n" + x.notes : "")
        return x.text || ""
    }
}
