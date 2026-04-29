# PTZ Telemetry & Scanning Engine

Bu sistem, termal kamera ağındaki cihazların fiziksel konum verilerini (Pan/Tilt) ONVIF protokolü üzerinden asenkron olarak takip etmek, standartlaştırmak ve otonom tarama rotaları oluşturmak için geliştirilmiştir.

## 🚀 Hızlı Başlangıç

1. **Bağımlılıkları Kur:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Yapılandırma:**
   `config/config.json` dosyasındaki IP, kullanıcı adı ve tarama (X/Y) parametrelerini güncelleyin.

3. **Sistemi Çalıştır:**
   ```bash
   python main.py
   ```

---

## 🛠 Mimari Bileşenler ve Dosya Rolleri

### 🔗 **src/core.py (Fiziksel Bağlantı Katmanı)**
Kameranın ONVIF protokolü üzerinden güvenli bağlantısını ve kimlik doğrulamasını yönetir.
- **PTZ Yönetimi:** `AbsoluteMove` komutlarını icra eder.
- **Stabilite:** `sys.setrecursionlimit` ayarı ile XML/SOAP parse işlemlerinde oluşabilecek derinlik hatalarını önler.

### 📐 **src/transformer.py (Birim Dönüştürücü / Tercüman)**
Üreticiye özel (vendor-specific) ham ONVIF koordinatlarını (-1.0 ile 1.0 arası), insan tarafından okunabilir 360 derecelik Pan ve fiziksel Tilt açılarına (-5° ile 90°) matematiksel olarak tercüme eder.

### 🧠 **src/scanner.py (Rotasyon Zekası)**
Sistemin otonom hareket mantığını yöneten matematiksel motor. Orijinal kod tabanındaki tüm tarama senaryolarını standartlaştırılmış bir yapıda sunar:
- **X-Scan:** Yatay eksende sürekli tarama.
- **Y-Scan:** Dikey eksende tarama (Tilt).
- **2D S-Scan:** X ve Y eksenlerini birleştirerek "S" şeklinde tam alan taraması.
- **360° Handling:** Pan eksenindeki 360/0 derece sınır geçişlerini akıllıca yönetir.

### ⚙️ **src/worker.py (İşlemci ve Asenkron İzleyici)**
Python `threading` kütüphanesini kullanarak ana programı dondurmadan arka planda çalışır.
- **Hareket Yönetimi:** `scanner.py` tarafından oluşturulan rotadaki her bir noktayı sırayla ziyaret eder.
- **Asenkron Veri Toplama (Pull):** Kameradan her 500ms'de bir konum bilgisini asenkron olarak çeker ve günceller.
- **Gecikme (Latency) Kontrolü:** Hareket sonrası yerleşme sürelerini (`post_move_delay`) milisaniyelik hassasiyetle yönetir.

### 🖥 **src/main.py (Sistem Yöneticisi)**
Projenin giriş noktasıdır. `config.json` dosyasındaki çoklu kamera yapılandırmasını okur, her kamera için bağımsız bir `worker` thread başlatır ve gelen telemetri verilerini merkezi bir ekranda konsolide eder.

---

## 📊 Teknik Avantajlar

- **Asenkron Yapı:** Her kamera bağımsız izlenir. Bir kamerada oluşabilecek ağ hatası diğerlerini etkilemez.
- **Dinamik Kalibrasyon:** `Transformer` modülü, ham veriyi gerçek zamanlı olarak fiziksel dereceye yüksek doğlukla dönüştürür.
- **Modülerlik:** Tarama zekası (`scanner`) ve fiziksel katman (`core`) ayrıştırılmıştır, yeni kamera markaları kolayca entegre edilebilir.
- **Standalone Çalışabilirlik:** Merkezi API bağımlılığı olmadan, yerel yapılandırma ile tam performans çalışabilir (Yönetici testi için optimize edilmiştir).

---

### 📝 Test ve Doğrulama Notu
Sistem çalıştırıldığında terminal ekranında akan Pan/Tilt dereceleri, kameranın web yönetim arayüzündeki fiziksel koordinatlar ile gerçek zamanlı olarak eşleşmektedir. Bu durum, telemetri verisinin SDK üzerinden başarıyla parse edildiğini ve anlamlandırıldığını kanıtlar.