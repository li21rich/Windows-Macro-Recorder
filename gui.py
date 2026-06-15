import os
import tkinter as tk
from tkinter import ttk, messagebox

from macro import MacroEngine, list_macros, macro_duration, RECORD_KEY, TEST_KEY

REC_KEY_STR = RECORD_KEY.replace(' ', '+').title()
TEST_KEY_STR = TEST_KEY.replace(' ', '+').title()

class HotkeyDialog:
    """Small popup that captures a single keypress and returns it."""

    def __init__(self, parent):
        self.result = None
        self.top = tk.Toplevel(parent)
        self.top.title("Give Hotkey")
        self.top.resizable(False, False)
        self.top.grab_set()
        self.top.geometry("240x90")

        tk.Label(self.top, text="Press any key to use as hotkey:").pack(pady=(10, 2))
        self.lbl = tk.Label(self.top, text="Waiting...", font=("TkDefaultFont", 8, "bold"))
        self.lbl.pack()
        
        tk.Button(self.top, text="Cancel", command=self.top.destroy, font=("TkDefaultFont", 8))

        self.top.bind("<Key>", self._on_key)
        self.top.focus_force()

    def _on_key(self, event):
        if event.keysym in ("Shift_L", "Shift_R", "Control_L", "Control_R",
                             "Alt_L", "Alt_R", "Super_L", "Super_R"):
            return
        self.result = event.char if (event.char and event.char.isprintable()) else event.keysym
        self.lbl.config(text=f"'{self.result}'")
        self.top.after(350, self.top.destroy)


