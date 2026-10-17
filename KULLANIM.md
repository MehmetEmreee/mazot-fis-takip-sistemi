# Mazot Fiş Kontrol — Kısa Kullanım

1. Windows'ta `BASLAT_ARAYUZ.bat`, Mac'te `BASLAT.command` dosyasına çift tıkla.
   (`.venv` klasörü olan bilgisayarda her zaman güncel kaynak kod çalışır;
   yalnız `.venv` yoksa `Mazot Fis Kontrol.exe` açılır.)
2. Tarayıcı otomatik açılınca fişleri kontrol et.
3. Fotoğrafları aşağıdaki üç yoldan biriyle ekle.
4. Alanları kontrol edip **Doğru, onayla** düğmesine bas.
5. İş bitince **Excel'e Aktar** ile dosya ve sayfayı seç.

## Fiş fotoğraflarını ekleme (çok sayıda fiş)

Üç yol da aynı kuyruğa gider; aynı fotoğraf ikinci kez eklenmez.

### 1. En hızlısı: LocalSend + gelen kutusu klasörü (önerilen)

Program açılınca proje klasöründe `gelen_fisler/` oluşur. Bu klasöre düşen
her fotoğraf birkaç saniye içinde kendiliğinden OCR kuyruğuna alınır.

Tek seferlik kurulum:

1. Bilgisayara ve telefona ücretsiz, açık kaynaklı **LocalSend** uygulamasını
   kur (https://localsend.org — Windows, Mac, iPhone, Android). İnternet
   gerekmez, aynı WiFi yeterli.
2. Bilgisayardaki LocalSend → Ayarlar → **Save to folder**: `gelen_fisler`
   klasörünü seç.
3. Telefonu bir kez Favoriler'e ekle ve Ayarlar → **Quick Save for
   "Favorites"** seçeneğini aç (her seferinde onay sormasın).

Kullanım: telefonda LocalSend → Galeri → bütün fişleri seç → bilgisayarı seç.
Sayı sınırı yok; telefon tarayıcısında olduğu gibi ekranın açık kalması
gerekmez.

Google Fotoğraflar / OneDrive / iCloud gibi bir senkron klasörünü veya USB ile
kopyalamayı da `gelen_fisler/` içine yönlendirebilirsin.

Klasördeki dosyalar silinmez, taşınır:
- `_islendi/<tarih>/` → kuyruğa alınanlar
- `_tekrar/` → daha önce yüklenmiş aynı fotoğraflar
- `_hatali/` → bozuk veya 2 dakika boyunca yarım kalan dosyalar

### 2. Bilgisayardan: Toplu Ekle

Üst çubuktaki **🗂 Toplu Ekle** düğmesi: fotoğrafları veya bütün klasörü
pencereye sürükle ya da **Dosya seç / Klasör seç** kullan. Pencerede gelen
kutusu klasörünün yolu ve **Klasörü aç** düğmesi de var.

### 3. Telefon tarayıcısından: Fiş Çek

Ekrandaki **Fiş Çek** sayfasını telefonda aç (adres program penceresinde
yazar). Galeriden yüzlerce fotoğraf tek seferde seçilebilir:
- Fotoğraflar göndermeden önce telefonda küçültülür (OCR kalitesi korunur,
  yükleme ~10 kat hızlanır). Gerekirse **Orijinal kalitede gönder** seç.
- Kopan yüklemeler kendiliğinden yeniden denenir.
- iPhone'da çok fotoğraf seçince telefon önce onları hazırlar; ekrandaki
  "hazırlanıyor" yazısını bekle. Yükleme bitene kadar ekranı açık tut.

iPhone'un HEIC fotoğrafları desteklenir; otomatik JPG'ye çevrilir.

## Partiler ve arşiv

Sol taraftaki **Fiş Partisi** listesinden aktif veya eski bir partiyi açabilirsin.
**+ Yeni** düğmesi mevcut partiyi Excel'e dokunmadan arşivler ve temiz bir
parti açar. OCR işlemi süren veya onaylanmamış fiş varsa veri kaybını önlemek
için parti kapanmaz.

Excel aktarımı her zaman manueldir: önce listeden aktarılacak partiyi seç,
sonra **Excel'e Aktar** düğmesinden hedef dosyayı, sayfayı ve başlangıç
satırını belirle. Yalnız onaylanmış ve daha önce aktarılmamış fişler eklenir.
Arşivlenen partilerde de Excel'e Aktar düğmesi kullanılabilir.

Her arşiv için dosya sayıları ve SHA-256 özetlerini içeren bir bütünlük
manifesti oluşturulur. Bir kaydın görseli eksikse arşivleme tamamlanmaz.

Excel geçmişi önerileri görünürse hepsini tek seferde doldurmak için
**Tümünü uygula** düğmesine bas. Hızlı onay ve sonraki fişe geçiş için
**Ctrl + Enter** kullanılabilir.

Program her açılışta önemli veri dosyalarının günlük yedeğini alır. Excel'e
yazmadan önce de hedef Excel dosyasının otomatik yedeğini oluşturur.

## İlk kurulum

- **Windows:** `KURULUM.bat` (Ollama modelini indirir, Python ortamını kurar).
- **Mac:** `KURULUM.command` (Ollama'yı https://ollama.com/download adresinden
  önce kur).

Model indirildikten sonra normal başlatma dosyasını kullan.

## EXE'yi güncelleme (Windows)

Depodaki `Mazot Fis Kontrol.exe` kaynak kod değiştikçe eskir. `.venv` olmayan
bir bilgisayara dağıtmadan önce `EXE_OLUSTUR.bat` ile yeniden oluştur.

## Kapatma

Programla birlikte açılan siyah (Mac'te Terminal) pencereyi kapatmak programı
durdurur.
