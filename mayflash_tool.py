#!/usr/bin/env python3
import hid
import sys
import time
import argparse

# Mayflash W012 USB IDs (PC Mode)
VID = 0x0079  # DragonRise
PID = 0x1843  # Mayflash PC

def find_adapter():
    for device in hid.enumerate():
        if device['vendor_id'] == VID and device['product_id'] == PID:
            return device
    return None

def listen_mode(dev_path):
    print(f"Opening adapter at {dev_path}")
    h = hid.Device(path=dev_path)
    try:
        h.nonblocking = True
        print("Listening for reports (press Ctrl+C to stop)...")
        while True:
            data = h.read(64, timeout=1000)
            if data:
                hex_str = ' '.join([f"{b:02X}" for b in data])
                print(f"Report [{len(data)} bytes]: {hex_str}")
    except KeyboardInterrupt:
        print("\nExiting...")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        h.close()

def send_custom_command(dev_path, cmd_bytes):
    h = hid.Device(path=dev_path)
    try:
        # W012 updater sends 65 byte reports (1 byte ID = 0, + 64 bytes data)
        # Pad with zeros
        report = [0x00] + list(cmd_bytes)
        while len(report) < 65:
            report.append(0)
            
        print(f"Sending custom command: {' '.join([f'{b:02X}' for b in report])}")
        
        # Some OSes (like Linux) might expect 64 bytes without ID if ID=0, or 65.
        # hidapi python wrapper expects ID as first byte for write() if using report IDs.
        # WiiU adapter uses Interrupt OUT endpoint for rumble/init.
        
        h.write(bytes(report))
        print("Sent. Waiting for response...")
        
        data = h.read(64, timeout=2000)
        if data:
            hex_str = ' '.join([f"{b:02X}" for b in data])
            print(f"Response: {hex_str}")
        else:
            print("No response received.")
            
    except Exception as e:
        print(f"Error: {e}")
    finally:
        h.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Mayflash W012 Analysis & Flashing Tool")
    parser.add_argument("action", choices=["listen", "info", "test_cmd", "extract", "updater_ping"], help="Action to perform")
    parser.add_argument("--exe", type=str, help="Path to official firmware .exe (for extract)")
    parser.add_argument("--out", type=str, default="firmware", help="Output prefix (for extract)")
    args = parser.parse_args()
    
    if args.action == "extract":
        if not args.exe:
            print("Error: --exe is required for extraction")
            sys.exit(1)
            
        print(f"Extracting firmware from {args.exe}...")
        try:
            with open(args.exe, 'rb') as f:
                data = f.read()
            
            # Find the magic header (after XOR)
            # Before XOR: 34 4A 83 81
            magic = bytes([0x34, 0x4A, 0x83, 0x81])
            idx = data.find(magic)
            if idx < 0:
                print("Error: Magic header not found. Is this a valid W012 updater?")
                sys.exit(1)
                
            # Usually 9 blocks of 0x8020
            fw_data = data[idx:idx + (9 * 0x8020)]
            print(f"Found firmware at offset 0x{idx:X}. Extracted {len(fw_data)} bytes.")
            
            # XOR Decode
            decoded = bytes(b ^ 0xCB for b in fw_data)
            
            # Save raw XOR decoded
            out_bin = f"{args.out}_decoded.bin"
            with open(out_bin, 'wb') as f:
                f.write(decoded)
            print(f"Saved decoded firmware to {out_bin}")
            
            # Strip headers to get payload
            payload = b""
            for i in range(9):
                block = decoded[i*0x8020:(i+1)*0x8020]
                payload += block[32:]
                
            out_payload = f"{args.out}_payload.bin"
            with open(out_payload, 'wb') as f:
                f.write(payload)
            print(f"Saved payload (headers stripped) to {out_payload}")
            
        except Exception as e:
            print(f"Error extracting: {e}")
        sys.exit(0)
        
    dev_info = find_adapter()
    if not dev_info:
        print("Adapter not found. Please ensure it's plugged in and switched to 'PC' mode.")
        sys.exit(1)
        
    print(f"Found Adapter: VID=0x{dev_info['vendor_id']:04X} PID=0x{dev_info['product_id']:04X} "
          f"Manufacturer='{dev_info['manufacturer_string']}' Product='{dev_info['product_string']}'")
          
    if args.action == "info":
        print(f"Path: {dev_info['path']}")
    elif args.action == "listen":
        listen_mode(dev_info['path'])
    elif args.action == "test_cmd":
        # Let's send the 0x13 init command that standard drivers send
        send_custom_command(dev_info['path'], [0x13])
    elif args.action == "updater_ping":
        # Send the exact command the official V10 updater uses to check version
        # 402fa8: mov DWORD PTR [esi+0xd7], 0x329d00cb  -> CB 00 9D 32
        # 402fb2: mov WORD PTR [esi+0xdb], 0x12         -> 12 00
        # This is at offsets 1-6 in the report (after ID 0x00)
        cmd = [0xCB, 0x00, 0x9D, 0x32, 0x12, 0x00]
        send_custom_command(dev_info['path'], cmd)