class MacroGUI:
    def __init__(self, root: tk.Tk, engine: MacroEngine):
        self.root   = root
        self.engine = engine

        root.title("Macro Recorder")
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._on_top_var = tk.BooleanVar(value=True)
        root.wm_attributes("-topmost", True)

        engine.on_state_change = self._on_engine_event

        self._build_ui()
        self._refresh_list()

    # ── UI ────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        PAD = 4
        BTN_FONT = ("TkDefaultFont", 8)

        ctrl = tk.LabelFrame(self.root, text="Controls", padx=PAD, pady=3)
        ctrl.pack(fill="x", padx=PAD, pady=(PAD, 2))

        self.rec_btn = tk.Button(ctrl, text=f"⏺ Record [{REC_KEY_STR}]",
                                  width=15, command=self._toggle_record, font=BTN_FONT)
        self.rec_btn.grid(row=0, column=0, padx=1, pady=1)

        self.save_btn = tk.Button(ctrl, text="Save", width=7,
                                  state="disabled", command=self._save, font=BTN_FONT)
        self.save_btn.grid(row=0, column=1, padx=1, pady=1)

        self.status_var = tk.StringVar(value="Ready")
        tk.Label(ctrl, textvariable=self.status_var, anchor="w", width=20
                 ).grid(row=0, column=2, padx=(6, 0))

        lst = tk.LabelFrame(self.root, text="Macros", padx=PAD, pady=3)
        lst.pack(fill="both", expand=True, padx=PAD, pady=1)

        cols = ("name", "duration", "hotkey")
        self.tree = ttk.Treeview(lst, columns=cols, show="headings",
                                 height=5, selectmode="browse")
        self.tree.heading("name",     text="File")
        self.tree.heading("duration", text="Duration")
        self.tree.heading("hotkey",   text="Hotkey")
        self.tree.column("name",     width=90, anchor="w")
        self.tree.column("duration", width=90,  anchor="center")
        self.tree.column("hotkey",   width=90,  anchor="center")
        self.tree.pack(side="left", fill="both", expand=True)

        sb = tk.Scrollbar(lst, command=self.tree.yview)
        sb.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=sb.set)

        acts = tk.Frame(self.root, padx=PAD, pady=3)
        acts.pack(fill="x")

        self.play_btn = tk.Button(acts, text="▶ Play", width=6, command=self._play, font=BTN_FONT)
        self.play_btn.pack(side="left", padx=1)
        
        self.test_btn = tk.Button(acts, text=f"Test [{TEST_KEY_STR}]", width=10, command=self._test_macro, font=BTN_FONT)
        self.test_btn.pack(side="left", padx=1)

        tk.Button(acts, text="Delete", width=6,
                  command=self._delete, font=BTN_FONT).pack(side="left", padx=1)
        
        tk.Button(acts, text="Clear Hotkey", width=10,
                  command=self._clear_hotkey, font=BTN_FONT).pack(side="right", padx=1)
        tk.Button(acts, text="Give Hotkey", width=10,
                  command=self._assign_hotkey, font=BTN_FONT).pack(side="right", padx=1)

        bottom = tk.Frame(self.root, padx=PAD)
        bottom.pack(fill="x", pady=(0, PAD))

        tk.Label(bottom, text=f"{REC_KEY_STR} = rec/stop   {TEST_KEY_STR} = test   Ctrl+C = quit",
                 anchor="w", fg="gray", font=("TkDefaultFont", 8)
                 ).pack(side="left")

        tk.Checkbutton(
            bottom, text="Keep on-screen",
            variable=self._on_top_var,
            command=self._toggle_on_top,
            font=("TkDefaultFont", 8),
        ).pack(side="right")

    def _toggle_on_top(self):
        self.root.wm_attributes("-topmost", self._on_top_var.get())

    # ── List ──────────────────────────────────────────────────────────────────
    def _refresh_list(self):
        for row in self.tree.get_children():
            self.tree.delete(row)
        for fp in list_macros():
            name = os.path.basename(fp)
            dur  = f"{macro_duration(fp):.1f}s"
            hk   = self.engine.hotkey_for(fp) or "—"
            self.tree.insert("", "end", iid=fp, values=(name, dur, hk))

    def _selected(self) -> str | None:
        sel = self.tree.selection()
        return sel[0] if sel else None

    # ── Actions ───────────────────────────────────────────────────────────────
    def _toggle_record(self):
        if not self.engine.is_recording:
            self.engine.start_recording()
        else:
            self.engine.stop_recording()

    def _test_macro(self):
        if self.engine.is_playing():
            self.engine.stop_playback()
        else:
            self.engine.play_temporary()

    def _save(self):
        if not self.engine.has_unsaved():
            messagebox.showinfo("Nothing to save", "Record something first.")
            return
        fname = self.engine.save_current()
        self.status_var.set(f"Saved {os.path.basename(fname)}")
        self.save_btn.config(state="disabled")
        self._refresh_list()

    def _play(self):
        if self.engine.is_playing():
            self.engine.stop_playback()
            return
        fp = self._selected()
        if not fp:
            fp = self.engine.get_latest_file()
        if not fp:
            messagebox.showinfo("No macro", "No macros found. Record one first.")
            return
        
        if not os.path.exists(fp):
            self._refresh_list()
            messagebox.showerror("Error", "Macro file not found or was removed externally.")
            return

        self.engine.play(fp)

    def _assign_hotkey(self):
        fp = self._selected()
        if not fp:
            messagebox.showinfo("No selection", "Select a macro from the list first.")
            return
        dlg = HotkeyDialog(self.root)
        self.root.wait_window(dlg.top)
        if dlg.result:
            req_char = RECORD_KEY.split()[-1].lower()
            if dlg.result.lower() == req_char:
                messagebox.showwarning("Reserved", f"'{dlg.result}' conflicts with the record hotkey ({REC_KEY_STR}).")
                return
            self.engine.assign_hotkey(dlg.result, fp)
            self._refresh_list()
            self.status_var.set(f"'{dlg.result}' → {os.path.basename(fp)}")

    def _clear_hotkey(self):
        fp = self._selected()
        if not fp:
            return
        self.engine.clear_hotkey(fp)
        self._refresh_list()

    def _delete(self):
        fp = self._selected()
        if not fp:
            return
        if not messagebox.askyesno("Delete", f"Delete {os.path.basename(fp)}?"):
            return
        try:
            self.engine.delete_macro(fp)
        except OSError as e:
            messagebox.showerror("Error", str(e))
        self._refresh_list()

    # ── Engine event callback (called from pynput thread) ─────────────────────
    def _on_engine_event(self, event: str):
        self.root.after(0, self._apply_event, event)

    def _apply_event(self, event: str):
        if event == 'record_start':
            self.rec_btn.config(text=f"⏹ Stop ({REC_KEY_STR})")
            self.status_var.set("Recording...")
            self.save_btn.config(state="disabled")
        elif event == 'record_stop':
            self.rec_btn.config(text=f"⏺ Record ({REC_KEY_STR})")
            self.status_var.set("Ended. Hit save to keep.")
            self.save_btn.config(state="normal")
        elif event == 'play_start':
            self.play_btn.config(text="⏹ Stop")
            self.status_var.set("Playing...")
        elif event == 'play_stop':
            self.play_btn.config(text="▶ Play")
            self.status_var.set("Ready.")
        elif event == 'save':
            self._refresh_list()

    def _on_close(self):
        self.engine.stop_playback()
        self.root.destroy()
        os._exit(0)

if __name__ == "__main__":
    engine = MacroEngine()
    root = tk.Tk()
    gui = MacroGUI(root, engine)
    root.mainloop()