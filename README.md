# ptz-telemetry-standardizer
High-precision PTZ telemetry extraction and coordinate normalization service for thermal camera systems.




thermal-ptz-telemetry-engine/
├── docs/
│   └── PRD.md              <-- Şirketin istediği belge
├── src/
│   ├── __init__.py
│   ├── core.py             <-- SDK bağlantısı ve ana mantık
│   ├── transformer.py      <-- Ham veriyi dereceye çeviren matematiksel mantık
│   └── async_worker.py     <-- Asenkron okuma motoru
├── config/
│   └── settings.yaml       <-- Limitler ve ayarlar
├── tests/
│   └── test_telemetry.py
├── requirements.txt
└── main.py                 <-- Başlatıcı script

----------------------------------------------------


thermal-ptz-telemetry-engine/
├── .env                    <-- Hassas bilgiler (Senin lokalinde)
├── .env.example            <-- Müdürün dolduracağı örnek
├── requirements.txt        <-- Gerekli kütüphaneler
├── main.py                 <-- Başlatıcı (Çoklu kamera destekli)
├── config/
│   └── config.json         <-- Senin paylaştığın kamera listesi
├── src/
│   ├── __init__.py
│   ├── core.py             <-- ONVIF bağlantı yönetimi
│   ├── transformer.py      <-- Matematiksel dönüşümler (Senin formülün)
│   └── worker.py           <-- Asenkron veri okuma motoru
└── docs/
    ├── PRD.md              <-- Ürün Gereksinim Belgesi
    └── README.md           <-- Repo kullanım kılavuzu