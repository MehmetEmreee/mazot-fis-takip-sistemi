# Mazot Fiş Kontrol — Kısa Kullanım

1. `Mazot Fis Kontrol.exe` dosyasına çift tıkla.
2. Tarayıcı otomatik açılınca fişleri kontrol et.
3. Telefondan fotoğraf yüklemek için ekranda **Fiş Çek** düğmesine bas.
4. Alanları kontrol edip **Doğru, onayla** düğmesine bas.
5. İş bitince **Excel'e Aktar** ile dosya ve sayfayı seç.

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

OCR modeli eksik uyarısı görülürse yalnızca bir kez `KURULUM.bat` dosyasını
çalıştır. Model indirildikten sonra normal başlatma dosyasını kullan.

## Kapatma

Programla birlikte açılan siyah pencereyi kapatmak programı durdurur.
