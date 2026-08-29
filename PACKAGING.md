# KraLYavuz Paketleme

PyInstaller her işletim sisteminde kendi yerel paketini üretir. Son kullanıcıya Python,
pip veya terminal gerekmez.

## Windows

1. Projeyi Windows makineye alın.
2. Paketleme ortamında `py -m pip install -r requirements-build.txt` çalıştırın.
3. `packaging\build_windows.ps1` betiğini çalıştırın.
4. `packaging\create_windows_zip.ps1` betiğini çalıştırın.
5. Oluşan `dist\KraLYavuz_Windows.zip` dosyasını dağıtın.

Kullanıcı `KraLYavuz.exe` dosyasını açar. Klasördeki `_internal` içeriği uygulamanın
Python çalışma zamanını ve bağımlılıklarını taşır; silinmemelidir. Aynı klasördeki
`KraLYavuzUpdater.exe`, onaylanan GitHub Release güncellemelerini ana uygulama
kapandıktan sonra güvenli biçimde uygular.

## GitHub Release

1. `kralyavuz/version.py` içindeki `APP_VERSION` değerini güncelleyin.
2. Windows'ta `packaging\build_windows.ps1` çalıştırın.
3. `packaging\create_windows_zip.ps1` çalıştırın.
4. GitHub'da `v<APP_VERSION>` etiketiyle published release oluşturun.
5. Release asset olarak adı değiştirilmeden `KraLYavuz_Windows.zip` yükleyin.

Uygulama yalnız `MeteLabs/KraLYavuz` deposunun latest published release kaydını ve
yalnız tam adı `KraLYavuz_Windows.zip` olan asset'i kabul eder. Güncelleme kontrolü
paketli Windows uygulamasının başlangıcında bir kez yapılır ve kullanıcı onayı olmadan
dosya değiştirilmez.

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
