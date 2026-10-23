"""
Indie Now · kit visual compartido
================================
Estética IndieDL para apps de escritorio Tk: fondo casi negro, tarjetas
redondeadas, acento amarillo #FFF333 y tipografía Segoe UI Black.

No requiere customtkinter: usa Tk + Pillow (para los bordes redondeados
antialias y el banner halftone) y animaciones con `after`.
"""

from __future__ import annotations

import math
import tkinter as tk
from collections import OrderedDict
from tkinter import font as tkfont

try:
    from PIL import Image, ImageDraw, ImageFont, ImageTk
    PIL_OK = True
except Exception:  # pragma: no cover - sin Pillow la app sigue funcionando
    PIL_OK = False

# ─────────────────────────────────────────────────────────────
# PALETA / TIPOGRAFÍA
# ─────────────────────────────────────────────────────────────
BG = "#0D1116"          # fondo de ventana (IndieDL)
BG_DEEP = "#0A0F14"     # consola / insets
BLACK = "#0A0D10"
SURFACE = "#151B21"     # tarjetas
SURFACE_2 = "#1A2129"
FIELD = "#20272E"       # inputs / botones oscuros
FIELD_H = "#2B333C"
BORDER = "#303942"
BORDER_SOFT = "#262F38"
BORDER_FIELD = "#39434D"
TEXT = "#F4F6F8"
MUTED = "#AAB4BE"
DIM = "#6E7A87"
YEL = "#FFF333"
YEL_H = "#FFE600"
YEL_DIM = "#C9BE2A"
GREEN = "#4ADE80"
GREEN_BG = "#10231A"
RED = "#FF5B65"
RED_BG = "#241114"
AMBER = "#FFC24B"
AMBER_BG = "#241E10"

F_BLACK = "Segoe UI Black"
F_SEMI = "Segoe UI Semibold"
F_BODY = "Segoe UI"
F_MONO = "Consolas"


def init_fonts(root):
    """Ajusta las familias a lo que realmente tenga el sistema."""
    global F_BLACK, F_SEMI, F_BODY, F_MONO
    try:
        fams = set(tkfont.families(root))
    except Exception:
        return

    def pick(*cands):
        for c in cands:
            if c in fams:
                return c
        return "Arial"

    F_BLACK = pick("Segoe UI Black", "Arial Black", "Segoe UI", "Arial")
    F_SEMI = pick("Segoe UI Semibold", "Segoe UI", "Arial")
    F_BODY = pick("Segoe UI", "Arial")
    F_MONO = pick("Consolas", "Cascadia Mono", "Courier New")


# ─────────────────────────────────────────────────────────────
# COLOR / IMAGEN
# ─────────────────────────────────────────────────────────────
def _rgb(color):
    c = color.lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def mix(a, b, t):
    """Mezcla dos colores hex (t=0 → a, t=1 → b)."""
    t = max(0.0, min(1.0, t))
    ca, cb = _rgb(a), _rgb(b)
    return "#%02X%02X%02X" % tuple(round(ca[i] + (cb[i] - ca[i]) * t) for i in range(3))


def spaced(text):
    """Texto con aire entre letras, como la banda del banner de IndieDL."""
    return "   ".join(" ".join(w) for w in text.split())


_CACHE = OrderedDict()
_CACHE_MAX = 220


def _cached(key, builder):
    try:
        if key in _CACHE:
            _CACHE.move_to_end(key)
            return _CACHE[key]
    except Exception:
        pass
    value = builder()
    _CACHE[key] = value
    if len(_CACHE) > _CACHE_MAX:
        _CACHE.popitem(last=False)
    return value


