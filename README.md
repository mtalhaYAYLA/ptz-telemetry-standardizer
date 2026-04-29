# ptz-telemetry-standardizer

Termal kamera ağındaki PTZ cihazlarının fiziksel konum verilerini (Pan/Tilt) ONVIF protokolü
üzerinden gerçek zamanlı olarak takip eden, standartlaştıran ve otonom tarama rotaları
oluşturan yüksek hassasiyetli telemetri motoru.

---

## Özellikler

- **Çoklu Kamera Desteği** — Her kamera bağımsız daemon thread üzerinde çalışır
- **Gerçek Zamanlı Telemetri** — ONVIF GetStatus ile 500ms hassasiyette konum okuma
- **Akıllı Tarama Rotaları** — X-Tarama, 2D S-Tarama ve 360° geçiş desteği
- **Yüksek Hassasiyet** — Ham ONVIF koordinatları fiziksel dereceye matematiksel dönüşüm
- **Kararlı Yapı** — Ağ kopması durumunda thread ölmez, otomatik devam eder

---

## Hızlı Başlangıç

### 1. Bağımlılıkları Kur

```bash
pip install -r requirements.txt
```

### 2. Kamera Yapılandırması

`config/config.json` dosyasını düzenle:

```json
{
  "cameras": [
    {
      "name": "Kamera-1_Depo",
      "camera_id": 1,
      "ip_address": "192.168.1.100",
      "credentials": {
        "user": "admin",
        "pass": "sifre"
      },
      "scan_parameters": {
        "pan_start_deg": 240,
        "pan_end_deg": 60,
        "pan_start_Ydeg": 5,
        "pan_end_Ydeg": 75,
        "step_deg": 10,
        "step_Ydeg": 10,
        "wait_sec": 3,
        "post_move_delay_sec": 6.0
      }
    }
  ]
}
```

### 3. Çalıştır

```bash
python main.py
```

```
=== EREN TERMAL PTZ MONITORING START ===
[*] Kamera-1_Depo (192.168.1.100) takibi başladı.

Canlı Veri İzleniyor... (Durdurmak için CTRL+C)

| Kamera-1_Depo: P:240.0° T:5.0° | Kamera-2_Depo: P:45.0° T:5.0° |
```

---

## Proje Yapısı

```
ptz-telemetry-standardizer/
├── main.py              ← Giriş noktası
├── requirements.txt
├── config/
│   └── config.json      ← Kamera ve tarama ayarları
├── src/
│   ├── core.py          ← ONVIF bağlantı + hareket + konum okuma
│   ├── transformer.py   ← Ham ONVIF koordinat → fiziksel derece
│   ├── scanner.py       ← Tarama rotası üretici
│   └── worker.py        ← Kamera başına asenkron thread
└── docs/
    └── README.md        ← Detaylı teknik dokümantasyon
```

---

## Mimari

```
main.py
  └── config.json'u okur
  └── Her kamera için:
        CameraClient  →  ONVIF bağlantısı kurar
        PTZTransformer → Koordinat dönüştürücü hazırlar
        TelemetryWorker (Thread) → Tarama döngüsünü başlatır
              │
              ├── PTZScanner → Tur noktaları üretir
              ├── move_to()  → Kamerayı konuma gönderir
              └── get_status() → Anlık Pan/Tilt okur → ekrana yazar
```

---

## Tarama Modları

| Mod | Koşul | Açıklama |
|-----|-------|----------|
| Sabit Nokta | `pan_start == pan_end` | Kamera tek noktada bekler |
| X-Tarama | `pan_start < pan_end` | Yatay eksende ileri-geri |
| 360° X-Tarama | `pan_start > pan_end` | 0°/360° sınırını geçerek tarama |
| 2D S-Tarama | `pan_start_Ydeg` tanımlı | Her tilt seviyesinde yön değiştirerek tam alan |

---

## Detaylı Dokümantasyon

Tüm bileşenlerin teknik detayları, parametre açıklamaları ve koordinat dönüşüm
formülleri için: **[docs/README.md](docs/README.md)**

---

## Gereksinimler

| Paket | Amaç |
|-------|------|
| `onvif-zeep` | ONVIF/SOAP protokol istemcisi |
| `numpy` | Koordinat dönüşüm hesaplamaları |
| `python-dotenv` | Ortam değişkeni yönetimi |
| `requests` | HTTP yardımcı kütüphanesi |
