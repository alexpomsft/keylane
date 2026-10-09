import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Dialogs

// First-run: open an existing KDBX vault or create a new one.
Rectangle {
    id: sp
    property string mode: "open"     // open | create
    property string error: ""
    property bool working: false
    implicitHeight: col.implicitHeight + th.s(48)
    color: th.surface
    radius: th.radius
    border.color: th.border
    border.width: th.s(2)

    function openExisting() {
        error = ""
        const r = kl.useVault(path.text.trim(), keyfile.text.trim())
        if (!r.ok) error = r.message
    }
    function create() {
        error = ""
        if (pw1.text !== pw2.text) { error = "The passwords do not match."; return }
        if (pw1.text && pw1.text.length < 8 && !keyfile.text) { error = "Use at least 8 characters, or add a key file."; return }
        working = true
        const p = pw1.text
        pw1.text = ""; pw2.text = ""
        kl.createVault(path.text.trim(), p, keyfile.text.trim(), function (r) {
            sp.working = false
            if (!r.ok) sp.error = r.message
        })
    }

    FileDialog { id: openDlg; title: "Choose a KDBX vault"; nameFilters: ["KeePass databases (*.kdbx)", "All files (*)"]; onAccepted: path.text = kl.localPath(selectedFile) }
    FileDialog { id: saveDlg; title: "Create vault"; fileMode: FileDialog.SaveFile; defaultSuffix: "kdbx"; nameFilters: ["KeePass databases (*.kdbx)"]; onAccepted: path.text = kl.localPath(selectedFile) }
    FileDialog { id: keyDlg; title: "Choose a key file"; onAccepted: keyfile.text = kl.localPath(selectedFile) }

    ColumnLayout {
        id: col
        anchors.fill: parent
        anchors.margins: th.s(24)
        spacing: th.s(14)
        RowLayout {
            spacing: th.s(12)
            Glyph { g: "\uf084"; color: th.accent; font.pixelSize: th.base + 14 }
            ColumnLayout {
                spacing: 0
                KText { text: "Welcome to Keylane"; font.pixelSize: th.base + 6; font.bold: true }
                KText { text: "Your vault is a standard KeePass (KDBX) file. Keylane never uploads it anywhere."; color: th.muted; wrapMode: Text.Wrap; Layout.fillWidth: true }
            }
        }
        RowLayout {
            spacing: th.s(6)
            KButton { text: "Open existing vault"; kind: sp.mode === "open" ? "primary" : "ghost"; onClicked: { sp.mode = "open"; sp.error = "" } }
            KButton { text: "Create new vault"; kind: sp.mode === "create" ? "primary" : "ghost"; onClicked: { sp.mode = "create"; sp.error = "" } }
        }
        KField {
            id: path
            Layout.fillWidth: true
            label: sp.mode === "open" ? "Vault file" : "Save new vault as"
            placeholder: sp.mode === "open" ? "~/Sync/passwords.kdbx" : "~/Documents/keylane.kdbx"
            onAccepted: sp.mode === "open" ? sp.openExisting() : pw1.focusField()
            IconButton { glyph: "\uf07c"; tip: "Browse…"; onClicked: sp.mode === "open" ? openDlg.open() : saveDlg.open() }
        }
        KField {
            id: keyfile
            Layout.fillWidth: true
            label: "Key file (optional)"
            placeholder: "none"
            IconButton { glyph: "\uf07c"; tip: "Browse…"; onClicked: keyDlg.open() }
        }
        KField { id: pw1; visible: sp.mode === "create"; Layout.fillWidth: true; label: "Master password"; secret: true; onAccepted: pw2.focusField() }
        KField { id: pw2; visible: sp.mode === "create"; Layout.fillWidth: true; label: "Repeat password"; secret: true; onAccepted: sp.create() }
        KText {
            visible: sp.mode === "create"
            Layout.fillWidth: true; wrapMode: Text.Wrap; color: th.muted; font.pixelSize: th.base - 1
            text: "New vaults use KDBX 4 with Argon2id and AES-256. There is no recovery if you forget the password — consider a passphrase from the generator."
        }
        Banner { Layout.fillWidth: true; text: sp.error }
        RowLayout {
            Item { Layout.fillWidth: true }
            KButton {
                text: sp.mode === "open" ? "Open vault" : sp.working ? "Creating…" : "Create vault"
                kind: "primary"; hint: "↵"
                enabled: path.text.trim() !== "" && !sp.working
                onClicked: sp.mode === "open" ? sp.openExisting() : sp.create()
            }
        }
    }
    Component.onCompleted: Qt.callLater(path.focusField)
}
