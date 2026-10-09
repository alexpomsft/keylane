import QtQuick
import QtQuick.Layouts

// Password prompt for the configured vault. Used by launcher and manager.
ColumnLayout {
    id: u
    property bool compact: false
    property string error: ""
    signal unlocked()
    signal cancelled()
    spacing: th.s(10)
    function focusInput() { pw.focusField() }
    function reset() { pw.text = ""; error = "" }
    function submit() {
        if (kl.busy) return
        error = ""
        const value = pw.text
        pw.text = ""
        kl.unlock(value, "", function (r) {
            if (r.ok) { u.unlocked() }
            else { u.error = r.message; pw.focusField() }
        })
    }

    RowLayout {
        spacing: th.s(10)
        Glyph { g: "\uf023"; color: th.accent; font.pixelSize: th.base + 6 }
        ColumnLayout {
            spacing: 0
            KText { text: "Unlock " + (kl.vaultName || "vault"); font.pixelSize: th.base + 4; font.bold: true }
            KText {
                text: kl.keyfilePath ? "Password + key file" : "Master password"
                color: th.muted
                font.pixelSize: th.base - 1
            }
        }
        Item { Layout.fillWidth: true }
    }
    KField {
        id: pw
        Layout.fillWidth: true
        secret: true
        placeholder: kl.keyfilePath ? "Password (leave empty if the key file is enough)" : "Password"
        onAccepted: u.submit()
        input.Keys.onEscapePressed: u.cancelled()
        IconButton {
            glyph: pw.revealed ? "\uf070" : "\uf06e"
            tip: pw.revealed ? "Hide" : "Show"
            onClicked: pw.revealed = !pw.revealed
        }
    }
    Banner { Layout.fillWidth: true; kind: "error"; text: u.error }
    Banner {
        Layout.fillWidth: true
        kind: "info"
        text: kl.externalChangeWhileLocked ? "The vault file changed while locked. Changes will be merged when you unlock." : ""
    }
    KText {
        visible: kl.busy
        text: "Unlocking…"
        color: th.muted
    }
}
