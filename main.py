import os
import sys
import tkinter as tk
from macro import MacroEngine
from gui import MacroGUI

if __name__ == "__main__":
    engine = MacroEngine()
    root   = tk.Tk()
    root.title("Macro Recorder")
    base_path = getattr(sys, '_MEIPASS', os.path.abspath("."))
    icon_path = os.path.join(base_path, "pen.ico")
    root.iconbitmap(icon_path)
    app    = MacroGUI(root, engine)
    root.mainloop()