def rounded_image(w, h, radius, fill, border=None, border_w=1, glow=None,
                  bgcolor=None):
    """Rectángulo redondeado antialias (2x + LANCZOS).

    Si se pasa `bgcolor`, la imagen se genera OPACA con ese color en las
    esquinas: Tk dibuja mucho más rápido y sin fantasmas al scrollear.
    """
    w, h = max(2, int(w)), max(2, int(h))
    radius = max(0, min(int(radius), min(w, h) // 2))
    key = ("round", w, h, radius, fill, border, border_w, glow, bgcolor)

    def build():
        if not PIL_OK:
            return None
        s = 2 if w * h <= 1_200_000 else 1
        if bgcolor:
            img = Image.new("RGB", (w * s, h * s), bgcolor)
        else:
            img = Image.new("RGBA", (w * s, h * s), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle(
            [0, 0, w * s - 1, h * s - 1],
            radius=radius * s,
            fill=fill,
            outline=border or fill,
            width=max(1, border_w * s),
        )
        if glow:
            d.line([(radius * s, s), (w * s - radius * s, s)], fill=glow, width=s)
        if s > 1:
            img = img.resize((w, h), Image.LANCZOS)
        return img

    return _cached(key, build)


def card_image(w, h, radius=12, fill=SURFACE, border=BORDER, bgcolor=None):
    return rounded_image(w, h, radius, fill, border, 1,
                         glow=None if fill == BLACK else "#1E262E",
                         bgcolor=bgcolor)


def rounded_photo(w, h, radius, fill, border=None, border_w=1, glow=None,
                  bgcolor=None):
    img = rounded_image(w, h, radius, fill, border, border_w, glow, bgcolor)
    if img is None:
        return None
    return ImageTk.PhotoImage(img)


# ─────────────────────────────────────────────────────────────
# ARTE DE MARCA
# ─────────────────────────────────────────────────────────────
def _font_file(*names):
    for name in names:
        path = "C:/Windows/Fonts/" + name
        try:
            with open(path, "rb"):
                return path
        except Exception:
            continue
    return None


def banner_image(width, height=104):
    """Banner halftone 'INDIE NOW' con banda amarilla, como IndieDL."""
    width = max(320, int(width))
    height = int(height)
    key = ("banner", width, height)

    def build():
        if not PIL_OK:
            return None
        s = 2
        W, H = width * s, height * s
        img = Image.new("RGB", (W, H), "#F8F8F4")
        d = ImageDraw.Draw(img)

        path = _font_file("seguibl.ttf", "ariblk.ttf", "arialbd.ttf")
        size = int(H * 0.62)
        font = None
        target = W * 0.63
        while size > 12:
            try:
                font = ImageFont.truetype(path, size) if path else ImageFont.load_default()
            except Exception:
                font = ImageFont.load_default()
            bbox = d.textbbox((0, 0), "INDIE NOW", font=font)
            if bbox[2] - bbox[0] <= target:
                break
            size -= 4
        text = "INDIE NOW"
        bbox = d.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        tx = (W - tw) // 2 - bbox[0]
        ty = (H - th) // 2 - bbox[1]
        # Zona libre de trama alrededor del logo (knockout de imprenta).
        kx1 = tx + bbox[0] - int(16 * s)
        ky1 = ty + bbox[1] - int(8 * s)
        kx2 = tx + bbox[2] + int(16 * s)
        ky2 = ty + bbox[3] + int(9 * s)

        step = max(8, int(13 * s / 2))
        for row, y in enumerate(range(0, H, step)):
            for x in range(0, W, step):
                xx = x + (step // 2 if row % 2 else 0)
                if kx1 <= xx <= kx2 and ky1 <= y <= ky2:
                    continue
                il = max(0.0, 1 - math.hypot(xx, y) / (0.40 * W))
                ir = max(0.0, 1 - math.hypot(W - xx, y) / (0.26 * W))
                inten = max(il, ir) ** 1.15
                r = inten * step * 0.60
                if r > 0.8:
                    d.ellipse([xx - r, y - r, xx + r, y + r], fill="black")
        d.polygon([(int(W * .095), 0), (int(W * .145), 0),
                   (int(W * .095), int(H * .60)), (int(W * .055), int(H * .60))],
                  fill="black")

        d.rectangle([tx + bbox[0] - int(12 * s), ty + bbox[1] + int(th * .54),
                     tx + bbox[2] + int(12 * s), ty + bbox[3] + int(4 * s)],
                    fill="#FFF333")
        d.text((tx, ty), text, font=font, fill="#0A0D10")
        img = img.resize((width, height), Image.LANCZOS)
        return img

    return _cached(key, build)


def banner_photo(width, height=104):
    img = banner_image(width, height)
    return ImageTk.PhotoImage(img) if img is not None else None


def icon_image(size=64):
    """Icono de ventana: cuadrado negro con 'IN' amarillo."""
    def build():
        if not PIL_OK:
            return None
        s = 4
        S = size * s
        img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * .22), fill=BLACK,
                            outline=YEL, width=max(2, int(S * .045)))
        path = _font_file("seguibl.ttf", "ariblk.ttf", "arialbd.ttf")
        try:
            font = ImageFont.truetype(path, int(S * .50)) if path else ImageFont.load_default()
        except Exception:
            font = ImageFont.load_default()
        text = "IN"
        bb = d.textbbox((0, 0), text, font=font)
        d.text(((S - (bb[2] - bb[0])) // 2 - bb[0], (S - (bb[3] - bb[1])) // 2 - bb[1] - int(S * .03)),
               text, font=font, fill=YEL)
        d.rectangle([int(S * .20), int(S * .80), int(S * .80), int(S * .86)], fill=YEL)
        return img.resize((size, size), Image.LANCZOS)

    return _cached(("icon", size), build)


def apply_window_icon(root):
    try:
        img = icon_image(64)
        if img is not None:
            photo = ImageTk.PhotoImage(img)
            root.iconphoto(True, photo)
            root._indie_icon = photo
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────
# UTILIDADES DE LAYOUT
# ─────────────────────────────────────────────────────────────
def round_rect_item(cv, x1, y1, x2, y2, r, **kw):
    """Rectángulo redondeado dibujado en un Canvas (bordes suaves)."""
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    pts = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return cv.create_polygon(pts, smooth=True, splinesteps=24, **kw)


def autowrap(widget, extra=24, minimum=140):
    host = widget.master
    def update(_=None):
        try:
            w = host.winfo_width()
            if w > 60:
                widget.configure(wraplength=max(minimum, w - extra))
        except tk.TclError:
            pass
    host.bind("<Configure>", update, add="+")
    try:
        widget.after(40, update)
    except Exception:
        pass


def hline(parent, color=BORDER_SOFT, pady=0):
    line = tk.Frame(parent, bg=color, height=1)
    line.pack(fill="x", pady=pady)
    return line


# ─────────────────────────────────────────────────────────────
# TARJETA REDONDEADA
# ─────────────────────────────────────────────────────────────
class Rounded(tk.Frame):
    """Panel redondeado con un `body` normal para meter contenido."""

    def __init__(self, master, fill=SURFACE, border=BORDER, radius=12, pad=14,
                 bg=None, **kw):
        base = bg or master.cget("bg")
        super().__init__(master, bg=base, bd=0, highlightthickness=0, **kw)
        self._base = base
        self.fill = fill
        self.border = border
        self.radius = radius
        self.pad = pad
        self.canvas = tk.Canvas(self, bg=base, bd=0, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.body = tk.Frame(self.canvas, bg=fill)
        self._win = self.canvas.create_window(pad, pad, window=self.body, anchor="nw")
        self._img = None
        self._item = None
        self._job = None
        self._photo_cache = None
        self._size = (0, 0)
        self._body_size = (0, 0)
        self.canvas.bind("<Configure>", self._on_canvas)
        self.body.bind("<Configure>", self._on_body)

    def _on_canvas(self, event):
        # OJO: <Configure> también llega cuando el widget se MUEVE (scroll).
        # Si el tamaño no cambió, no hay que regenerar la imagen de fondo.
        size = (event.width, event.height)
        if size == self._size:
            return
        self._size = size
        try:
            self.canvas.itemconfigure(self._win, width=max(1, event.width - self.pad * 2))
        except tk.TclError:
            return
        self._schedule_paint()

    def _on_body(self, event):
        size = (event.width, event.height)
        if size == self._body_size:
            return
        self._body_size = size
        target = int(event.height) + self.pad * 2
        try:
            if int(self.canvas.cget("height") or 0) != target:
                self.canvas.configure(height=target)
        except (tk.TclError, ValueError):
            pass

    def _schedule_paint(self):
        if self._job:
            try:
                self.after_cancel(self._job)
            except Exception:
                pass
        try:
            self._job = self.after(20, self._paint)
        except tk.TclError:
            pass

    def _paint(self):
        self._job = None
        try:
            w = self.canvas.winfo_width()
            h = self.canvas.winfo_height()
        except tk.TclError:
            return
        if w < 4 or h < 4:
            return
        photo = rounded_photo(w, h, self.radius, self.fill, self.border,
                              bgcolor=self._base)
        if photo is None:
            self.delete("all")
            round_rect_item(self.canvas, 1, 1, w - 1, h - 1, self.radius,
                            fill=self.fill, outline=self.border)
            return
        self._photo_cache = photo
        if self._item is None:
            self._item = self.canvas.create_image(0, 0, anchor="nw", image=photo)
        else:
            self.canvas.itemconfigure(self._item, image=photo)
        self.canvas.tag_lower(self._item)

    def set_border(self, color):
        if color != self.border:
            self.border = color
            self._schedule_paint()


# ─────────────────────────────────────────────────────────────
# BOTÓN
# ─────────────────────────────────────────────────────────────
class BrandButton(tk.Canvas):
    PALETTE = {
        "primary": dict(fill=YEL, hover=YEL_H, press="#E4D400", fg=BLACK, border=None),
        "dark": dict(fill=FIELD, hover=FIELD_H, press="#192027", fg=TEXT, border=BORDER_FIELD),
        "chip": dict(fill="#252C33", hover="#343D46", press="#1D242B", fg=YEL, border=BORDER),
        "ghost": dict(fill=SURFACE, hover=SURFACE_2, press=FIELD, fg=MUTED, border=BORDER_SOFT),
        "danger": dict(fill="#2A1417", hover="#3A1A1E", press="#201013", fg=RED, border="#4A2226"),
    }

    def __init__(self, master, text="", command=None, kind="primary", height=44,
                 radius=10, font=None, bg=None, icon="", padx=12, cursor="hand2", **kw):
        base = bg or master.cget("bg")
        super().__init__(master, height=height, bg=base, bd=0, highlightthickness=0,
                         cursor=cursor, **kw)
        self._base = base
        self._kind = kind if kind in self.PALETTE else "primary"
        self._text = text
        self._icon = icon
        self._enabled = True
        self._radius = radius
        self._font = font or (F_BLACK, 12)
        self._padx = padx
        self._command = command
        self._job = None
        self._anim = None
        self._photo = None
        self._hover = False
        self._press = False
        self._size = (0, 0)
        self.bind("<Configure>", self._on_configure)
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)

    def _on_configure(self, event):
        size = (event.width, event.height)
        if size == self._size:
            return
        self._size = size
        self._draw()

    # -- estado -------------------------------------------------
    def _fill(self):
        pal = self.PALETTE[self._kind]
        if not self._enabled:
            return "#1B222A", "#39434D", "#5B6672"
        if self._press:
            return pal["press"], pal["border"], pal["fg"]
        if self._hover:
            return pal["hover"], pal["border"], pal["fg"]
        return pal["fill"], pal["border"], pal["fg"]

    def _draw(self):
        try:
            w, h = self.winfo_width(), self.winfo_height()
        except tk.TclError:
            return
        if w < 4 or h < 4:
            return
        fill, border, fg = self._fill()
        photo = rounded_photo(w, h, self._radius, fill, border,
                              1 if border else 0, None, bgcolor=self._base)
        self._photo = photo
        self.delete("all")
        if photo is not None:
            self.create_image(0, 0, anchor="nw", image=photo)
        else:
            round_rect_item(self, 1, 1, w - 1, h - 1, self._radius,
                            fill=fill, outline=border or fill)
        label = (self._icon + "  " + self._text).strip() if self._icon else self._text
        self.create_text(w // 2, h // 2 - 1, text=label, fill=fg,
                         font=self._font, anchor="center")
        if not self._enabled:
            try:
                self.configure(cursor="arrow")
            except tk.TclError:
                pass
        else:
            try:
                self.configure(cursor="hand2")
            except tk.TclError:
                pass

    def _set_hover(self, on):
        if not self._enabled:
            return
        self._hover = on
        self._draw()

    def _on_press(self, _=None):
        if not self._enabled:
            return
        self._press = True
        self._draw()

    def _on_release(self, _=None):
        if not self._enabled:
            return
        was = self._press
        self._press = False
        self._draw()
        if was and self._command:
            try:
                self._command()
            except Exception:
                pass

    # -- API ----------------------------------------------------
    def set_text(self, text, icon=None):
        self._text = text
        if icon is not None:
            self._icon = icon
        self._draw()

    def set_kind(self, kind):
        if kind in self.PALETTE and kind != self._kind:
            self._kind = kind
            self._draw()

    def set_enabled(self, enabled):
        if self._enabled != bool(enabled):
            self._enabled = bool(enabled)
            self._draw()

    def is_enabled(self):
        return self._enabled


# ─────────────────────────────────────────────────────────────
# CHECKBOX
# ─────────────────────────────────────────────────────────────
_CHECK_CACHE = {}


def _check_img(size, checked, hover, disabled=False):
    key = ("chk", size, checked, hover, disabled)
    if key in _CHECK_CACHE:
        return _CHECK_CACHE[key]
    if not PIL_OK:
        return None
    s = 4
    S = size * s
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = int(S * 0.28)
    if disabled:
        fill, edge = "#161C23", "#333C46"
    elif checked:
        fill, edge = YEL, YEL
    else:
        fill, edge = ("#1B232B" if hover else "#161D24"), (YEL if hover else "#3B4652")
    d.rounded_rectangle([2, 2, S - 3, S - 3], radius=r, fill=fill, outline=edge,
                        width=int(S * 0.10))
    if checked:
        d.line([(S * .26, S * .52), (S * .44, S * .70), (S * .76, S * .33)],
               fill=BLACK if not disabled else "#6B7683", width=int(S * 0.13),
               joint="curve")
    img = img.resize((size, size), Image.LANCZOS)
    photo = ImageTk.PhotoImage(img)
    _CHECK_CACHE[key] = photo
    return photo


def check_photo(size, checked, hover=False, disabled=False):
    """Foto de casilla para usar en filas propias."""
    return _check_img(size, checked, hover, disabled)


class BrandCheck(tk.Frame):
    """Fila seleccionable con caja amarilla y etiqueta."""

    def __init__(self, master, text, variable, command=None, bg=None,
                 font=None, size=21, pad=(0, 3), disabled=False):
        base = bg or master.cget("bg")
        super().__init__(master, bg=base)
        self.variable = variable
        self.command = command
        self._bg = base
        self._hover = False
        self._disabled = disabled
        self.box = tk.Canvas(self, width=size, height=size, bg=base, bd=0,
                             highlightthickness=0, cursor="hand2")
        self.box.pack(side="left", pady=pad)
        self.lbl = tk.Label(self, text=text, bg=base, fg=TEXT,
                            font=font or (F_SEMI, 11), anchor="w", justify="left",
                            cursor="hand2")
        self.lbl.pack(side="left", padx=(10, 0), fill="x", expand=True, pady=pad)
        self._size = size
        self._photo = None
        self._disabled = disabled
        for w in (self.box, self.lbl):
            w.bind("<Button-1>", self._toggle)
            w.bind("<Enter>", lambda e: self._hovering(True))
            w.bind("<Leave>", lambda e: self._hovering(False))
        try:
            self.variable.trace_add("write", lambda *_: self._paint())
        except Exception:
            pass
        self._paint()

    def _hovering(self, on):
        if self._disabled:
            return
        self._hover = on
        self.lbl.configure(fg=YEL if on else TEXT)
        self._paint()

    def _toggle(self, _=None):
        if self._disabled:
            return
        self.variable.set(not bool(self.variable.get()))
        if self.command:
            try:
                self.command()
            except Exception:
                pass

    def _paint(self):
        checked = bool(self.variable.get())
        photo = _check_img(self._size, checked, self._hover, self._disabled)
        self._photo = photo
        self.box.delete("all")
        if photo is not None:
            self.box.create_image(0, 0, anchor="nw", image=photo)
        else:
            self.box.create_rectangle(1, 1, self._size - 1, self._size - 1,
                                      outline=YEL if checked else "#3B4652",
                                      fill=YEL if checked else "", width=2)
        self.lbl.configure(fg=(DIM if self._disabled else
                               (YEL if self._hover else TEXT)))

    def set_enabled(self, enabled):
        self._disabled = not enabled
        for w in (self.box, self.lbl):
            try:
                w.configure(cursor="hand2" if enabled else "arrow")
            except tk.TclError:
                pass
        self._paint()


# ─────────────────────────────────────────────────────────────
# SELECTOR SEGMENTADO
# ─────────────────────────────────────────────────────────────
class Segmented(tk.Frame):
    def __init__(self, master, options, variable, command=None, height=38,
                 bg=None, font=None, gap=6):
        base = bg or master.cget("bg")
        super().__init__(master, bg=base)
        self.variable = variable
        self.command = command
        self.buttons = {}
        for i, (value, label) in enumerate(options):
            btn = BrandButton(self, text=label, kind="dark", height=height,
                              radius=9, font=font or (F_BLACK, 10), bg=base,
                              command=lambda v=value: self._choose(v))
            btn.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else gap, 0))
            self.grid_columnconfigure(i, weight=1, uniform="seg")
            self.buttons[value] = btn
        try:
            variable.trace_add("write", lambda *_: self.refresh())
        except Exception:
            pass
        self.refresh()

    def _choose(self, value):
        if value == self.variable.get():
            return
        self.variable.set(value)
        if self.command:
            try:
                self.command()
            except Exception:
                pass

    def refresh(self):
        current = self.variable.get()
        for value, btn in self.buttons.items():
            btn.set_kind("primary" if value == current else "dark")

    def set_enabled(self, enabled):
        for btn in self.buttons.values():
            btn.set_enabled(enabled)


# ─────────────────────────────────────────────────────────────
# SELECT (menú oscuro propio)
# ─────────────────────────────────────────────────────────────
class BrandSelect(tk.Canvas):
    def __init__(self, master, values, variable, command=None, height=42,
                 bg=None, font=None):
        base = bg or master.cget("bg")
        super().__init__(master, height=height, bg=base, bd=0,
                         highlightthickness=0, cursor="hand2")
        self._base = base
        self._values = list(values)
        self.variable = variable
        self.command = command
        self._font = font or (F_BODY, 11)
        self._photo = None
        self._popup = None
        self._hover = False
        self._enabled = True
        self._size = (0, 0)
        self.bind("<Configure>", self._on_configure)
        self.bind("<Enter>", lambda e: self._hov(True))
        self.bind("<Leave>", lambda e: self._hov(False))
        self.bind("<Button-1>", lambda e: self._open())
        try:
            variable.trace_add("write", lambda *_: self._draw())
        except Exception:
            pass

    def _on_configure(self, event):
        size = (event.width, event.height)
        if size == self._size:
            return
        self._size = size
        self._draw()

    def set_values(self, values):
        self._values = list(values)
        self._draw()

    def set_enabled(self, enabled):
        self._enabled = bool(enabled)
        try:
            self.configure(cursor="hand2" if self._enabled else "arrow")
        except tk.TclError:
            pass
        if not self._enabled:
            self._close()
        self._draw()

    def _hov(self, on):
        if not self._enabled:
            return
        self._hover = on
        self._draw()

    def _draw(self):
        try:
            w, h = self.winfo_width(), self.winfo_height()
        except tk.TclError:
            return
        if w < 4 or h < 4:
            return
        if not self._enabled:
            fill, border, fg, arrow = "#131A20", BORDER_SOFT, DIM, "#4A5560"
        else:
            fill, border = (FIELD_H if self._hover else FIELD), BORDER_FIELD
            fg, arrow = TEXT, YEL
        photo = rounded_photo(w, h, 9, fill, border, 1, None,
                              bgcolor=self._base)
        self._photo = photo
        self.delete("all")
        if photo is not None:
            self.create_image(0, 0, anchor="nw", image=photo)
        else:
            round_rect_item(self, 1, 1, w - 1, h - 1, 9, fill=fill,
                            outline=border)
        text = self.variable.get() or "—"
        if len(text) > 46:
            text = text[:45] + "…"
        self.create_text(14, h // 2 - 1, text=text, anchor="w", fill=fg,
                         font=self._font)
        self.create_text(w - 16, h // 2 - 2, text="▾", anchor="e", fill=arrow,
                         font=(F_BODY, 12, "bold"))

    def _close(self):
        if self._popup is not None:
            try:
                self._popup.grab_release()
            except Exception:
                pass
            try:
                self._popup.destroy()
            except Exception:
                pass
            self._popup = None
            try:
                self.unbind_all("<Button-1>")
                self.unbind_all("<Escape>")
            except Exception:
                pass

    def _open(self):
        if self._popup is not None or not self._values or not self._enabled:
            return
        top = tk.Toplevel(self)
        top.overrideredirect(True)
        top.configure(bg=BORDER)
        top.attributes("-topmost", True)
        holder = tk.Frame(top, bg=SURFACE)
        holder.pack(fill="both", expand=True, padx=1, pady=1)
        rows = min(len(self._values), 12)
        for value in self._values[:rows]:
            selected = value == self.variable.get()
            row = tk.Label(holder, text=("●  " if selected else "    ") + value,
                           bg=SURFACE_2 if selected else SURFACE,
                           fg=YEL if selected else TEXT,
                           font=(F_SEMI, 10), anchor="w", padx=12, pady=8)
            row.pack(fill="x")
            row.bind("<Enter>", lambda e, r=row: r.configure(bg=FIELD, fg=YEL))
            row.bind("<Leave>", lambda e, r=row, s=selected: r.configure(
                bg=SURFACE_2 if s else SURFACE, fg=YEL if s else TEXT))
            row.bind("<Button-1>", lambda e, v=value: self._choose(v))
        try:
            self.update_idletasks()
            x = self.winfo_rootx()
            y = self.winfo_rooty() + self.winfo_height() + 4
            alto = min(len(self._values), 12) * 34 + 2
            try:
                if y + alto > self.winfo_screenheight() - 16:
                    y = max(8, self.winfo_rooty() - alto - 4)
                x = max(8, min(x, self.winfo_screenwidth() - self.winfo_width() - 8))
            except Exception:
                pass
            top.geometry(f"{self.winfo_width()}x{alto}+{x}+{y}")
        except tk.TclError:
            pass
        self._popup = top
        try:
            top.grab_set()
        except tk.TclError:
            pass
        try:
            top.bind_all("<Button-1>", self._maybe_close, add="+")
            top.bind_all("<Escape>", lambda e: self._close(), add="+")
        except Exception:
            pass

    def _maybe_close(self, event):
        try:
            if self._popup is None:
                return
            x, y = event.x_root, event.y_root
            px, py = self._popup.winfo_rootx(), self._popup.winfo_rooty()
            pw, ph = self._popup.winfo_width(), self._popup.winfo_height()
            if not (px <= x <= px + pw and py <= y <= py + ph):
                self._close()
        except Exception:
            self._close()

    def _choose(self, value):
        self._close()
        self.variable.set(value)
        if self.command:
            try:
                self.command()
            except Exception:
                pass


# ─────────────────────────────────────────────────────────────
# PROGRESO
# ─────────────────────────────────────────────────────────────
class Meter(tk.Canvas):
    def __init__(self, master, height=8, bg=None, fill=YEL, track="#222B33"):
        base = bg or master.cget("bg")
        super().__init__(master, height=height, bg=base, bd=0, highlightthickness=0)
        self._fill = fill
        self._track = track
        self._target = 0.0
        self._value = 0.0
        self._job = None
        self._bar = None
        self._size = (0, 0)
        self.bind("<Configure>", self._on_configure)

    def _on_configure(self, event):
        size = (event.width, event.height)
        if size == self._size:
            return
        self._size = size
        self._draw()

    def set(self, value, animate=True):
        try:
            value = max(0.0, min(1.0, float(value)))
        except Exception:
            value = 0.0
        self._target = value
        if not animate:
            self._value = value
            self._draw()
            return
        if self._job is None:
            self._step()

    def _step(self):
        diff = self._target - self._value
        if abs(diff) < 0.004:
            self._value = self._target
            self._job = None
            self._draw()
            return
        self._value += diff * 0.18
        self._draw()
        try:
            self._job = self.after(16, self._step)
        except tk.TclError:
            self._job = None

    def _draw(self):
        try:
            w, h = self.winfo_width(), self.winfo_height()
        except tk.TclError:
            return
        self.delete("all")
        if w < 4:
            return
        round_rect_item(self, 0, 0, w, h, h / 2, fill=self._track, outline="")
        fill_w = max(0, int(w * self._value))
        if fill_w > 2:
            round_rect_item(self, 0, 0, fill_w, h, h / 2, fill=self._fill, outline="")


class Spinner(tk.Canvas):
    def __init__(self, master, size=16, bg=None, color=YEL):
        base = bg or master.cget("bg")
        super().__init__(master, width=size, height=size, bg=base, bd=0,
                         highlightthickness=0)
        self._size = size
        self._color = color
        self._angle = 0
        self._job = None
        self._running = False
        self._arc = None

    def _draw(self):
        self.delete("all")
        m = 2
        self._arc = self.create_arc(m, m, self._size - m, self._size - m,
                                    start=self._angle, extent=250, style="arc",
                                    outline=self._color, width=2)

    def start(self):
        if self._running:
            return
        self._running = True
        self._tick()

    def stop(self):
        self._running = False
        if self._job:
            try:
                self.after_cancel(self._job)
            except Exception:
                pass
            self._job = None
        self.delete("all")

    def _tick(self):
        if not self._running:
            return
        self._angle = (self._angle + 28) % 360
        self.delete("all")
        m = 2
        self.create_arc(m, m, self._size - m, self._size - m, start=self._angle,
                        extent=250, style="arc", outline=self._color, width=2)
        try:
            self._job = self.after(55, self._tick)
        except tk.TclError:
            self._running = False


class PulseDot(tk.Canvas):
    def __init__(self, master, size=12, bg=None, color=GREEN):
        base = bg or master.cget("bg")
        super().__init__(master, width=size, height=size, bg=base, bd=0,
                         highlightthickness=0)
        self._size = size
        self._color = color
        self._t = 0.0
        self._dir = 1
        self._job = None
        self._tick()

    def _tick(self):
        self.delete("all")
        c = self._size / 2
        r = 2.2 + self._t * 2.1
        self.create_oval(c - r, c - r, c + r, c + r,
                         fill=mix(self._color, "#1A2A20", self._t * 0.55), outline="")
        self._t += 0.06 * self._dir
        if self._t >= 1:
            self._t, self._dir = 1.0, -1
        elif self._t <= 0:
            self._t, self._dir = 0.0, 1
        try:
            self._job = self.after(45, self._tick)
        except tk.TclError:
            self._job = None


# ─────────────────────────────────────────────────────────────
# SCROLL
# ─────────────────────────────────────────────────────────────
class SlimScrollbar(tk.Canvas):
    def __init__(self, master, command, width=9, bg=BG):
        super().__init__(master, width=width, bg=bg, bd=0, highlightthickness=0)
        self.command = command
        self._first, self._last = 0.0, 1.0
        self._drag = None
        self._thumb = None
        self._size = (0, 0)
        self.bind("<Configure>", self._on_configure)
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._motion)
        self.bind("<ButtonRelease-1>", lambda e: setattr(self, "_drag", None))

    def _on_configure(self, event):
        size = (event.width, event.height)
        if size == self._size:
            return
        self._size = size
        self._draw()

    def set(self, first, last):
        try:
            self._first, self._last = float(first), float(last)
        except Exception:
            self._first, self._last = 0.0, 1.0
        self._draw()

    def _geometry(self):
        h = self.winfo_height()
        top = self._first * h
        bottom = self._last * h
        if bottom - top < 32:
            bottom = min(h, top + 32)
            top = max(0, bottom - 32)
        return h, top, bottom

    def _draw(self):
        self.delete("all")
        try:
            w, h = self.winfo_width(), self.winfo_height()
        except tk.TclError:
            return
        if h < 10 or w < 2:
            return
        if (self._last - self._first) >= 0.999:
            return  # todo el contenido visible: no ocupamos lugar visual
        round_rect_item(self, w / 2 - 1.5, 2, w / 2 + 1.5, h - 2, 1.5,
                        fill="#1B232B", outline="")
        _, top, bottom = self._geometry()
        round_rect_item(self, 1, top, w - 1, bottom, (w - 2) / 2,
                        fill="#46525E", outline="")

    def _press(self, event):
        h, top, bottom = self._geometry()
        if top <= event.y <= bottom:
            self._drag = event.y - top
        else:
            self._drag = (bottom - top) / 2
            self._jump(event.y)
            self._motion(event)

    def _motion(self, event):
        if self._drag is None:
            return
        try:
            self.command("moveto", max(0.0, min(1.0, (event.y - self._drag) / max(1, self.winfo_height()))))
        except Exception:
            pass

    def _jump(self, y):
        try:
            self.command("moveto", max(0.0, min(1.0, y / max(1, self.winfo_height()))))
        except Exception:
            pass


