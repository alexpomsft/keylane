"""Typed theme tokens for QML, exposed as the ``th`` context property."""

from __future__ import annotations

from PySide6.QtCore import Property, QObject, Signal, Slot
from PySide6.QtGui import QColor


def _c(hexv: str, alpha: float = 1.0) -> QColor:
    c = QColor(hexv)
    if not c.isValid():
        c = QColor("#888888")
    c.setAlphaF(max(0.0, min(1.0, c.alphaF() * alpha)))
    return c


class ThemeObject(QObject):
    changed = Signal()

    def __init__(self, backend, parent=None):
        super().__init__(parent)
        self._b = backend
        backend.themeChanged.connect(self.changed)

    def _t(self, k, d=None):
        return self._b.theme.get(k, d)

    def _col(name, key, alpha_key=None, alpha=1.0, _sig=changed):  # noqa: N805
        def get(self):
            a = self._t(alpha_key, 1.0) if alpha_key else 1.0
            return _c(self._t(key, "#888888"), a * alpha)
        return Property(QColor, get, notify=_sig)

    bg = _col("bg", "bg", "bgAlpha")
    bgSolid = _col("bgSolid", "bg")
    surface = _col("surface", "surface")
    fg = _col("fg", "fg")
    muted = _col("muted", "muted")
    accent = _col("accent", "accent")
    border = _col("border", "border", "borderAlpha")
    scrim = _col("scrim", "scrim", "scrimAlpha")
    sel = _col("sel", "selBg", "selBgAlpha")
    selFg = _col("selFg", "selFg")
    selBorder = _col("selBorder", "selBorder", "selBorderAlpha")
    danger = _col("danger", "danger")
    warning = _col("warning", "warning")
    success = _col("success", "success")
    hairline = _col("hairline", "fg", None, 0.12)
    faint = _col("faint", "fg", None, 0.05)
    hover = _col("hover", "fg", None, 0.06)
    del _col

    @Property(str, notify=changed)
    def fontFamily(self):
        return self._t("fontFamily", "monospace")

    @Property(int, notify=changed)
    def base(self):
        return int(self._t("fontBase", 12))

    @Property(int, notify=changed)
    def radius(self):
        return int(self._t("radius", 0))

    @Property(bool, notify=changed)
    def dark(self):
        return self._t("mode", "dark") != "light"

    @Property(str, notify=changed)
    def source(self):
        return self._t("source", "fallback")

    @Slot(float, result=int)
    def s(self, n: float) -> int:
        """Scale a spacing value designed at base size 12."""
        return max(1, round(n * self.base / 12))

    @Slot(QColor, float, result=QColor)
    def alpha(self, c: QColor, a: float) -> QColor:
        c = QColor(c)
        c.setAlphaF(max(0.0, min(1.0, a)))
        return c
