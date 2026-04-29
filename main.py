# (Müdürün Çalıştıracağı Dosya) Senin verdiğin config.json dosyasını tarar ve her kamera için bir motor başlatır.

import os
import json
import time
from src.core import CameraClient
from src.transformer import PTZTransformer
from src.worker import TelemetryWorker

def load_local_config():
    # Dosya yolunu daha güvenli hale getirdik
    path = os.path.join('config', 'config.json')
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def main():
    print("=== EREN TERMAL PTZ MONITORING START ===")
    
    # HATA DÜZELTMESİ 1: Fonksiyon adı load_local_config olarak güncellendi
    try:
        config = load_local_config()
    except Exception as e:
        print(f"Yapılandırma dosyası yüklenemedi: {e}")
        return

    transformer = PTZTransformer()
    workers = []

    for cam in config['cameras']:
        client = CameraClient(
            cam['name'], 
            cam['ip_address'], 
            cam['credentials']['user'], 
            cam['credentials']['pass']
        )
        
        if client.connect():
            # HATA DÜZELTMESİ 2: Eksik olan scan_parameters parametresi eklendi
            worker = TelemetryWorker(client, transformer, cam['scan_parameters'])
            worker.start()
            workers.append(worker)
            print(f"[*] {cam['name']} ({cam['ip_address']}) takibi başladı.")
        else:
            print(f"[!] {cam['name']} ({cam['ip_address']}) bağlantı kurulamadı.")

    try:
        print("\nCanlı Veri İzleniyor... (Durdurmak için CTRL+C)\n")
        while True:
            # Ekrana canlı veri bas
            output = ""
            # HATA DÜZELTMESİ 3: active_workers ismi workers olarak düzeltildi
            for w in workers:
                d = w.current_data
                output += f"| {w.client.name}: P:{d['pan']}° T:{d['tilt']}° "
            
            if output:
                print(output, end="\r")
            time.sleep(0.5)
            
    except KeyboardInterrupt:
        print("\n\nSistem kapatılıyor...")
        for w in workers:
            w.stop()

if __name__ == "__main__":
    main()