class ScrollArea(tk.Frame):
    def __init__(self, master, bg=BG, pad=22):
        super().__init__(master, bg=bg, bd=0, highlightthickness=0)
        self.canvas = tk.Canvas(self, bg=bg, bd=0, highlightthickness=0)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._win = self.canvas.create_window(pad, 18, window=self.inner, anchor="nw")
        self.bar = SlimScrollbar(self, command=self.canvas.yview, bg=bg)
        self.canvas.configure(yscrollcommand=self._on_scroll)
        self.canvas.pack(side="left", fill="both", expand=True)
        # La barra ocupa siempre el mismo ancho: si aparece/desaparece, el
        # contenido cambia de ancho y se generan todos los fondos (parpadeo).
        self.bar.pack(side="right", fill="y", padx=(0, 4))
        self.inner.bind("<Configure>", self._on_inner)
        self.canvas.bind("<Configure>", self._on_canvas)
        self._wheel_px = 0.0
        self._wheel_job = None
        self.bind_all("<MouseWheel>", self._wheel, add="+")

    def _on_inner(self, event=None):
        if event is not None:
            size = (event.width, event.height)
        else:
            size = (self.inner.winfo_width(), self.inner.winfo_height())
        if size == getattr(self, "_inner_size", None):
            return
        self._inner_size = size
        try:
            self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        except tk.TclError:
            pass

    def _on_canvas(self, event):
        size = (event.width, event.height)
        if size == getattr(self, "_canvas_size", None):
            return
        self._canvas_size = size
        try:
            self.canvas.itemconfigure(self._win, width=max(1, event.width - 44))
        except tk.TclError:
            pass
        self._on_inner()

    def _on_scroll(self, first, last):
        self.bar.set(first, last)

    def _wheel(self, event):
        try:
            x, y = self.winfo_pointerxy()
            under = self.winfo_containing(x, y)
        except Exception:
            return
        if under is None:
            return
        if not (under is self or str(under).startswith(str(self))):
            return
        self._wheel_px += float(-event.delta)
        if self._wheel_job is None:
            self._wheel_job = self.after(12, self._apply_wheel)
        return "break"

    def _apply_wheel(self):
        self._wheel_job = None
        px, self._wheel_px = self._wheel_px, 0.0
        if not px:
            return
        try:
            region = self.canvas.bbox("all")
            alto = (region[3] - region[1]) if region else 1
            actual = self.canvas.yview()[0]
            destino = max(0.0, min(1.0, actual + px / max(1.0, float(alto))))
            self.canvas.yview_moveto(destino)
            # Mover las ventanas embebidas antes del siguiente evento:
            # sin esto, Tk acumula eventos y se ve el "desgarro".
            self.canvas.update_idletasks()
        except tk.TclError:
            pass

    def to_bottom(self):
        try:
            self.canvas.yview_moveto(1.0)
            self.canvas.update_idletasks()
        except tk.TclError:
            pass
