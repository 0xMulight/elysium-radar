"""Windows-friendly monitor GUI; only Python standard library."""
import json
import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk
from pathlib import Path

from elysium_radar import CONFIG, STATE, EVENTS, Radar, RPC, display_odds, display_roi, ensure_config, load_config, load_state


class MonitorUI:
    def __init__(self, root):
        ensure_config()
        self.root = root
        self.root.title("Elysium Radar · 主网新币 + 404机器")
        self.root.geometry("900x690")
        self.root.minsize(760, 580)
        self.q = queue.Queue()
        self.stop = threading.Event()
        self.worker = None
        self.cfg = load_config()
        self._build()
        self.root.after(120, self.drain)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def _build(self):
        frame = ttk.Frame(self.root, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="ELYSIUM RADAR", font=("Segoe UI", 19, "bold")).pack(anchor="w")
        ttk.Label(frame, text="Kinetiq Elysium (99901) · 只读链上监控 · 绝不触发交易", foreground="#667085").pack(anchor="w", pady=(1, 12))
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(0, 12))
        self.start_button = ttk.Button(buttons, text="▶ 开始持续监控", command=self.start)
        self.start_button.pack(side="left", padx=(0, 8))
        self.stop_button = ttk.Button(buttons, text="■ 停止", command=self.halt, state="disabled")
        self.stop_button.pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="仅运行一次", command=self.once).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="检查主网", command=self.check).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="机器赔率", command=self.odds).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="收益模型", command=self.roi).pack(side="left")

        settings = ttk.LabelFrame(frame, text="预警设置（可选，保存后生效）", padding=10)
        settings.pack(fill="x", pady=(0, 10))
        ttk.Label(settings, text="Telegram Bot Token").grid(row=0, column=0, sticky="w", padx=(0, 6), pady=3)
        self.token = ttk.Entry(settings, show="•", width=43)
        self.token.grid(row=0, column=1, sticky="we", pady=3)
        self.token.insert(0, self.cfg["telegram"].get("bot_token", ""))
        ttk.Label(settings, text="Chat ID").grid(row=1, column=0, sticky="w", pady=3)
        self.chat = ttk.Entry(settings)
        self.chat.grid(row=1, column=1, sticky="we", pady=3)
        self.chat.insert(0, self.cfg["telegram"].get("chat_id", ""))
        self.tg_enabled = tk.BooleanVar(value=bool(self.cfg["telegram"].get("enabled")))
        ttk.Checkbutton(settings, text="启用 Telegram 异常通知", variable=self.tg_enabled).grid(row=2, column=1, sticky="w")
        actions = ttk.Frame(settings)
        actions.grid(row=0, column=2, rowspan=3, sticky="n", padx=12)
        ttk.Button(actions, text="保存设置", command=self.save).pack(fill="x", pady=2)
        ttk.Button(actions, text="测试 Telegram", command=self.test_telegram).pack(fill="x", pady=2)
        ttk.Button(actions, text="打开配置文件", command=self.edit_config).pack(fill="x", pady=2)
        settings.columnconfigure(1, weight=1)
        self.status = tk.StringVar(value="未运行 | 初次运行会建立基线，不向你推送旧币")
        ttk.Label(frame, textvariable=self.status, font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(3, 7))
        self.output = scrolledtext.ScrolledText(frame, font=("Consolas", 10), wrap="word", state="disabled")
        self.output.pack(fill="both", expand=True)
        footer = ttk.Frame(frame)
        footer.pack(fill="x", pady=(8, 0))
        ttk.Button(footer, text="查看近期通知", command=self.show_events).pack(side="left", padx=(0, 6))
        ttk.Button(footer, text="清空界面", command=self.clear).pack(side="left")
        ttk.Label(footer, text="不会连接钱包，也不会收集助记词。关闭窗口即停止持续监控。", foreground="#667085").pack(side="right")
        self.write("准备就绪。点击「检查主网」，再点击「开始持续监控」。")

    def write(self, msg):
        self.output.configure(state="normal")
        self.output.insert("end", msg + "\n")
        self.output.see("end")
        self.output.configure(state="disabled")

    def drain(self):
        try:
            while True:
                kind, value = self.q.get_nowait()
                if kind == "log":
                    self.write(value)
                elif kind == "status":
                    self.status.set(value)
                elif kind == "finished":
                    self.worker = None
                    self.start_button.configure(state="normal")
                    self.stop_button.configure(state="disabled")
        except queue.Empty:
            pass
        self.root.after(120, self.drain)

    def save(self):
        # Credentials stay in config.json on this computer; never printed in logs.
        cfg = load_config()
        cfg["telegram"]["enabled"] = self.tg_enabled.get()
        cfg["telegram"]["bot_token"] = self.token.get().strip()
        cfg["telegram"]["chat_id"] = self.chat.get().strip()
        CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        self.cfg = cfg
        self.write("设置已保存到本地 config.json。改动在下一次点击开始监控后生效。")

    def task(self, func, repeat=False):
        if self.worker and self.worker.is_alive():
            self.write("已有任务正在运行。请先停止，避免重复扫描。")
            return
        self.stop.clear()
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")

        def wrapper():
            try:
                while not self.stop.is_set():
                    try:
                        func()
                        self.q.put(("status", "监控运行中 · " + time.strftime("%H:%M:%S")))
                    except Exception as exc:
                        self.q.put(("log", "⚠️ 安全停止本轮：" + str(exc)[:300]))
                        self.q.put(("status", "本轮失败，查看日志"))
                    if not repeat:
                        break
                    self.stop.wait(max(15, int(load_config()["monitor"]["poll_seconds"])))
            finally:
                self.q.put(("finished", None))
                self.q.put(("status", "已停止监控"))
        self.worker = threading.Thread(target=wrapper, daemon=True)
        self.worker.start()

    def start(self):
        self.save()
        self.task(lambda: Radar(load_config(), on_message=lambda s: self.q.put(("log", s))).once(), repeat=True)

    def halt(self):
        self.stop.set()
        self.write("正在停止。网络请求完成后会退出当前轮询。")

    def once(self):
        self.save()
        self.task(lambda: Radar(load_config(), on_message=lambda s: self.q.put(("log", s))).once())

    def check(self):
        def run():
            rpc = RPC(load_config()["network"]["rpc_urls"])
            height = rpc.check()
            self.q.put(("log", f"✅ 已验证 Kinetiq Elysium 主网，chain ID 99901，区块 #{height}，RPC {rpc.url}"))
        self.task(run)

    def test_telegram(self):
        self.save()
        self.task(lambda: Radar(load_config(), on_message=lambda s: self.q.put(("log", s))).send_telegram("这是一条测试通知，没有进行任何链上交易。", force=True))

    def odds(self):
        try:
            msg = display_odds(load_config(), 1)
            messagebox.showinfo("404 机器抽取概率", msg)
        except Exception as exc:
            messagebox.showerror("无法计算", str(exc))

    def roi(self):
        try:
            messagebox.showinfo("404 机器收益率情景模型", display_roi(load_config()))
        except Exception as exc:
            messagebox.showerror("无法计算", str(exc))

    def show_events(self):
        if not EVENTS.exists():
            self.write("还没有记录到预警事件。")
            return
        lines = EVENTS.read_text(encoding="utf-8").splitlines()[-20:]
        self.write("—— 最近预警 ——")
        for line in lines:
            try:
                msg = json.loads(line)
                self.write(msg["time"] + " " + msg["message"])
            except Exception:
                pass

    def edit_config(self):
        import os
        self.save()
        try:
            if hasattr(os, "startfile"):
                os.startfile(str(CONFIG))
            else:
                self.write("配置文件：" + str(CONFIG))
        except OSError as exc:
            self.write(str(exc))

    def clear(self):
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        self.output.configure(state="disabled")

    def close(self):
        self.stop.set()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    try:
        ttk.Style(root).theme_use("clam")
    except Exception:
        pass
    MonitorUI(root)
    root.mainloop()
