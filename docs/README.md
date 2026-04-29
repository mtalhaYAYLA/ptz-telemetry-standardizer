# PTZ Telemetry & Scanning Engine

Termal kamera ağındaki cihazların fiziksel konum verilerini (Pan/Tilt) ONVIF protokolü
üzerinden asenkron olarak takip eden, standartlaştıran ve otonom tarama rotaları oluşturan sistem.

---

## Hızlı Kurulum

### 1. Gereksinimler

- Python 3.9+
- Ağ erişimi olan ONVIF destekli termal PTZ kameralar

### 2. Bağımlılıkları Kur

```bash
pip install -r requirements.txt
```

`requirements.txt` içeriği:
```
onvif-zeep
numpy
python-dotenv
requests
```

### 3. Kamera Yapılandırması

`config/config.json` dosyasını düzenle:

```json
{
  "cameras": [
    {
      "name": "Kamera-1_Depo",
      "camera_id": 26,
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
        "step_Ydeg": 10,
        "tilt_fixed_deg": 0,
        "step_deg": 10,
        "wait_sec": 3,
        "post_move_delay_sec": 6.0
      }
    }
  ]
}
```

### 4. Çalıştır

```bash
python main.py
```

Çıktı:
```
=== EREN TERMAL PTZ MONITORING START ===
[*] Kamera-1_Depo (192.168.1.100) takibi başladı.

Canlı Veri İzleniyor... (Durdurmak için CTRL+C)

| Kamera-1_Depo: P:240.0° T:5.0° | ...
```

Durdurmak için: `CTRL+C`

---

## Mimari

```
ptz-telemetry-standardizer/
├── main.py              ← Giriş noktası. Config okur, worker'ları başlatır.
├── config/
│   └── config.json      ← Kamera IP, kimlik bilgileri, tarama parametreleri
├── src/
│   ├── core.py          ← ONVIF bağlantı + AbsoluteMove + GetStatus
│   ├── transformer.py   ← ONVIF ham koordinat → fiziksel derece dönüşümü
│   ├── scanner.py       ← Tarama rotası üretici (X, S-tarama, 360° geçiş)
│   └── worker.py        ← Her kamera için bağımsız daemon thread
└── docs/
    └── README.md        ← Bu dosya
```

---

## Bileşenler

### `src/core.py` — Bağlantı Katmanı

ONVIF protokolü üzerinden kameraya bağlanır, hareket komutu gönderir ve konum okur.

| Metot | Girdi | Çıktı | Açıklama |
|-------|-------|-------|----------|
| `__init__` | name, ip, user, password | — | Nesne oluşturur |
| `connect()` | — | `bool` | ONVIF bağlantısı kurar |
| `move_to(pan_deg, tilt_deg)` | 0–359°, -5–90° | — | AbsoluteMove gönderir |
| `get_status()` | — | PanTilt nesnesi veya `None` | Anlık ONVIF konumu okur |

**Koordinat Dönüşümü (Yazma — V1 formülü):**
```
corrected_pan = (pan_deg - 180 + 360) % 360
ONVIF x = interp(corrected_pan, [0, 360], [-1, 1])
ONVIF y = interp(tilt_deg,      [-5, 90], [1.0, -1.0])
```

---

### `src/transformer.py` — Birim Dönüştürücü

ONVIF'ten okunan ham -1/1 koordinatlarını fiziksel derecelere çevirir.

| Metot | Girdi | Çıktı | Açıklama |
|-------|-------|-------|----------|
| `onvif_to_degree(x, y)` | ONVIF x, y (-1.0–1.0) | `(pan_deg, tilt_deg)` | Ham koordinat → derece |

**Koordinat Dönüşümü (Okuma):**
```
pan_deg  = (interp(x, [-1, 1], [0, 360]) + 180) % 360   ← montaj açısı düzeltmesi
tilt_deg =  interp(y, [-1.0, 1.0], (90, -5))             ← ters orantı
```

> **Not:** Pan'daki +180° düzeltmesi, `move_to()`'daki -180° dönüşümünün matematiksel
> teridir. İkisi birlikte pan komut = pan okuma eşitliğini sağlar.

---

### `src/scanner.py` — Rota Üretici

`scan_parameters` bloğunu analiz ederek kameranın izleyeceği (pan, tilt) tur noktaları listesi üretir.

| Metot | Girdi | Çıktı | Açıklama |
|-------|-------|-------|----------|
| `generate_tour_points(scan_p)` | scan_parameters dict | `list[(pan, tilt)]` | Tur noktaları |

**Tarama Modları:**

