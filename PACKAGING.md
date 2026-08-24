# KraLYavuz Paketleme

PyInstaller her işletim sisteminde kendi yerel paketini üretir. Son kullanıcıya Python,
pip veya terminal gerekmez.

## Windows

1. Projeyi Windows makineye alın.
2. Paketleme ortamında `py -m pip install -r requirements-build.txt` çalıştırın.
3. `packaging\build_windows.ps1` betiğini çalıştırın.
4. Oluşan `dist\KraLYavuz` klasörünü bütün olarak dağıtın.

Kullanıcı `KraLYavuz.exe` dosyasını açar. Klasördeki `_internal` içeriği uygulamanın
Python çalışma zamanını ve bağımlılıklarını taşır; silinmemelidir.

## macOS

1. Paketleme ortamında `python3 -m pip install -r requirements-build.txt` çalıştırın.
2. `./packaging/build_macos.sh` çalıştırın.
3. Oluşan `dist/KraLYavuz.app` paketini imzalayıp notarize ederek dağıtın.

## Çalışma Verileri

Paketli uygulama ilk açılışta aşağıdaki kullanıcıya yazılabilir yolları hazırlar:

- Ayarlar: `~/.kralyavuz/config.json`
- Varsayılan sonuçlar: `~/KraLYavuz/results`

Kullanıcının arayüzden seçtiği screenshot klasörü JSON ayarında saklanmaya devam eder.
Opera GX uygulaması sistemde bulunamazsa veya VPN/CDP oturumu hazır değilse arayüz
ayrı bir hata mesajı gösterir.
