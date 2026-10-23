"""
Indie Now · Subtítulos — interfaz (estética IndieDL)
===================================================
Pantalla completa del generador de subtítulos. No sabe nada de Resolve ni de
Whisper: recibe un `backend` con los métodos que necesita.
"""

from __future__ import annotations

import os
import queue
import threading
import time
import tkinter as tk
import traceback
from tkinter import filedialog, messagebox

import indie_ui as iu

# Estados de la tarjeta de estado
ESTADOS = {
    "idle":    dict(color=iu.MUTED, title="EN ESPERA", glyph="·"),
    "working": dict(color=iu.YEL, title="TRABAJANDO", glyph="…"),
    "success": dict(color=iu.GREEN, title="LISTO", glyph="✓"),
    "error":   dict(color=iu.RED, title="ERROR", glyph="!"),
    "warn":    dict(color=iu.AMBER, title="AVISO", glyph="!"),
}

IDIOMAS_AUDIO = [
    ("auto", "AUTOMÁTICO"), ("es", "ESPAÑOL"), ("en", "INGLÉS"),
    ("pt", "PORTUGUÉS"), ("fr", "FRANCÉS"), ("it", "ITALIANO"),
    ("de", "ALEMÁN"),
]
IDIOMAS_DESTINO = [
    ("es", "ESPAÑOL"), ("en", "INGLÉS"), ("pt", "PORTUGUÉS"),
    ("fr", "FRANCÉS"), ("it", "ITALIANO"), ("de", "ALEMÁN"),
]


def _corto(texto, limite=46):
    texto = str(texto or "")
    if len(texto) <= limite:
        return texto
    return texto[:limite - 1].rstrip() + "…"


class StateIcon(tk.Canvas):
    """Círculo de estado que gira mientras se trabaja."""

    def __init__(self, master, bg, size=26):
        super().__init__(master, width=size, height=size, bg=bg, bd=0,
                         highlightthickness=0)
        self._size = size
        self._bg = bg
        self._kind = "idle"
        self._angle = 0
        self._job = None
        self._spin = False
        self.draw("idle")

    def draw(self, kind):
        self._kind = kind
        self._spin = kind == "working"
        if not self._spin and self._job:
            try:
                self.after_cancel(self._job)
            except Exception:
                pass
            self._job = None
        self._render()
        if self._spin and self._job is None:
            self.tick()

    def _render(self):
        self.delete("all")
        size = self._size
        m = 2
        conf = ESTADOS.get(self._kind, ESTADOS["idle"])
        color = conf["color"]
        if self._kind == "working":
            self.create_arc(m, m, size - m, size - m, start=self._angle,
                            extent=250, style="arc", outline=color, width=2)
        else:
            self.create_oval(m + 1, m + 1, size - m - 1, size - m - 1,
                             outline=iu.mix(color, self._bg, 0.55), width=1)
            glyph = conf["glyph"]
            font = (iu.F_BLACK, 12 if glyph != "·" else 14)
            self.create_text(size / 2, size / 2 - 1, text=glyph, fill=color,
                             font=font)

    def tick(self):
        if not self._spin:
            return
        self._angle = (self._angle + 28) % 360
        self._render()
        try:
            self._job = self.after(55, self.tick)
        except tk.TclError:
            self._job = None


class TrackRow(tk.Frame):
    """Fila de pista de audio: casilla + badge A1 + descripción."""

    def __init__(self, master, data, var, on_change):
        super().__init__(master, bg=iu.SURFACE_2, cursor="hand2")
        self.data = data
        self.var = var
        self.on_change = on_change
        self._hover = False
        self._photo = None
        self._enabled = bool(data.get("clips"))
        self.grid_columnconfigure(2, weight=1)

        self.box = tk.Canvas(self, width=20, height=20, bg=iu.SURFACE_2, bd=0,
                             highlightthickness=0, cursor="hand2")
        self.box.grid(row=0, column=0, padx=(12, 0), pady=11)

        self.badge = tk.Label(self, text=str(data.get("label", "A?")), bg=iu.FIELD,
                              fg=iu.YEL, font=(iu.F_BLACK, 9), padx=8, pady=3)
        self.badge.grid(row=0, column=1, padx=(10, 12), pady=9)

        self.name = tk.Label(self, text=_corto(data.get("titulo") or "Sin clips", 58),
                             bg=iu.SURFACE_2,
                             fg=iu.TEXT if self._enabled else iu.DIM,
                             font=(iu.F_SEMI, 10), anchor="w", justify="left")
        self.name.grid(row=0, column=2, sticky="ew", pady=(8, 1))

        self.meta = tk.Label(self, text=data.get("detalle") or "vacía",
                             bg=iu.SURFACE_2, fg=iu.DIM, font=(iu.F_BODY, 8),
                             anchor="w")
        self.meta.grid(row=1, column=2, sticky="ew", pady=(0, 8))

        self.right = tk.Label(self, text=data.get("derecha") or "",
                              bg=iu.SURFACE_2, fg=iu.DIM,
                              font=(iu.F_BLACK, 8), anchor="e")
        self.right.grid(row=0, column=3, rowspan=2, padx=(8, 14))

        for w in (self, self.box, self.badge, self.name, self.meta, self.right):
            w.bind("<Button-1>", self._toggle)
            w.bind("<Enter>", lambda e: self._set_hover(True))
            w.bind("<Leave>", lambda e: self._set_hover(False))
        try:
            self.var.trace_add("write", lambda *_: self._paint())
        except Exception:
            pass
        self._paint()

    def _set_hover(self, on):
        if not self._enabled:
            return
        self._hover = on
        bg = "#1F2831" if on else iu.SURFACE_2
        for w in (self, self.box, self.name, self.meta, self.right):
            try:
                w.configure(bg=bg)
            except tk.TclError:
                pass
        self._paint()

    def _toggle(self, _=None):
        if not self._enabled:
            return
        self.var.set(not bool(self.var.get()))
        if self.on_change:
            self.on_change()

    def _paint(self):
        checked = bool(self.var.get())
        self._photo = iu.check_photo(20, checked, self._hover, not self._enabled)
        self.box.delete("all")
        if self._photo is not None:
            self.box.create_image(0, 0, anchor="nw", image=self._photo)
        else:
            self.box.create_rectangle(1, 1, 19, 19, outline=iu.YEL,
                                      width=2, fill=iu.YEL if checked else "")


