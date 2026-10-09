import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Full entry editor. Secrets are masked until revealed; reveal and copy are separate.
FocusScope {
    id: ed
    property string entryId: ""
    property bool isNew: false
    property bool loading: false
    property string originalJson: ""
    property real modified: 0
    property int historyCount: 0
    property var attachments: []
    property int rev: 0              // bumped on custom-field edits so `dirty` re-evaluates
    ListModel { id: fieldModel }      // {key, value, prot, revealed}
    property string error: ""
    property string info: ""
    property bool saving: false
    property bool showGen: false
    property bool conflicted: entryId !== "" && kl.conflicts.some(c => c.entryId === entryId)
    readonly property bool active: entryId !== "" || isNew
    readonly property bool dirty: active && !loading && rev >= 0 && JSON.stringify(current()) !== originalJson
    property var groupItems: []
    signal saved(string id)
    signal requestResolve(string entryId)

    function rebuildGroups() {
        const items = []
        const gs = kl.groups
        for (let i = 0; i < gs.length; i++) {
            if (gs[i].isTrash || gs[i].inTrash) continue
            items.push({ id: gs[i].id, text: gs[i].depth === 0 ? "(top level)" : gs[i].path })
        }
        groupItems = items
    }
    function groupIndex(id) {
        for (let i = 0; i < groupItems.length; i++) if (groupItems[i].id === id) return i
        return 0
    }
    function current() {
        return {
            title: fTitle.text, username: fUser.text, password: fPass.text, url: fUrl.text, notes: fNotes.text,
            tags: fTags.text, groupId: groupItems[fGroup.currentIndex] ? groupItems[fGroup.currentIndex].id : "",
            expires: fExpires.checked, expiry: fExpires.checked ? parseDate(fExpiry.text) : 0,
            fields: fieldList()
        }
    }
    function fieldList() {
        const out = []
        for (let i = 0; i < fieldModel.count; i++) {
            const f = fieldModel.get(i)
            out.push({ key: f.key, value: f.value, protected: f.prot })
        }
        return out
    }
    function titleText() { return fTitle.text }
    function setField(i, k, v) { fieldModel.setProperty(i, k, v); rev++ }
    function parseDate(s) {
        const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s.trim())
        if (!m) return 0
        return Date.UTC(+m[1], +m[2] - 1, +m[3], 12)
    }
    function fmtDate(ms) {
        if (!ms) return ""
        const d = new Date(ms)
        return d.getUTCFullYear() + "-" + String(d.getUTCMonth() + 1).padStart(2, "0") + "-" + String(d.getUTCDate()).padStart(2, "0")
    }
    function apply(e) {
        rebuildGroups()
        fTitle.text = e.title || ""; fUser.text = e.username || ""; fPass.text = e.password || ""
        fUrl.text = e.url || ""; fNotes.text = e.notes || ""; fTags.text = e.tags || ""
        fGroup.currentIndex = groupIndex(e.groupId || "")
        fExpires.checked = !!e.expires
        fExpiry.text = fmtDate(e.expiry)
        fieldModel.clear()
        for (const f of (e.fields || [])) fieldModel.append({ key: f.key, value: f.value, prot: !!f.protected, revealed: false })
        rev++
        attachments = e.attachments || []
        modified = e.modified || 0
        historyCount = e.historyCount || 0
        fPass.revealed = false
        originalJson = JSON.stringify(current())
    }
    function load(id) {
        error = ""; info = ""; isNew = false; showGen = false
        if (!id) { clear(); return }
        loading = true
        entryId = id
        kl.readEntry(id, function (r) {
            if (ed.entryId !== id) return
            loading = false
            if (r.ok) apply(r.entry)
            else { error = r.message; clear() }
        })
    }
    function newEntry(prefill) {
        error = ""; info = ""; entryId = ""; isNew = true; showGen = false
        loading = true
        const p = prefill || {}
        apply({ title: p.title || "", username: p.username || "", password: p.password || "", url: p.url || "", groupId: p.groupId || kl.settings.defaultGroup || "" })
        if (!p.password) { const g = kl.generate(null); if (g.ok) fPass.text = g.value }
        originalJson = JSON.stringify({})
        loading = false
        Qt.callLater(() => fTitle.forceActiveFocus())
    }
    function clear() {
        loading = true
        entryId = ""; isNew = false
        fTitle.text = ""; fUser.text = ""; fPass.text = ""; fUrl.text = ""; fNotes.text = ""; fTags.text = ""
        fieldModel.clear(); attachments = []; originalJson = ""; showGen = false
        loading = false
    }
    function save(after) {
        if (saving || !dirty) { if (after) after(true); return }
        if (!fTitle.text.trim()) { error = "An entry needs a title."; fTitle.forceActiveFocus(); return }
        if (fExpires.checked && !parseDate(fExpiry.text)) { error = "Enter the expiry date as YYYY-MM-DD."; return }
        error = ""; saving = true
        const data = current()
        const done = function (r) {
            saving = false
            if (!r.ok) { error = r.message + " Your changes are kept."; if (after) after(false); return }
            info = "Saved."
            const id = r.id || ed.entryId
            ed.saved(id)
            load(id)
            if (after) after(true)
        }
        if (isNew) kl.createEntry(data, false, done)
        else { data.id = entryId; data.modified = modified; kl.updateEntry(data, done) }
    }
    function copy(value, label) {
        kl.copyValue(value, label, function (r) { if (r.ok) { info = label + " copied. Clears in " + r.seconds + "s."; error = "" } else error = r.message })
    }

    function addField() {
        fieldModel.append({ key: "", value: "", prot: false, revealed: true }); rev++
        Qt.callLater(() => { const r = fieldRows.itemAt(fieldModel.count - 1); if (r) r.children[0].focusField() })
    }

    Keys.onPressed: (e) => {
        const ctrl = e.modifiers & Qt.ControlModifier
        if (ctrl && e.key === Qt.Key_S) { save(); e.accepted = true }
        else if (ctrl && (e.modifiers & Qt.ShiftModifier) && e.key === Qt.Key_H) { showHistory(); e.accepted = true }
        else if (ctrl && e.key === Qt.Key_H) { fPass.revealed = !fPass.revealed; e.accepted = true }
        else if (ctrl && (e.modifiers & Qt.ShiftModifier) && e.key === Qt.Key_R && active) { showGen = !showGen; e.accepted = true }
        else if (ctrl && (e.modifiers & Qt.ShiftModifier) && e.key === Qt.Key_F && active) { addField(); e.accepted = true }
        else if (ctrl && e.key === Qt.Key_R && active) { const g = kl.generate(null); if (g.ok) fPass.text = g.value; e.accepted = true }
    }

    Connections { target: kl; function onIndexChanged() { if (ed.active) { const g = ed.groupItems[fGroup.currentIndex]; ed.rebuildGroups(); fGroup.currentIndex = ed.groupIndex(g ? g.id : "") } } }

    KText {
        anchors.centerIn: parent
        visible: !ed.active
        width: parent.width - th.s(40)
        horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.Wrap
        text: "Select an entry, or press Ctrl+N to create one."
        color: th.muted
    }

    ColumnLayout {
        anchors.fill: parent
        visible: ed.active
        spacing: 0

        AutoScrollView {
            id: sv
            Layout.fillWidth: true
            Layout.fillHeight: true
            ColumnLayout {
                width: sv.availableWidth
                spacing: th.s(12)
                Item { implicitHeight: th.s(6) }
                RowLayout {
                    visible: ed.conflicted
                    Layout.fillWidth: true; Layout.leftMargin: th.s(20); Layout.rightMargin: th.s(20)
                    spacing: th.s(8)
                    Banner {
                        Layout.fillWidth: true
                        kind: "warning"
                        text: ed.conflicted ? "This entry has an unresolved sync conflict. Editing and copying are blocked until you choose a version." : ""
                    }
                    KButton { text: "Resolve"; kind: "primary"; onClicked: ed.requestResolve(ed.entryId) }
                }
                TextInput {
                    id: fTitle
                    Layout.fillWidth: true; Layout.leftMargin: th.s(20); Layout.rightMargin: th.s(20)
                    font.family: th.fontFamily
                    font.pixelSize: th.base + 9
                    font.bold: true
                    color: th.fg
                    activeFocusOnTab: true
                    selectByMouse: true
                    selectionColor: th.alpha(th.accent, 0.35)
                    clip: true
                    Accessible.name: "Title"
                    KText { anchors.fill: parent; visible: !fTitle.text; text: "Title"; color: th.muted; font.pixelSize: th.base + 9 }
                    Rectangle { anchors.top: parent.bottom; anchors.topMargin: 2; width: parent.width; height: fTitle.activeFocus ? 2 : 1; color: fTitle.activeFocus ? th.accent : th.hairline }
                }
                GridLayout {
                    Layout.fillWidth: true; Layout.leftMargin: th.s(20); Layout.rightMargin: th.s(20)
                    columns: 2
                    columnSpacing: th.s(14)
                    rowSpacing: th.s(10)
                    KField {
                        id: fUser; Layout.fillWidth: true; Layout.preferredWidth: 1; label: "Username"
                        IconButton { glyph: "\uf0c5"; tip: "Copy username"; enabled: fUser.text !== ""; onClicked: ed.copy(fUser.text, "Username") }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        Layout.preferredWidth: 1
                        spacing: th.s(4)
                        KText { text: "Group"; font.pixelSize: th.base - 1; color: th.muted }
                        KCombo { id: fGroup; Layout.fillWidth: true; model: ed.groupItems; textRole: "text"; Accessible.name: "Group" }
                    }
                    KField {
                        id: fPass; Layout.fillWidth: true; Layout.columnSpan: 2; label: "Password"; secret: true; mono: true
                        IconButton { glyph: fPass.revealed ? "\uf070" : "\uf06e"; tip: (fPass.revealed ? "Hide" : "Reveal") + " (Ctrl+H)"; onClicked: fPass.revealed = !fPass.revealed }
                        IconButton { glyph: "\uf0c5"; tip: "Copy password"; enabled: fPass.text !== ""; onClicked: ed.copy(fPass.text, "Password") }
                        IconButton { glyph: "\uf021"; tip: "Generate (Ctrl+R)"; onClicked: { const g = kl.generate(null); if (g.ok) fPass.text = g.value } }
                        IconButton { glyph: "\uf013"; tip: "Generator settings (Ctrl+Shift+R)"; checked: ed.showGen; onClicked: ed.showGen = !ed.showGen }
                    }
                    GeneratorPanel {
                        Layout.fillWidth: true; Layout.columnSpan: 2
                        visible: ed.showGen
                        onUse: (v) => { fPass.text = v; ed.showGen = false }
                    }
                    KField {
                        id: fUrl; Layout.fillWidth: true; Layout.columnSpan: 2; label: "URL"
                        IconButton { glyph: "\uf0c5"; tip: "Copy URL"; enabled: fUrl.text !== ""; onClicked: ed.copy(fUrl.text, "URL") }
                        IconButton { glyph: "\uf08e"; tip: "Open in browser"; enabled: /^https?:\/\//i.test(fUrl.text); onClicked: Qt.openUrlExternally(fUrl.text) }
                    }
                    KField { id: fTags; Layout.fillWidth: true; Layout.preferredWidth: 1; label: "Tags"; placeholder: "comma separated" }
                    RowLayout {
                        Layout.fillWidth: true
                        Layout.preferredWidth: 1
                        Layout.alignment: Qt.AlignBottom
                        spacing: th.s(10)
                        KSwitch { id: fExpires; text: "Expires" }
                        KField { id: fExpiry; Layout.fillWidth: true; visible: fExpires.checked; placeholder: "YYYY-MM-DD"; error: fExpires.checked && fExpiry.text && !ed.parseDate(fExpiry.text) ? "Use YYYY-MM-DD" : "" }
                    }
                }
                ColumnLayout {
                    Layout.fillWidth: true; Layout.leftMargin: th.s(20); Layout.rightMargin: th.s(20)
                    spacing: th.s(4)
                    KText { text: "Notes"; font.pixelSize: th.base - 1; color: fNotes.activeFocus ? th.fg : th.muted }
                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: Math.max(th.s(90), fNotes.implicitHeight + th.s(12))
                        radius: th.radius
                        color: th.faint
                        border.width: fNotes.activeFocus ? 2 : 1
                        border.color: fNotes.activeFocus ? th.accent : th.hairline
                        TextEdit {
                            id: fNotes
                            anchors.fill: parent
                            anchors.margins: th.s(8)
                            wrapMode: TextEdit.Wrap
                            color: th.fg
                            font.family: th.fontFamily
                            font.pixelSize: th.base + 1
                            selectByMouse: true
                            activeFocusOnTab: true
                            selectionColor: th.alpha(th.accent, 0.35)
                            Accessible.name: "Notes"
                            Keys.onTabPressed: (e) => { nextItemInFocusChain().forceActiveFocus(); e.accepted = true }
                        }
                    }
                }
                ColumnLayout {
                    Layout.fillWidth: true; Layout.leftMargin: th.s(20); Layout.rightMargin: th.s(20)
                    spacing: th.s(6)
                    RowLayout {
                        SectionLabel { text: "Custom fields" }
                        Item { Layout.fillWidth: true }
                        KButton { text: "Add field"; glyph: "\uf067"; kind: "ghost"; hint: "Ctrl+Shift+F"; onClicked: ed.addField() }
                    }
                    KText { visible: fieldModel.count === 0; text: "No custom fields."; color: th.muted; font.pixelSize: th.base }
                    Repeater {
                        id: fieldRows
                        model: fieldModel
                        RowLayout {
                            id: fr
                            required property string key
                            required property string value
                            required property bool prot
                            required property bool revealed
                            required property int index
                            Layout.fillWidth: true
                            spacing: th.s(8)
                            KField { Layout.fillWidth: false; Layout.preferredWidth: th.s(170); placeholder: "Name"; text: fr.key; onEdited: ed.setField(fr.index, "key", text) }
                            KField {
                                Layout.fillWidth: true
                                Layout.preferredWidth: th.s(200)
                                placeholder: "Value"
                                text: fr.value
                                secret: fr.prot
                                revealed: fr.revealed
                                onEdited: ed.setField(fr.index, "value", text)
                                IconButton { visible: fr.prot; glyph: fr.revealed ? "\uf070" : "\uf06e"; tip: fr.revealed ? "Hide" : "Reveal"; onClicked: fieldModel.setProperty(fr.index, "revealed", !fr.revealed) }
                                IconButton { glyph: "\uf0c5"; tip: "Copy"; enabled: fr.value !== ""; onClicked: ed.copy(fr.value, fr.key || "Field") }
                            }
                            IconButton { glyph: fr.prot ? "\uf023" : "\uf09c"; tip: fr.prot ? "Protected (masked)" : "Not protected"; checkable: false; checked: fr.prot; onClicked: ed.setField(fr.index, "prot", !fr.prot) }
                            IconButton { glyph: "\uf00d"; tip: "Remove field"; onClicked: { fieldModel.remove(fr.index); ed.rev++ } }
                        }
                    }
                }
                ColumnLayout {
                    visible: ed.attachments.length > 0
                    Layout.fillWidth: true; Layout.leftMargin: th.s(20); Layout.rightMargin: th.s(20)
                    spacing: th.s(4)
                    SectionLabel { text: "Attachments (preserved, read-only)" }
                    Repeater {
                        model: ed.attachments
                        KText { required property var modelData; text: "\uf0c6  " + modelData.name + "  ·  " + Math.max(1, Math.round(modelData.size / 1024)) + " KiB"; color: th.muted }
                    }
                }
                Item { implicitHeight: th.s(12) }
            }
        }
        Banner { Layout.fillWidth: true; Layout.margins: th.s(10); Layout.bottomMargin: 0; kind: "error"; text: ed.error }
        Banner { Layout.fillWidth: true; Layout.margins: th.s(10); Layout.bottomMargin: 0; kind: "success"; text: ed.error ? "" : ed.info }
        Rectangle {
            Layout.fillWidth: true
            implicitHeight: bar.implicitHeight + th.s(20)
            color: th.faint
            Separator { anchors.top: parent.top; width: parent.width }
            RowLayout {
                id: bar
                anchors.fill: parent
                anchors.margins: th.s(10)
                spacing: th.s(8)
                readonly property bool tight: width < th.s(600)
                KButton { visible: !ed.isNew; iconOnly: bar.tight; text: "History" + (ed.historyCount ? " (" + ed.historyCount + ")" : ""); glyph: "\uf1da"; kind: "ghost"; hint: "Ctrl+Shift+H"; enabled: ed.historyCount > 0; onClicked: ed.showHistory() }
                KButton { visible: !ed.isNew; iconOnly: bar.tight; text: "Move to trash"; glyph: "\uf1f8"; kind: "ghost"; onClicked: ed.trash() }
                Item { Layout.fillWidth: true }
                KText { visible: ed.dirty; text: bar.tight ? "Unsaved" : "Unsaved changes"; color: th.warning; font.pixelSize: th.base - 1 }
                KButton { text: ed.isNew ? "Discard" : "Revert"; kind: "ghost"; enabled: ed.dirty || ed.isNew; onClicked: ed.isNew ? ed.clear() : ed.load(ed.entryId) }
                KButton { text: ed.saving ? "Saving…" : ed.isNew ? "Create" : "Save"; hint: "Ctrl+S"; kind: "primary"; enabled: ed.dirty && !ed.saving; onClicked: ed.save() }
            }
        }
    }
    signal trashRequested(string id)
    function trash() { trashRequested(entryId) }
    function showHistory() { if (active && !isNew && historyCount > 0) historyPopup.openFor(entryId) }

    HistoryPopup { id: historyPopup; onRestored: ed.load(ed.entryId) }
}