| Mod | Koşul | Davranış |
|-----|-------|----------|
| Sabit Nokta | `pan_start == pan_end` | Tek noktada bekler |
| X Tarama | `pan_start < pan_end` | İleri-geri yatay tarama |
| 360° X Tarama | `pan_start > pan_end` | 0/360° sınırını geçerek tarama |
| 2D S-Tarama | `pan_start_Ydeg` ve `pan_end_Ydeg` mevcut | Her tilt seviyesinde yön değiştirerek tam alan |

**S-Tarama Örneği** (pan 240→60, tilt 5→25, step 10):
```
Tilt=5°:  Pan 240→250→...→360→0→...→60  (ileri)
Tilt=15°: Pan 60→50→...→0→359→...→240   (geri)
Tilt=25°: Pan 240→250→...→60            (ileri)
```

---

### `src/worker.py` — Asenkron İzleyici

Her kamera için bağımsız bir `daemon=True` thread çalıştırır.

| Metot | Açıklama |
|-------|----------|
| `__init__(client, transformer, scan_params)` | Thread hazırlar, tur noktalarını üretir |
| `run()` | Tarama döngüsünü başlatır (threading.Thread giriş noktası) |
| `stop()` | `running=False` yaparak döngüyü güvenli durdurur |
| `safe_data` (property) | Thread-safe `{"pan": float, "tilt": float}` döner |

**Döngü Akışı (her tur noktası):**
```
move_to(pan, tilt)
  └── post_move_delay_sec kadar bekle (kamera yerleşsin)
      └── wait_sec boyunca her 500ms:
          └── get_status() → onvif_to_degree() → current_data güncelle
```

**Hata Yönetimi:** Ağ kopması veya herhangi bir istisna → 10 saniye bekle → döngüye devam.

---

### `main.py` — Sistem Yöneticisi

| Fonksiyon | Açıklama |
|-----------|----------|
| `load_local_config()` | `config/config.json` okur, parse eder |
| `main()` | Config'deki her kamera için worker başlatır, ekrana canlı veri basar |

---

## config.json Parametreleri

### Kamera Bloğu

| Anahtar | Tip | Açıklama |
|---------|-----|----------|
| `name` | string | Görünen ad (log ve ekranda görünür) |
| `camera_id` | int | Sistem içi kamera kimliği |
| `ip_address` | string | Kameranın IP adresi |
| `credentials.user` | string | ONVIF kullanıcı adı |
| `credentials.pass` | string | ONVIF şifresi |

### scan_parameters Bloğu

| Anahtar | Tip | Zorunlu | Açıklama |
|---------|-----|---------|----------|
| `pan_start_deg` | int | Evet | Pan başlangıç açısı (0–359) |
| `pan_end_deg` | int | Evet | Pan bitiş açısı (0–359) |
| `step_deg` | float | Hayır (v: 10) | Pan adım büyüklüğü |
| `pan_start_Ydeg` | float | Hayır | Tilt başlangıç açısı (S-tarama) |
| `pan_end_Ydeg` | float | Hayır | Tilt bitiş açısı (S-tarama) |
| `step_Ydeg` | float | Hayır (v: 10) | Tilt adım büyüklüğü |
| `tilt_fixed_deg` | float | Hayır (v: 0) | Sabit tilt açısı (yalnızca X-tarama) |
| `wait_sec` | float | Hayır (v: 3) | Her noktada gözlem süresi (saniye) |
| `post_move_delay_sec` | float | Hayır (v: 1.0) | Hareket sonrası yerleşme süresi (saniye) |

---

## Teknik Notlar

### Neden `sys.setrecursionlimit(5000)`?
Zeep kütüphanesi, ONVIF'in karmaşık WSDL/SOAP şemalarını parse ederken Python'un varsayılan
1000 frame limitini aşabilir. Bu satır `core.py`'nin en üstünde tanımlıdır.

### Pan +180° Düzeltmesi Neden Var?
Bu kamera modelinde ONVIF koordinat sistemi fiziksel 0° yönünü 180° offset ile tanımlar.
`move_to()` yazarken -180°, `onvif_to_degree()` okurken +180° uygulanır → net etki sıfır,
komut ve geri okuma tutarlıdır.

### Thread Güvenliği
`current_data` dict'i `threading.Lock` ile korunur. Dışarıdan her zaman `safe_data`
property'si üzerinden okunmalıdır.

### Daemon Thread
Tüm worker'lar `daemon=True` ile başlatılır; ana process kapandığında OS otomatik temizler.
`stop()` çağrısı mevcut bekleme süresi dolunca thread'i nazikçe durdurur.