class SubtitulosApp:
    def __init__(self, root, backend):
        self.root = root
        self.backend = backend
        self.cfg = dict(backend.config() or {})
        self.pistas_sel = set()
        self.track_vars = {}
        self.track_rows = []
        self._signature_pistas = None
        self._busy = False
        self._job_header = None
        self._job_watch = None
        self._pulse = None
        self.timeline = None
        self.tpls = []
        self._pistas = []
        self._cola_obj = queue.Queue()
        self._cod_idioma = {eti: code for code, eti in IDIOMAS_AUDIO}
        self._lab_idioma = {code: eti for code, eti in IDIOMAS_AUDIO}
        self._cod_destino = {eti: code for code, eti in IDIOMAS_DESTINO}
        self._lab_destino = {code: eti for code, eti in IDIOMAS_DESTINO}

        self.v_guion = tk.StringVar(value=self.cfg.get("guion", "") or "")
        self.v_srt = tk.StringVar(value=self.cfg.get("srt", "") or "")
        self.v_rango = tk.StringVar(value=self.cfg.get("rango", "inout"))
        self.v_tpl = tk.StringVar(value=self.cfg.get("plantilla", "") or "")
        self.v_subs = tk.BooleanVar(value=bool(self.cfg.get("subs", True)))
        self.v_txt = tk.BooleanVar(value=bool(self.cfg.get("txt", False)))
        self.v_comp = tk.BooleanVar(value=bool(self.cfg.get("comparar", False)))
        self.v_solo = tk.BooleanVar(value=bool(self.cfg.get("solo_srt", False)))
        self.v_idioma = tk.StringVar(value=self._lab_idioma.get(
            str(self.cfg.get("idioma", "auto")), "AUTOMÁTICO"))
        self.v_trad = tk.BooleanVar(value=bool(self.cfg.get("traducir", False)))
        self.v_destino = tk.StringVar(value=self._lab_destino.get(
            str(self.cfg.get("idioma_destino", "es")), "ESPAÑOL"))
        self.v_estilo = tk.StringVar(
            value="PALABRAS" if self.cfg.get("estilo_trad", "palabras") == "palabras"
            else "FRASES")
        if self.v_solo.get():
            self.v_subs.set(False)
            self.v_txt.set(False)
            self.v_comp.set(False)

        pistas_cfg = self.cfg.get("pistas")
        if isinstance(pistas_cfg, (list, tuple)):
            self.pistas_sel = {int(p) for p in pistas_cfg if str(p).isdigit()}
        elif self.cfg.get("pista"):
            try:
                self.pistas_sel = {int(str(self.cfg["pista"]).lstrip("Aa"))}
            except Exception:
                self.pistas_sel = set()

        self._config_root()
        self._build_header()
        self._build_footer()
        self._build_scroll()
        self._build_timeline()
        self._build_actions()
        self._build_audio()
        self._build_guion()
        self._build_salida()
        self._build_plantilla()
        self._build_traduccion()
        self._build_estado()
        self._build_log()

        self._set_estado("idle", "Leyendo la timeline de Resolve…")
        self._fade_in()
        self.root.after(40, self._arranque)

    def _arranque(self):
        """Primer lectura de Resolve, después de que la ventana ya se dibujó."""
        self._refresh_timeline(force=True)
        self._sync_modo()
        if self.timeline:
            self._set_estado(
                "idle", f"Listo. Se va a trabajar sobre «{self.timeline['nombre']}».")
        else:
            self._set_estado("idle", "Listo. Abrí una timeline en Resolve.")
        self._log("Aplicación iniciada · estética IndieDL", "info")
        self._poll()
        self._watch()

    # ─────────────────────────────────────────────────────────
    # VENTANA / CABECERA / PIE
    # ─────────────────────────────────────────────────────────
    def _config_root(self):
        r = self.root
        r.title("Indie Now · Subtítulos")
        r.configure(bg=iu.BG)
        r.minsize(900, 620)
        ancho, alto = 1180, 820
        try:
            sw, sh = r.winfo_screenwidth(), r.winfo_screenheight()
            ancho = min(ancho, sw - 80)
            alto = min(alto, sh - 90)
            x = max(0, (sw - ancho) // 2)
            y = max(0, (sh - alto) // 2 - 10)
            r.geometry(f"{ancho}x{alto}+{x}+{y}")
        except tk.TclError:
            r.geometry("1180x820")
        iu.apply_window_icon(r)
        try:
            r.attributes("-topmost", True)
            r.after(700, lambda: r.attributes("-topmost", False))
        except tk.TclError:
            pass

    def _build_header(self):
        self.header = tk.Frame(self.root, bg=iu.BG)
        self.header.pack(side="top", fill="x")
        self.banner = tk.Label(self.header, bg=iu.BG, bd=0, highlightthickness=0)
        self.banner.pack(fill="x")
        band = tk.Frame(self.header, bg=iu.BLACK, height=40)
        band.pack(fill="x")
        band.pack_propagate(False)
        tk.Label(band, text="SUBTÍTULOS   ·   TRANSCRIPCIÓN   ·   DAVINCI RESOLVE",
                 bg=iu.BLACK, fg=iu.YEL, font=(iu.F_BLACK, 11)).pack(expand=True)
        tk.Frame(self.header, bg=iu.YEL, height=4).pack(fill="x")
        self._draw_banner(max(320, self.root.winfo_width()))
        self.root.bind("<Configure>", self._on_root_resize, add="+")

    def _on_root_resize(self, event=None):
        if event is not None and event.widget is not self.root:
            return
        if self._job_header:
            try:
                self.root.after_cancel(self._job_header)
            except Exception:
                pass
        self._job_header = self.root.after(140, lambda: self._draw_banner(
            max(320, self.root.winfo_width())))

    def _draw_banner(self, width):
        self._job_header = None
        try:
            photo = iu.banner_photo(width, 104)
        except Exception:
            photo = None
        if photo is None:
            return
        self.banner.configure(image=photo)
        self.banner._photo = photo

    def _build_footer(self):
        self.footer = tk.Frame(self.root, bg="#0B1015")
        self.footer.pack(side="bottom", fill="x")
        tk.Frame(self.footer, bg=iu.BORDER, height=1).pack(fill="x")
        self.meter = iu.Meter(self.footer, height=6, bg="#0B1015")
        self.meter.pack(fill="x", padx=0)
        row = tk.Frame(self.footer, bg="#0B1015", padx=22, pady=12)
        row.pack(fill="x")
        self.footer_txt = tk.Label(row, text="En espera", bg="#0B1015", fg=iu.DIM,
                                   font=(iu.F_BODY, 9), anchor="w", justify="left")
        self.footer_txt.pack(side="left", fill="x", expand=True)
        row.bind("<Configure>", lambda e: self.footer_txt.configure(
            wraplength=max(160, e.width - 360)), add="+")
        self.cta = iu.BrandButton(row, text="GENERAR SUBTÍTULOS", kind="primary",
                                  height=54, font=(iu.F_BLACK, 15), radius=11,
                                  icon="▶", command=self._go)
        self.cta.pack(side="right", padx=(16, 0))
        self.cta.configure(width=300)

    # ─────────────────────────────────────────────────────────
    # CUERPO
    # ─────────────────────────────────────────────────────────
    def _build_scroll(self):
        self.scroll = iu.ScrollArea(self.root, bg=iu.BG, pad=22)
        self.scroll.pack(fill="both", expand=True)
        self.content = self.scroll.inner
        self.content.grid_columnconfigure(0, weight=1, uniform="layout_columns")
        self.content.grid_columnconfigure(1, weight=1, uniform="layout_columns")
        self.host_timeline = tk.Frame(self.content, bg=iu.BG)
        self.host_timeline.grid(row=0, column=0, columnspan=2, sticky="ew")
        self.col_left = tk.Frame(self.content, bg=iu.BG)
        self.col_left.grid(row=1, column=0, sticky="new", padx=(0, 7))
        self.col_right = tk.Frame(self.content, bg=iu.BG)
        self.col_right.grid(row=1, column=1, sticky="new", padx=(7, 0))
        self.host_state = tk.Frame(self.content, bg=iu.BG)
        self.host_state.grid(row=2, column=0, columnspan=2, sticky="ew")
        self.host_log = tk.Frame(self.content, bg=iu.BG)
        self.host_log.grid(row=3, column=0, columnspan=2, sticky="ew")

    def _card(self, parent, pady=(0, 12)):
        card = iu.Rounded(parent)
        card.pack(fill="x", pady=pady)
        return card

    def _section(self, parent, numero, titulo, hint="", pady=(0, 12)):
        card = iu.Rounded(parent)
        card.pack(fill="x", pady=pady)
        body = card.body
        head = tk.Frame(body, bg=iu.SURFACE)
        head.pack(fill="x", pady=(0, 10))
        badge = tk.Canvas(head, width=26, height=26, bg=iu.SURFACE, bd=0,
                          highlightthickness=0)
        if numero is None:
            badge.create_rectangle(11, 4, 15, 22, fill=iu.YEL, outline="")
        else:
            badge.create_oval(0, 0, 25, 25, fill=iu.YEL, outline="")
            badge.create_text(13, 12, text=str(numero), fill=iu.BLACK,
                              font=(iu.F_BLACK, 11))
        badge.pack(side="left")
        tk.Label(head, text=titulo, bg=iu.SURFACE, fg=iu.TEXT,
                 font=(iu.F_BLACK, 12)).pack(side="left", padx=(10, 0))
        hint_lbl = tk.Label(head, text=hint, bg=iu.SURFACE, fg=iu.DIM,
                            font=(iu.F_BLACK, 8))
        hint_lbl.pack(side="right", pady=(4, 0))
        return card, body, hint_lbl

    def _build_timeline(self):
        card = self._card(self.host_timeline, pady=(0, 12))
        body = card.body
        top = tk.Frame(body, bg=iu.SURFACE)
        top.pack(fill="x")
        left = tk.Frame(top, bg=iu.SURFACE)
        left.pack(side="left", fill="x", expand=True)
        tk.Label(left, text="TRABAJANDO EN ESTA TIMELINE", bg=iu.SURFACE,
                 fg=iu.DIM, font=(iu.F_BLACK, 8)).pack(anchor="w")
        self.lbl_tl = tk.Label(left, text="—", bg=iu.SURFACE, fg=iu.TEXT,
                               font=(iu.F_BLACK, 19), anchor="w", justify="left")
        self.lbl_tl.pack(anchor="w", pady=(2, 0))
        self.lbl_tl_sub = tk.Label(left, text="", bg=iu.SURFACE, fg=iu.MUTED,
                                   font=(iu.F_BODY, 9), anchor="w")
        self.lbl_tl_sub.pack(anchor="w")
        right = tk.Frame(top, bg=iu.SURFACE)
        right.pack(side="right", anchor="ne")
        self.dot = iu.PulseDot(right, bg=iu.SURFACE)
        self.dot.pack(side="left", padx=(0, 6))
        self.lbl_live = tk.Label(right, text="EN VIVO", bg=iu.SURFACE,
                                 fg=iu.GREEN, font=(iu.F_BLACK, 8))
        self.lbl_live.pack(side="left")

    # ── 1. ACCIONES ──────────────────────────────────────────
    def _build_actions(self):
        card, body, hint = self._section(self.col_left, 1, "QUÉ QUERÉS HACER", "MODO")
        self.hint_acciones = hint
        self.chk_subs = iu.BrandCheck(body, "Generar subtítulos (SRT + plantilla)",
                                      self.v_subs, command=self._modo_changed)
        self.chk_subs.pack(fill="x", pady=(0, 6))
        self.chk_txt = iu.BrandCheck(body, "Transcribir el audio a un archivo de texto (.txt)",
                                     self.v_txt, command=self._modo_changed)
        self.chk_txt.pack(fill="x", pady=6)
        self.chk_comp = iu.BrandCheck(body, "Comparar con el guion (marcadores + informe)",
                                      self.v_comp, command=self._modo_changed)
        self.chk_comp.pack(fill="x", pady=6)
        self.chk_solo = iu.BrandCheck(body, "Generar solo SRT (sin plantilla ni añadirlo a la timeline)",
                                      self.v_solo, command=self._solo_changed)
        self.chk_solo.pack(fill="x", pady=(6, 0))

    # ── 2. AUDIO ─────────────────────────────────────────────
    def _build_audio(self):
        card, body, hint = self._section(self.col_left, 2, "AUDIO Y RANGO", "PISTAS")
        self.hint_audio = hint
        top = tk.Frame(body, bg=iu.SURFACE)
        top.pack(fill="x")
        tk.Label(top, text="Pistas de audio", bg=iu.SURFACE, fg=iu.MUTED,
                 font=(iu.F_SEMI, 9)).pack(side="left")
        self.btn_todas = iu.BrandButton(top, text="TODAS", kind="chip", height=26,
                                        font=(iu.F_BLACK, 8), width=64, radius=7,
                                        command=lambda: self._marcar_pistas(True))
        self.btn_todas.pack(side="right")
        self.btn_ninguna = iu.BrandButton(top, text="NINGUNA", kind="chip", height=26,
                                          font=(iu.F_BLACK, 8), width=76, radius=7,
                                          command=lambda: self._marcar_pistas(False))
        self.btn_ninguna.pack(side="right", padx=(0, 6))

        panel = iu.Rounded(body, fill=iu.SURFACE_2, border=iu.BORDER_SOFT,
                           radius=10, pad=6)
        panel.pack(fill="x", pady=(8, 8))
        self.tracks_box = panel.body
        self.lbl_sin_pistas = tk.Label(self.tracks_box, text="Buscando audio en la timeline…",
                                       bg=iu.SURFACE_2, fg=iu.DIM,
                                       font=(iu.F_BODY, 9))
        self.lbl_sin_pistas.pack(pady=10)

        self.lbl_audio_note = tk.Label(body, text="", bg=iu.SURFACE, fg=iu.DIM,
                                       font=(iu.F_BODY, 8), anchor="w",
                                       justify="left")
        self.lbl_audio_note.pack(fill="x")
        iu.autowrap(self.lbl_audio_note, extra=10)

        row_lang = tk.Frame(body, bg=iu.SURFACE)
        row_lang.pack(fill="x", pady=(10, 0))
        tk.Label(row_lang, text="Idioma del audio", bg=iu.SURFACE, fg=iu.MUTED,
                 font=(iu.F_SEMI, 9)).pack(side="left")
        self.sel_idioma = iu.BrandSelect(
            row_lang, values=[eti for _, eti in IDIOMAS_AUDIO],
            variable=self.v_idioma, command=self._guardar, height=36)
        self.sel_idioma.pack(side="right", fill="x", expand=True, padx=(12, 0))

        sep = tk.Frame(body, bg=iu.BORDER_SOFT, height=1)
        sep.pack(fill="x", pady=(10, 10))
        tk.Label(body, text="Alcance", bg=iu.SURFACE, fg=iu.MUTED,
                 font=(iu.F_SEMI, 9)).pack(anchor="w", pady=(0, 6))
        self.seg_rango = iu.Segmented(
            body,
            [("inout", "SOLO IN / OUT"), ("full", "TODA LA LÍNEA")],
            self.v_rango, command=self._guardar, height=38,
            font=(iu.F_BLACK, 9))
        self.seg_rango.pack(fill="x")

    # ── 3. GUION ─────────────────────────────────────────────
    def _build_guion(self):
        card, body, hint = self._section(self.col_right, 3, "GUION", "OPCIONAL")
        self.hint_guion = hint
        row = tk.Frame(body, bg=iu.SURFACE)
        row.pack(fill="x")
        info = tk.Frame(row, bg=iu.SURFACE)
        info.pack(side="left", fill="x", expand=True)
        self.lbl_guion = tk.Label(info, text="Sin guion", bg=iu.SURFACE,
                                  fg=iu.TEXT, font=(iu.F_SEMI, 11), anchor="w",
                                  justify="left")
        self.lbl_guion.pack(fill="x")
        self.lbl_guion_dir = tk.Label(
            info, text="Sin guion: se transcribe el audio tal cual (ideal para videos sueltos).",
            bg=iu.SURFACE, fg=iu.DIM, font=(iu.F_BODY, 8),
            anchor="w", justify="left")
        self.lbl_guion_dir.pack(fill="x")
        iu.autowrap(self.lbl_guion_dir, extra=180)
        self.btn_quitar = iu.BrandButton(row, text="QUITAR", kind="chip", height=32,
                                         font=(iu.F_BLACK, 8), width=76, radius=8,
                                         command=self._limpiar_guion)
        self.btn_quitar.pack(side="right", padx=(8, 0))
        self.btn_guion = iu.BrandButton(row, text="ELEGIR GUION…", kind="dark",
                                        height=36, font=(iu.F_BLACK, 9), width=170,
                                        radius=8, icon="▤", command=self._elegir_guion)
        self.btn_guion.pack(side="right")

    # ── 4. SALIDA ────────────────────────────────────────────
    def _build_salida(self):
        card, body, hint = self._section(self.col_right, 4, "DÓNDE GUARDAR", "SRT + TXT")
        row = tk.Frame(body, bg=iu.SURFACE)
        row.pack(fill="x")
        info = tk.Frame(row, bg=iu.SURFACE)
        info.pack(side="left", fill="x", expand=True)
        self.lbl_srt = tk.Label(info, text="Se guarda junto a la timeline",
                                bg=iu.SURFACE, fg=iu.TEXT, font=(iu.F_SEMI, 11),
                                anchor="w", justify="left")
        self.lbl_srt.pack(fill="x")
        self.lbl_srt_dir = tk.Label(info, text="Podés cambiarlo cuando quieras.",
                                    bg=iu.SURFACE, fg=iu.DIM, font=(iu.F_BODY, 8),
                                    anchor="w", justify="left")
        self.lbl_srt_dir.pack(fill="x")
        iu.autowrap(self.lbl_srt_dir, extra=180)
        self.btn_abrir = iu.BrandButton(row, text="ABRIR", kind="chip", height=32,
                                        font=(iu.F_BLACK, 8), width=70, radius=8,
                                        command=self._abrir_carpeta)
        self.btn_abrir.pack(side="right", padx=(8, 0))
        self.btn_srt = iu.BrandButton(row, text="CAMBIAR DESTINO…", kind="dark",
                                      height=36, font=(iu.F_BLACK, 9), width=180,
                                      radius=8, icon="▣", command=self._elegir_srt)
        self.btn_srt.pack(side="right")

    # ── 5. PLANTILLA ─────────────────────────────────────────
    def _build_plantilla(self):
        card, body, hint = self._section(self.col_right, 5, "PLANTILLA", "SNAP CAPTIONS")
        self.hint_tpl = hint
        row = tk.Frame(body, bg=iu.SURFACE)
        row.pack(fill="x")
        self.sel_tpl = iu.BrandSelect(row, values=[], variable=self.v_tpl,
                                      command=self._guardar, height=42)
        self.sel_tpl.pack(side="left", fill="x", expand=True)
        self.btn_tpl = iu.BrandButton(row, text="ACTUALIZAR", kind="chip", height=42,
                                      font=(iu.F_BLACK, 8), width=112, radius=9,
                                      icon="↻", command=self._refrescar_plantillas)
        self.btn_tpl.pack(side="right", padx=(8, 0))
        self.lbl_tpl_note = tk.Label(body, text="", bg=iu.SURFACE, fg=iu.DIM,
                                     font=(iu.F_BODY, 8), anchor="w", justify="left")
        self.lbl_tpl_note.pack(fill="x", pady=(8, 0))
        iu.autowrap(self.lbl_tpl_note, extra=10)

    # ── 6. TRADUCCIÓN ────────────────────────────────────────
    def _build_traduccion(self):
        card, body, hint = self._section(self.col_right, 6, "TRADUCCIÓN", "GOOGLE")
        self.hint_trad = hint
        self.chk_trad = iu.BrandCheck(
            body, "Traducir los subtítulos a otro idioma", self.v_trad,
            command=self._trad_changed)
        self.chk_trad.pack(fill="x")

        row = tk.Frame(body, bg=iu.SURFACE)
        row.pack(fill="x", pady=(8, 0))
        tk.Label(row, text="Idioma destino", bg=iu.SURFACE, fg=iu.MUTED,
                 font=(iu.F_SEMI, 9)).pack(side="left")
        self.sel_destino = iu.BrandSelect(
            row, values=[eti for _, eti in IDIOMAS_DESTINO],
            variable=self.v_destino, command=self._guardar, height=36)
        self.sel_destino.pack(side="right", fill="x", expand=True, padx=(12, 0))

        tk.Label(body, text="Cómo mostrar la pista traducida", bg=iu.SURFACE,
                 fg=iu.MUTED, font=(iu.F_SEMI, 9)).pack(anchor="w", pady=(10, 6))
        self.seg_estilo = iu.Segmented(
            body,
            [("PALABRAS", "PALABRAS"), ("FRASES", "FRASES")],
            self.v_estilo, command=self._guardar, height=34,
            font=(iu.F_BLACK, 8))
        self.seg_estilo.pack(fill="x")

        self.lbl_trad_note = tk.Label(body, text="", bg=iu.SURFACE, fg=iu.DIM,
                                      font=(iu.F_BODY, 8), anchor="w",
                                      justify="left")
        self.lbl_trad_note.pack(fill="x", pady=(8, 0))
        iu.autowrap(self.lbl_trad_note, extra=10)

    # ── ESTADO ───────────────────────────────────────────────
    def _build_estado(self):
        card = self._card(self.host_state, pady=(0, 12))
        body = card.body
        row = tk.Frame(body, bg=iu.SURFACE)
        row.pack(fill="x")
        self.stripe_estado = tk.Frame(row, bg=iu.MUTED, width=3)
        self.stripe_estado.pack(side="left", fill="y", padx=(0, 12))
        content = tk.Frame(row, bg=iu.SURFACE)
        content.pack(side="left", fill="both", expand=True)
        self.icono_estado = StateIcon(content, bg=iu.SURFACE)
        self.icono_estado.pack(side="left", anchor="n", padx=(0, 10), pady=1)
        text = tk.Frame(content, bg=iu.SURFACE)
        text.pack(side="left", fill="x", expand=True)
        self.lbl_estado_titulo = tk.Label(text, text="EN ESPERA", bg=iu.SURFACE,
                                          fg=iu.MUTED, font=(iu.F_BLACK, 9),
                                          anchor="w")
        self.lbl_estado_titulo.pack(fill="x")
        self.lbl_estado = tk.Label(text, text="", bg=iu.SURFACE, fg=iu.TEXT,
                                   font=(iu.F_BODY, 9), anchor="w", justify="left")
        self.lbl_estado.pack(fill="x", pady=(2, 0))
        iu.autowrap(self.lbl_estado, extra=60)

    # ── LOG ──────────────────────────────────────────────────
    def _build_log(self):
        card, body, hint = self._section(self.host_log, None, "REGISTRO EN TIEMPO REAL", "LOG")
        self.hint_log = hint
        top = tk.Frame(body, bg=iu.SURFACE)
        top.pack(fill="x", pady=(0, 6))
        self.lbl_log_hint = tk.Label(top, text="Todo lo que hace la app, paso a paso.",
                                     bg=iu.SURFACE, fg=iu.DIM, font=(iu.F_BODY, 8))
        self.lbl_log_hint.pack(side="left")
        self.btn_log_toggle = iu.BrandButton(top, text="VER REGISTRO", kind="chip", height=26,
                                      font=(iu.F_BLACK, 8), width=116, radius=7,
                                      command=self._toggle_log)
        self.btn_log_toggle.pack(side="right", padx=(6, 0))
        self.btn_log = iu.BrandButton(top, text="LIMPIAR", kind="chip", height=26,
                                      font=(iu.F_BLACK, 8), width=76, radius=7,
                                      command=self._limpiar_log)
        self.btn_log.pack(side="right")
        console = tk.Frame(body, bg=iu.BG_DEEP)
        self.log_console = console
        self.log = tk.Text(console, height=7, wrap="word", state="disabled",
                           font=(iu.F_MONO, 8), relief="flat", borderwidth=0,
                           bg=iu.BG_DEEP, fg="#C7D2DD", insertbackground="#FFFFFF",
                           padx=12, pady=10, selectbackground="#2B333C",
                           highlightthickness=1, highlightbackground=iu.BORDER_SOFT)
        self.log.pack(side="left", fill="both", expand=True)
        self.log_bar = iu.SlimScrollbar(console, command=self.log.yview, bg=iu.BG_DEEP)
        self.log_bar.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=self._log_scroll)
        self.log.tag_configure("ts", foreground=iu.DIM)
        self.log.tag_configure("info", foreground="#C7D2DD")
        self.log.tag_configure("ok", foreground=iu.GREEN)
        self.log.tag_configure("err", foreground=iu.RED)
        self.log.tag_configure("cmd", foreground=iu.YEL)
        self._log_counter = 0
        self._log_open = False

    def _toggle_log(self):
        self._log_open = not self._log_open
        if self._log_open:
            self.log_console.pack(fill="x")
            self.btn_log_toggle.set_text("OCULTAR REGISTRO")
        else:
            self.log_console.pack_forget()
            self.btn_log_toggle.set_text("VER REGISTRO")

    # ─────────────────────────────────────────────────────────
    # ESTADO / CONFIG
    # ─────────────────────────────────────────────────────────
    def _set_estado(self, kind, mensaje, titulo=None):
        conf = ESTADOS.get(kind, ESTADOS["idle"])
        self.icono_estado.draw(kind)
        self.lbl_estado_titulo.configure(
            text=titulo or conf["title"], fg=conf["color"])
        self.lbl_estado.configure(text=mensaje or "")
        try:
            self.stripe_estado.configure(bg=conf["color"])
        except tk.TclError:
            pass
        try:
            self.footer_txt.configure(text=_corto((mensaje or "").splitlines()[0], 140))
        except tk.TclError:
            pass

    def _log(self, mensaje, kind="info"):
        """Consola con hora + color. kind: info|ok|err|cmd."""
        if not hasattr(self, "log"):
            return
        texto = str(mensaje).strip()
        if kind == "info" and texto == getattr(self, "_ultimo_log", None):
            return
        self._ultimo_log = texto if kind == "info" else None
        try:
            self.log.configure(state="normal")
            self.log.insert("end", time.strftime("%H:%M:%S  "), "ts")
            self.log.insert("end", texto + "\n", kind)
            self.log.see("end")
            self.log.configure(state="disabled")
            self._log_counter += 1
            if self._log_counter > 500:
                self.log.configure(state="normal")
                self.log.delete("1.0", "100.0")
                self.log.configure(state="disabled")
                self._log_counter -= 50
        except tk.TclError:
            pass

    def _log_scroll(self, first, last):
        # La barra ocupa siempre el mismo ancho (si aparece/desaparece, el
        # Text cambia de ancho y parpadea al hacer scroll).
        self.log_bar.set(first, last)

    def _limpiar_log(self):
        try:
            self.log.configure(state="normal")
            self.log.delete("1.0", "end")
            self.log.configure(state="disabled")
            self._ultimo_log = None
        except tk.TclError:
            pass

    def _guardar(self, *_):
        cfg = {
            "guion": self.v_guion.get(),
            "srt": self.v_srt.get(),
            "plantilla": self.v_tpl.get(),
            "pistas": sorted(self.pistas_sel),
            "rango": self.v_rango.get(),
            "subs": self.v_subs.get(),
            "txt": self.v_txt.get(),
            "comparar": self.v_comp.get(),
            "solo_srt": self.v_solo.get(),
            "idioma": self._cod_idioma.get(self.v_idioma.get(), "auto"),
            "traducir": self.v_trad.get(),
            "idioma_destino": self._cod_destino.get(self.v_destino.get(), "es"),
            "estilo_trad": ("palabras" if self.v_estilo.get() == "PALABRAS"
                            else "frases"),
        }
        try:
            self.backend.guardar(cfg)
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────
    # TIMELINE / PISTAS / PLANTILLAS
    # ─────────────────────────────────────────────────────────
    def _refresh_timeline(self, force=False):
        info = None
        try:
            info = self.backend.info_timeline()
        except Exception as exc:
            self._log(f"No pude leer la timeline: {exc}", "err")
        anterior = (self.timeline or {}).get("nombre")
        self.timeline = info
        if info:
            self.lbl_tl.configure(text=_corto(info.get("nombre", "—"), 40), fg=iu.TEXT)
            self.lbl_tl_sub.configure(
                text=f"Proyecto: {info.get('proyecto', '—')}   ·   {info.get('fps', 0):g} fps")
            self.lbl_live.configure(text="EN VIVO", fg=iu.GREEN)
        else:
            self.lbl_tl.configure(text="Sin timeline abierta", fg=iu.RED)
            self.lbl_tl_sub.configure(text="Abrí una timeline en Resolve; se detecta sola.")
            self.lbl_live.configure(text="SIN SEÑAL", fg=iu.RED)
        cambio = force or (anterior != (info or {}).get("nombre"))
        self._refresh_pistas(reiniciar=cambio)
        if cambio:
            self._refrescar_plantillas(silencioso=True)

    def _refresh_pistas(self, reiniciar=False):
        try:
            pistas = list(self.backend.pistas() or [])
        except Exception as exc:
            self._log(f"No pude leer las pistas de audio: {exc}", "err")
            pistas = []
        self._pistas = pistas
        firma = tuple((p.get("n"), p.get("clips"), p.get("titulo")) for p in pistas)
        validas = {p["n"] for p in pistas if p.get("clips")}
        self.pistas_sel = {p for p in self.pistas_sel if p in validas}
        if reiniciar and validas and not self.pistas_sel:
            self.pistas_sel = {min(validas)}
        if (not reiniciar and firma == self._signature_pistas
                and len(self.track_rows) == len(pistas)):
            for n, var in self.track_vars.items():
                if bool(var.get()) != (n in self.pistas_sel):
                    var.set(n in self.pistas_sel)
            self._audio_resumen()
            return
        self._signature_pistas = firma
        self.track_vars.clear()
        for row in self.track_rows:
            row.destroy()
        self.track_rows = []
        for child in self.tracks_box.winfo_children():
            child.destroy()
        if not pistas:
            tk.Label(self.tracks_box, text="No hay pistas de audio en esta timeline.",
                     bg=iu.SURFACE_2, fg=iu.DIM, font=(iu.F_BODY, 9)).pack(pady=10)
            self._audio_resumen()
            return
        for i, data in enumerate(pistas):
            var = tk.BooleanVar(value=data["n"] in self.pistas_sel)
            self.track_vars[data["n"]] = var
            row = TrackRow(self.tracks_box, data, var, self._audio_changed)
            row.pack(fill="x", padx=2, pady=1)
            self.track_rows.append(row)
            if i < len(pistas) - 1:
                tk.Frame(self.tracks_box, bg=iu.BORDER_SOFT, height=1).pack(
                    fill="x", padx=12)
        self._audio_resumen()

    def _audio_changed(self):
        self.pistas_sel = {n for n, v in self.track_vars.items() if v.get()}
        self._audio_resumen()
        self._guardar()

    def _marcar_pistas(self, on):
        for n, var in self.track_vars.items():
            row = next((r for r in self.track_rows if r.data["n"] == n), None)
            if on and not (row and row._enabled):
                continue
            var.set(on)
        self._audio_changed()

    def _audio_resumen(self):
        pistas = getattr(self, "_pistas", [])
        if not pistas:
            self.lbl_audio_note.configure(text="Sin pistas de audio disponibles.")
            self.hint_audio.configure(text="SIN AUDIO")
            return
        elegidas = [d for d in pistas if d["n"] in self.pistas_sel]
        self.hint_audio.configure(
            text=f"{len(elegidas)} DE {len(pistas)}" if elegidas else "NINGUNA")
        if not elegidas:
            self.lbl_audio_note.configure(
                text="Elegí al menos una pista para transcribir.")
        elif len(elegidas) == 1:
            self.lbl_audio_note.configure(
                text=f"{elegidas[0]['label']} · «{elegidas[0]['titulo']}»")
        else:
            labels = " + ".join(d["label"] for d in elegidas)
            self.lbl_audio_note.configure(
                text=f"{labels} · se mezclan en un solo audio antes de transcribir.")

    def _refrescar_plantillas(self, silencioso=False):
        try:
            valores = list(self.backend.plantillas() or [])
        except Exception as exc:
            self._log(f"No pude leer las plantillas: {exc}", "err")
            valores = []
        self.tpls = valores
        actual = self.v_tpl.get()
        self.sel_tpl.set_values(valores)
        if valores and actual not in valores:
            self.v_tpl.set(valores[0])
        if not silencioso:
            self._log(f"{max(0, len(valores) - 1)} plantillas de Snap Captions.", "info")
            self._set_estado("idle", f"{max(0, len(valores) - 1)} plantillas disponibles.")

    # ─────────────────────────────────────────────────────────
    # MODOS
    # ─────────────────────────────────────────────────────────
    def _solo_changed(self):
        if self.v_solo.get():
            self.v_subs.set(False)
            self.v_txt.set(False)
            self.v_comp.set(False)
        self._sync_modo()

    def _modo_changed(self):
        if self.v_subs.get() or self.v_txt.get() or self.v_comp.get():
            self.v_solo.set(False)
        self._sync_modo()

    def _sync_modo(self):
        solo = self.v_solo.get()
        subs = self.v_subs.get()
        comp = self.v_comp.get()
        usa_plantilla = subs and not solo
        self.sel_tpl.set_enabled(usa_plantilla)
        self.btn_tpl.set_enabled(usa_plantilla)
        self.hint_tpl.configure(text="SNAP CAPTIONS" if usa_plantilla else "NO SE USA")
        if self.v_solo.get():
            modo = "SOLO SRT"
        elif comp:
            modo = "REVISIÓN"
        elif subs:
            modo = "SUBTÍTULOS"
        elif self.v_txt.get():
            modo = "TRANSCRIPCIÓN"
        else:
            modo = "SIN MODO"
        self.hint_acciones.configure(text=modo)
        self.hint_guion.configure(text="NECESARIO" if comp else "OPCIONAL")
        self.lbl_tpl_note.configure(
            text=("Se aplica una plantilla del Media Pool a cada subtítulo."
                  if usa_plantilla else
                  "Solo se usa cuando generás subtítulos con plantilla."))
        self._sync_trad()
        self.cta.set_text(self._texto_boton())
        self._actualizar_rutas()
        self._guardar()

    def _trad_changed(self):
        self._sync_trad()
        self._guardar()

    def _sync_trad(self):
        puede = self.v_subs.get() or self.v_solo.get()
        activo = puede and self.v_trad.get()
        self.chk_trad.set_enabled(puede)
        self.sel_destino.set_enabled(activo)
        self.seg_estilo.set_enabled(activo)
        self.hint_trad.configure(text="GOOGLE" if puede else "SOLO SUBS")
        if not puede:
            nota = "Solo disponible cuando generás subtítulos."
        elif not self.v_trad.get():
            nota = ("Activá para crear un SRT traducido y su propia pista. "
                    "El original no se toca.")
        else:
            nota = ("Whisper transcribe el audio y Google traduce esos subtítulos "
                    "por frases, en bloques cortos. El original no se toca. "
                    "Requiere internet.")
            if self.v_guion.get():
                nota += (" El guion es opcional y solo corrige nombres: tiene que "
                         "estar en el idioma del audio.")
            else:
                nota += " Sin guion: perfecto para videos sueltos."
        con_plantilla = bool(self.v_subs.get() and not self.v_solo.get()
                             and self.tpls and self.v_tpl.get()
                             and self.v_tpl.get() != self.tpls[0])
        if activo and con_plantilla:
            nota += (" Se crean dos pistas de video (original y traducción): "
                     "si ves las dos, desactivá la que no quieras.")
            self.lbl_tpl_note.configure(
                text="La misma plantilla se aplica a la pista original y a la "
                     "traducida (dos pistas de video).")
        self.lbl_trad_note.configure(text=nota)

    def _texto_boton(self):
        if self._busy:
            return "PROCESANDO…"
        if self.v_solo.get():
            return "GENERAR SOLO SRT"
        s, t, c = self.v_subs.get(), self.v_txt.get(), self.v_comp.get()
        if s and not t and not c:
            return "GENERAR SUBTÍTULOS"
        if t and not s and not c:
            return "TRANSCRIBIR AUDIO"
        if c and not s and not t:
            return "COMPARAR CON EL GUION"
        return "EJECUTAR"

    def _actualizar_rutas(self):
        guion = self.v_guion.get()
        if guion:
            base = os.path.basename(guion)
            self.lbl_guion.configure(text=_corto(base, 52), fg=iu.TEXT)
            self.lbl_guion_dir.configure(text=_corto(os.path.dirname(guion), 74),
                                         fg=iu.DIM)
        else:
            self.lbl_guion.configure(text="Sin guion", fg=iu.MUTED)
            self.lbl_guion_dir.configure(
                text="Sin guion: se transcribe el audio tal cual (ideal para videos sueltos).",
                fg=iu.DIM)
        srt = self.v_srt.get()
        if srt:
            self.lbl_srt.configure(text=_corto(os.path.basename(srt), 52), fg=iu.TEXT)
            self.lbl_srt_dir.configure(text=_corto(os.path.dirname(srt), 74),
                                       fg=iu.DIM)
        else:
            nombre = self.timeline["nombre"] if self.timeline else "timeline"
            self.lbl_srt.configure(text=_corto(f"{nombre}.srt", 52), fg=iu.MUTED)
            self.lbl_srt_dir.configure(
                text="Se guarda junto al guion o en Documentos.", fg=iu.DIM)

    # ─────────────────────────────────────────────────────────
    # ACCIONES DE ARCHIVO
    # ─────────────────────────────────────────────────────────
    def _elegir_guion(self):
        p = filedialog.askopenfilename(
            title="Seleccionar guion",
            filetypes=[("Guiones", "*.txt *.docx"), ("Word", "*.docx"),
                       ("Texto", "*.txt")])
        if not p:
            return
        self.v_guion.set(p)
        if not self.v_srt.get():
            self.v_srt.set(os.path.join(os.path.dirname(p),
                                        os.path.splitext(os.path.basename(p))[0] + ".srt"))
        self._actualizar_rutas()
        self._guardar()
        self._log(f"Guion elegido: {p}", "cmd")
        self._set_estado("idle", f"Guion cargado: {os.path.basename(p)}")

    def _limpiar_guion(self):
        self.v_guion.set("")
        self._actualizar_rutas()
        self._guardar()
        self._log("Guion quitado; se trabaja solo con el audio.", "info")
        self._set_estado("idle", "Guion quitado. Podés trabajar sin guion.")

    def _elegir_srt(self):
        ini = os.path.dirname(self.v_srt.get() or self.v_guion.get()) or None
        nombre = (self.timeline["nombre"] if self.timeline else "subtitulos")
        p = filedialog.asksaveasfilename(
            title="Guardar subtítulos SRT",
            initialdir=ini or os.path.join(os.path.expanduser("~"), "Videos"),
            initialfile=nombre + ".srt",
            defaultextension=".srt", filetypes=[("SRT", "*.srt")])
        if not p:
            return
        self.v_srt.set(p)
        self._actualizar_rutas()
        self._guardar()
        self._log(f"Destino del SRT: {p}", "cmd")

    def _abrir_carpeta(self):
        ruta = self.v_srt.get() or self.v_guion.get()
        carpeta = os.path.dirname(ruta) if ruta else os.path.expanduser("~")
        try:
            os.startfile(carpeta)
        except Exception as exc:
            self._log(f"No pude abrir la carpeta: {exc}", "err")

    # ─────────────────────────────────────────────────────────
    # EJECUCIÓN
    # ─────────────────────────────────────────────────────────
    def _go(self):
        if self._busy:
            return
        if not (self.v_subs.get() or self.v_txt.get() or self.v_comp.get()
                or self.v_solo.get()):
            messagebox.showwarning("Nada para hacer",
                                   "Marcá al menos una opción en «1. QUÉ QUERÉS HACER».")
            return
        if self.v_comp.get() and not self.v_guion.get():
            messagebox.showwarning("Falta el guion",
                                   "Para comparar con el guion necesito el guion.")
            return
        if self.v_guion.get() and not self.backend.existe(self.v_guion.get()):
            messagebox.showerror("Guion no encontrado",
                                 "El archivo seleccionado ya no existe.")
            return
        if not self.pistas_sel:
            messagebox.showwarning("Sin audio",
                                   "Elegí al menos una pista de audio en «2. AUDIO Y RANGO».")
            return
        self._refresh_timeline()
        if not self.timeline:
            messagebox.showerror("Sin timeline",
                                 "No hay ninguna timeline abierta en Resolve.")
            return
        self._guardar()
        cfg = {
            "guion": "" if self.v_solo.get() else self.v_guion.get(),
            "srt": self.v_srt.get(),
            "plantilla": self.v_tpl.get(),
            "pistas": sorted(self.pistas_sel),
            "rango": self.v_rango.get(),
            "subs": self.v_subs.get() or self.v_solo.get(),
            "txt": False if self.v_solo.get() else self.v_txt.get(),
            "comparar": False if self.v_solo.get() else self.v_comp.get(),
            "solo_srt": self.v_solo.get(),
            "idioma": self._cod_idioma.get(self.v_idioma.get(), "auto"),
            "traducir": bool(self.v_trad.get()
                             and (self.v_subs.get() or self.v_solo.get())),
            "idioma_destino": self._cod_destino.get(self.v_destino.get(), "es"),
            "estilo_trad": ("palabras" if self.v_estilo.get() == "PALABRAS"
                            else "frases"),
        }
        self._busy = True
        self.cta.set_enabled(False)
        self.cta.set_text("PROCESANDO…")
        self.meter.set(0, animate=False)
        self._set_estado("working", f"Iniciando proceso para «{self.timeline['nombre']}»…")
        self._log(f"── Inicio · «{self.timeline['nombre']}» · "
                  f"{len(cfg['pistas'])} pista(s) · {cfg['rango']}", "cmd")

        def trabajo():
            try:
                self.backend.correr(cfg, self._progreso)
                self._cola.put(("fin", None))
            except Exception as exc:
                tb = traceback.format_exc()
                try:
                    self.backend.registrar_error(exc, tb)
                except Exception:
                    pass
                self._cola.put(("error", f"{type(exc).__name__}: {exc}"))

        threading.Thread(target=trabajo, daemon=True).start()

    def _progreso(self, porcentaje, mensaje):
        self._cola.put(("prog", float(porcentaje), str(mensaje)))

    def _poll(self):
        try:
            while True:
                evento = self._cola.get_nowait()
                if evento[0] == "prog":
                    _, p, msg = evento
                    self.meter.set(p / 100.0)
                    if p >= 100:
                        alerta = ("No pude traducir" in msg
                                  or "PARCIAL" in msg or "no se colocó" in msg)
                        self._set_estado("warn" if alerta else "success", msg)
                    else:
                        self._set_estado("working", msg)
                    self._log(msg, "info")
                elif evento[0] == "error":
                    self._set_estado("error", evento[1])
                    self._log("ERROR · " + evento[1], "err")
                    self.meter.set(0, animate=False)
                    self._fin_trabajo()
                elif evento[0] == "fin":
                    self._fin_trabajo()
        except queue.Empty:
            pass
        except tk.TclError:
            return
        try:
            self.root.after(90, self._poll)
        except tk.TclError:
            pass

    def _fin_trabajo(self):
        self._busy = False
        self.cta.set_enabled(True)
        self.cta.set_text(self._texto_boton())
        self._log("Proceso finalizado; el botón volvió a estar disponible.", "info")

    def _watch(self):
        if not self._busy:
            antes = (self.timeline or {}).get("nombre")
            self._refresh_timeline()
            ahora = (self.timeline or {}).get("nombre")
            if antes and ahora and antes != ahora:
                self._log(f"Cambiaste de timeline: «{ahora}»", "cmd")
                self._set_estado("idle", f"Ahora trabajás sobre «{ahora}».")
            self._actualizar_rutas()
        try:
            self._job_watch = self.root.after(2500, self._watch)
        except tk.TclError:
            pass

    # ─────────────────────────────────────────────────────────
    # CIERRE / ANIMACIÓN
    # ─────────────────────────────────────────────────────────
    def cerrar(self):
        self._guardar()
        if self._busy:
            if not messagebox.askyesno(
                    "Proceso en curso",
                    "La generación todavía está trabajando.\n\n"
                    "Si cerrás ahora se detendrán FFmpeg/FFprobe.\n\n¿Seguro?"):
                return
        try:
            self.backend.cancelar()
        except Exception:
            pass
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def _fade_in(self):
        try:
            self.root.attributes("-alpha", 0.0)
        except tk.TclError:
            return

        def step(alpha=0.0):
            alpha = min(1.0, alpha + 0.16)
            try:
                self.root.attributes("-alpha", alpha)
                if alpha < 1.0:
                    self.root.after(16, lambda: step(alpha))
            except tk.TclError:
                pass
        self.root.after(10, step)

    # el atributo se crea al final para que _progreso pueda usarlo
    @property
    def _cola(self):
        if not hasattr(self, "_cola_obj"):
            self._cola_obj = queue.Queue()
        return self._cola_obj
