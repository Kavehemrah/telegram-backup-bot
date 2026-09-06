from backup_bot import load_env
from backup_ui import BackupApp
import tkinter as tk


if __name__ == "__main__":
    load_env()
    root = tk.Tk()
    BackupApp(root)
    root.mainloop()
