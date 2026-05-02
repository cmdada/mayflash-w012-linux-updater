import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import hid
import threading
import queue
import time
import os
import sys

VID = 0x057E
PID = 0x0337

class FirmwareFile:
    def __init__(self, path):
        self.path = path
        self.raw_data = None
        self.blocks = []
        self.version = None
        self.product_id = None
        
    def load(self):
        with open(self.path, 'rb') as f:
            self.raw_data = f.read()
            
        magic = bytes([0x34, 0x4A, 0x83, 0x81])
        idx = self.raw_data.find(magic)
        if idx < 0:
            raise ValueError("Magic header not found. Invalid or unsupported firmware file.")
            
        fw_data = self.raw_data[idx:idx + (9 * 0x8020)]
        if len(fw_data) < 9 * 0x8020:
            raise ValueError("Firmware payload is truncated.")
            
        decoded = bytes(b ^ 0xCB for b in fw_data)
        
        self.blocks = []
        for i in range(9):
            self.blocks.append(decoded[i*0x8020:(i+1)*0x8020])
            
        header = self.blocks[0][:32]
        self.version = header[5]
        self.product_id = header[0x12:0x14]
        return True

class MayflashUpdaterApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Mayflash W012 Linux Updater")
        self.geometry("600x500")
        
        self.fw = None
        self.dev_path = None
        self.log_queue = queue.Queue()
        
        self._build_ui()
        self._check_device()
        self.after(100, self._process_log_queue)
        
    def _build_ui(self):
        # Status Frame
        status_frame = ttk.LabelFrame(self, text="Device Status")
        status_frame.pack(fill="x", padx=10, pady=5)
        
        self.lbl_status = ttk.Label(status_frame, text="Checking...", font=("Arial", 10, "bold"))
        self.lbl_status.pack(pady=5)
        
        btn_refresh = ttk.Button(status_frame, text="Refresh Device", command=self._check_device)
        btn_refresh.pack(pady=5)
        
        # Firmware Frame
        fw_frame = ttk.LabelFrame(self, text="Firmware Selection")
        fw_frame.pack(fill="x", padx=10, pady=5)
        
        self.lbl_fw = ttk.Label(fw_frame, text="No file selected")
        self.lbl_fw.pack(pady=5)
        
        btn_select = ttk.Button(fw_frame, text="Select W012_VXX.exe", command=self._select_file)
        btn_select.pack(pady=5)
        
        # Action Frame
        action_frame = ttk.Frame(self)
        action_frame.pack(fill="x", padx=10, pady=5)
        
        self.var_dry_run = tk.BooleanVar(value=True)
        chk_dry = ttk.Checkbutton(action_frame, text="Dry Run (Do not send flash commands)", variable=self.var_dry_run)
        chk_dry.pack(side="left")
        
        self.btn_update = ttk.Button(action_frame, text="UPDATE FIRMWARE", state="disabled", command=self._start_update)
        self.btn_update.pack(side="right")
        
        self.progress = ttk.Progressbar(self, mode="determinate", length=580)
        self.progress.pack(pady=10)
        
        # Log Frame
        log_frame = ttk.LabelFrame(self, text="Log")
        log_frame.pack(fill="both", expand=True, padx=10, pady=5)
        
        self.txt_log = tk.Text(log_frame, height=10, state="disabled")
        self.txt_log.pack(fill="both", expand=True)
        
    def log(self, msg):
        self.log_queue.put(msg)
        
    def _process_log_queue(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self.txt_log.config(state="normal")
                self.txt_log.insert("end", msg + "\n")
                self.txt_log.see("end")
                self.txt_log.config(state="disabled")
        except queue.Empty:
            pass
        self.after(100, self._process_log_queue)
        
    def _check_device(self):
        found = False
        for dev in hid.enumerate():
            if dev['vendor_id'] == VID and dev['product_id'] == PID:
                self.dev_path = dev['path']
                self.lbl_status.config(text=f"Connected: {dev['manufacturer_string']} {dev['product_string']}", foreground="green")
                self.log("Adapter detected.")
                found = True
                break
                
        if not found:
            self.dev_path = None
            self.lbl_status.config(text="Not Found. Ensure it is plugged in and in Wii U mode.", foreground="red")
            self.log("Adapter not found.")
            
        self._update_ui_state()
            
    def _select_file(self):
        path = filedialog.askopenfilename(filetypes=[("Executable Files", "*.exe"), ("All Files", "*.*")])
        if not path: return
        
        try:
            self.fw = FirmwareFile(path)
            self.fw.load()
            self.lbl_fw.config(text=f"{os.path.basename(path)} (Version: 0x{self.fw.version:02X})")
            self.log(f"Loaded firmware: {path}")
            self.log(f"  Version: 0x{self.fw.version:02X}")
            self.log(f"  Product ID: {' '.join(f'{b:02X}' for b in self.fw.product_id)}")
        except Exception as e:
            self.fw = None
            self.lbl_fw.config(text="Invalid firmware file")
            messagebox.showerror("Error", str(e))
            self.log(f"Firmware load error: {e}")
            
        self._update_ui_state()
        
    def _update_ui_state(self):
        if self.dev_path and self.fw:
            self.btn_update.config(state="normal")
        else:
            self.btn_update.config(state="disabled")
            
    def _start_update(self):
        if not self.var_dry_run.get():
            confirm = messagebox.askyesno("WARNING", 
                "You are about to write to the adapter's flash memory. "
                "Since this uses a reverse-engineered protocol without full bootloader visibility, "
                "there is a risk of bricking your device.\n\nAre you sure you want to proceed?")
            if not confirm:
                return
                
        self.btn_update.config(state="disabled")
        self.progress['value'] = 0
        self.progress['maximum'] = 9 * 513  # 9 blocks, ~513 reports per block
        
        threading.Thread(target=self._update_thread, daemon=True).start()
        
    def _update_thread(self):
        self.log(f"Starting update sequence... Dry Run: {self.var_dry_run.get()}")
        h = hid.Device(path=self.dev_path)
        try:
            # 1. Ping / Init
            self.log("Sending ping command...")
            ping_cmd = [0xCB, 0x00, 0x9D, 0x32, 0x12, 0x00]
            report = [0x00] + ping_cmd + [0]*(64-len(ping_cmd))
            
            if not self.var_dry_run.get():
                h.write(bytes(report))
                time.sleep(0.5)
            
            # 2. Flash Blocks
            for b_idx, block in enumerate(self.fw.blocks):
                self.log(f"Flashing block {b_idx+1}/9...")
                
                # Re-encode with XOR 0xCB to match what device expects
                encoded_block = bytes(b ^ 0xCB for b in block)
                
                reports = [encoded_block[i:i+64] for i in range(0, len(encoded_block), 64)]
                
                for r_idx, r_data in enumerate(reports):
                    report = [0x00] + list(r_data)
                    while len(report) < 65:
                        report.append(0)
                        
                    if not self.var_dry_run.get():
                        h.write(bytes(report))
                        # Small delay to not overwhelm USB buffer
                        time.sleep(0.002) 
                        
                    # Update progress
                    self.progress['value'] += 1
                    
                self.log(f"Block {b_idx+1} sent.")
                time.sleep(0.1) # Block processing delay
                
            self.log("Update sequence completed.")
            self.log("If this was not a dry run, please replug your device.")
            
        except Exception as e:
            self.log(f"CRITICAL ERROR during update: {e}")
        finally:
            h.close()
            # Enable UI via after
            self.after(0, lambda: self.btn_update.config(state="normal"))

if __name__ == "__main__":
    app = MayflashUpdaterApp()
    app.mainloop()
