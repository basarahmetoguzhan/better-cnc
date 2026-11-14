# ui/ — Operator arayüzü

`design/prototype-source.jsx`'in düzenini ve görsel dilini alıp, **gerçek donanım
verileriyle** yeniden uygulanmış hâli.

Tam gerekçe ve prototipte düzeltilen her gerçeğin listesi:
**[docs/11-ui-implementation.md](../docs/11-ui-implementation.md)**

---

## Çalıştırma

```bash
make ui              # test tezgahı profili, http://127.0.0.1:8080/
make ui-marble       # mermer profili — neyin TODO olduğunu görmek için
make dump SCREEN=makine > /tmp/makine.html   # sunucusuz tek ekran
```

`--dump`'a bilinmeyen ya da boş bir ekran adı vermek (`make dump SCREEN=makina`)
kullanım hatasıdır: komut 2 koduyla çıkar, sayfa yazmaz ve geçerli ekranları
listeler (kontrol, program, probing, takimlar, alarmlar, makine). `make dump`'ta
`SCREEN` verilmezse ya da boş verilirse (`SCREEN=`) `makine` kullanılır; boş ad
hatası yalnızca doğrudan `--dump ""` için geçerli.

`make` olmadan: paket kurulu değil, `ui/` import yoluna eklenmeli.

```bash
PYTHONPATH=ui python3 -m bettercnc.app --profile test-rig --port 8080
```

Varsayılan adaptör `MockAdapter`: makineye bağlı **değil**, hiçbir komut göndermez.
Bunu yalnızca stderr'deki başlangıç satırı söylüyor; ekranda SİMÜLASYON
işareti yok, bağlantı sayaçları ve DRO ölçülmüş gibi görünür (açık eksik).
Çalışan bir LinuxCNC'ye bağlanmak için `--adapter linuxcnc`:

```bash
PYTHONPATH=ui python3 -m bettercnc.app --profile test-rig --adapter linuxcnc
```

Başlangıç satırı hangi adaptörün **gerçekten** kurulduğuna bakarak yazılır
(`app.adapter_banner`): `mock adapter — makineye bağlı DEĞİL` ya da
`LinuxCncAdapter — komutlar LinuxCNC'ye GİDİYOR`.

`LinuxCncAdapter` makinede yalnız 2026-11-12'de, AXIS'in yanında salt izleme olarak
(depo dışı bir izleyiciyle) çalıştı; komutları makineyi hiç sürmedi. 2026-10-19'dan
beri gerçek bir LinuxCNC 2.9.9'a karşı simülatörde çalışıyor ([sim/](../sim/README.md): Mac'te
Docker, arm64). docs/11'deki ilk-oturum kontrol listesinin hepsi orada geçti. Kart,
gerçek zamanlama ve motorlar simülatörde yok: zamanlama değerleri ve sınırları
(0.25 s bekleme sınırı, 3 × `[EMCIO]CYCLE_TIME` estop sıfırlama süresi, hata
kanalından okuma başına 64 mesaj; bir de sunucunun 10 s istek zaman aşımı) makinede
hâlâ ölçülmedi. Açık eksiklerin listesi:
[docs/11, "Known gaps and next steps"](../docs/11-ui-implementation.md#known-gaps-and-next-steps).

`--adapter linuxcnc` ile arayüz **LinuxCNC'den sonra** ve LinuxCNC'yi başlatan
kullanıcıyla (aynı `HOME`) başlatılmalı. Değilse Türkçe bir mesajla 1 koduyla çıkar:
LinuxCNC'nin Python modülü takım tablosunu `$HOME/.tool.mmap`'te arar, ilk poll'da
bulamazsa o süreç bir daha durum okuyamaz (docs/11, "Found in `sim/`").

Makinede bilinmesi gereken bir yan etki: bağlantı pinlerini, switch girişlerini ve
step zamanlamasını okumak için UI pinsiz bir HAL bileşeni (`bettercnc-ui-<pid>`)
açar. LinuxCNC kapanırken `halcmd unload all` bu sürece SIGTERM gönderir ve UI de
kapanır; LinuxCNC'den sonra yeniden başlatın.

Makineyi AXIS kullanırken arayüz yanında yalnız izleme için açılır (`--observe`;
`FILE`, LinuxCNC'nin başlatıldığı INI):

```bash
PYTHONPATH=ui python3 -m bettercnc.app --adapter linuxcnc --observe --ini FILE
```

Bu modda hata kanalı açılmaz (LinuxCNC'nin mesajları AXIS'te kalır), ekrandan
yalnız yazılım acil stop'u gider, diğer düğmeler kapalıdır ve her ekran salt
izleme olduğunu söyler; kart bağlantısı koparsa giden acil stop bilerek kalır.
Başlangıç satırına `SALT İZLEME` eklenir. Yalnız sahte LinuxCNC'yle test edildi,
makinede AXIS'in yanında henüz denenmedi (docs/OPEN-QUESTIONS.md B12).

Makinede iki çalışma biçimi var, adım adım
[docs/12-operator-handover.md](../docs/12-operator-handover.md)'de: **Mod A** — AXIS sürer,
arayüz yanında `--observe` ile (`--ini configs/test-rig/test-rig.ini --port 8080`); **Mod B** —
AXIS kapalı, arayüz LinuxCNC'nin `[DISPLAY]` programı (`deploy/kiosk.sh clean-shm`,
`python3 deploy/make_kiosk_ini.py configs/test-rig/test-rig.ini`,
`linuxcnc configs/test-rig/test-rig-kiosk.ini`, tarayıcıda http://127.0.0.1:8080/). AXIS
açıkken arayüzü `--observe`'suz başlatmayın: komut gönderir ve hata kanalını AXIS'ten çalar;
bunu engelleyen bir kilit henüz yok (plan WP12). Aynı belgede makinede yapılacak denemelerin
listesi var.

Her ekranın sağ kenarında **ACİL STOP** (kırmızı) ve **DURDUR** (sarı) durur: 1 Hz
yenilenen bölgenin dışında, basışta (pointerdown) gider, oturum kilidini beklemez.
Klavyede Esc = Durdur (AXIS'teki gibi), Pause = Acil stop; MDI alanında da çalışır.
DURDUR = LinuxCNC abort: programı, MDI'ı, jog'u ve referans almayı durdurur (referans
alınıyorsa o eksenler referanssız kalır). Bu ekrandaki acil stop yalnız yazılımdır,
motor gücünü kesmez: gerçek durdurma güç anahtarı. Salt izlemede DURDUR kapalıdır
(Durdur AXIS'ten), ACİL STOP açıktır. Ayrıntı: docs/11, "J. The stop rail and DURDUR".

Her ekranın üstünde **mesaj bandı** durur, ekran kayarken de görünür: solda son
mesaj (gri bilgi, sarı ret), sağda kırmızı **hata kutusu** — saatiyle, Türkçe
(LinuxCNC'nin bilinen hataları çevrilir: ekseni, switch'i ve yumuşak limit
aralığını adlandırır; altında LinuxCNC'nin kendi metni), «Tamam»a basılana kadar
kalır, sonraki mesaj onu silmez. Bir boşaltmada en özgül LinuxCNC hatası kutuya
girer ("invalid params in linear command" değil, ondan önceki "would exceed").
MDI geçmişinde her satır sonucuyla görünür: gönderildi (sarı), tamamlandı
(yeşil), reddedildi ya da yarıda kaldı (kırmızı). «Tamam» yereldir, LinuxCNC'ye
bir şey göndermez; salt izlemede de çalışır. Ayrıntı: docs/11, "K. The message band".

Bir eksen **limit switch'inde** kaldıysa (makine kapalı) Kontrol'de limit kartı çıkar:
basılı switch'in adı ve pini, altında **«Limitten kurtar: X+ yönünde 3 mm»** — ne
hareket edeceğini söyler. Tek basış: LinuxCNC'nin limit aşmasını verir, makineyi açar,
ekseni switch'ten uzağa tek jog'la çıkarır; limit koruması o jog bitince kendiliğinden
geri gelir (bu sürede başlıkta kırmızı `LİMİT KORUMASI GEÇİCİ KAPALI`). Switch basılıyken
«Yazılım sıfırla» makineyi açmaz, Home All reddedilir, switch'e doğru jog gönderilmez.
Ayrıntı: docs/11, "L. Limit recovery".

**Jog** tıklaması LinuxCNC'ye bir şey gitmeden önce tek bir kuralla karara bağlanır
(iki adaptör de, Jog kartının düğmeleri de bu kuralı izler). Referans alma, program ya
da MDI sürerken (duraklatılmışken de) jog yoktur ve Jog kartı gerekçesiyle kapalıdır;
referans alma bozulmadan biter. Hareket sürerken ikinci tık reddedilir, çünkü artış
hedefe eklenirdi. Referanssız eksende adım en çok 10 mm, Z− yönünde (alt switch yok)
en çok 1 mm; 50 mm çipi referans alınana kadar kapalıdır. Referanslı eksende fazla
uzun adım yumuşak limite kadar kısaltılır ve bu söylenir (`Z+ en çok 2.000 mm
gidebilir … kısaltıldı`); limitteki eksen o yöne gitmez. Kapalı her düğmenin gerekçesi
pedin altında yazıyla durur. Mesaj satırı yalnız jog gerçekten gittiyse `Jog X+1 mm.`
der. Ayrıntı: docs/11, "G. Jog".

**Referans alma** kartında her eksenin çipinde kendi düğmesi var: «Z referansla»
yalnız o ekseni referanslar (AXIS'teki Machine ▸ Homing gibi); «Tümünü referansla»
Z → Y → X. Makine kapalıyken, bir switch basılıyken (switch'in adı ve «Limitten
kurtar» yazar), program ya da MDI sürerken veya referans alma sürerken düğmeler
gerekçesiyle kapalıdır; referans alma sürerken kart «Durdur»un ne yaptığını da söyler.
Bir eksen referanslıysa referansı yeniden almadan önce sorulur, Z referanssızken X ya da
Y tek başına referanslanacaksa da: ekranın üstünde **onay bandı** ne hareket edeceğini
yazar (`Yeniden referans: Z üst switch'ine (Z+), sonra Y ön uç switch'ine (Y−), sonra X
sol uç switch'ine (X−) gidecek — yol açık mı?`), «Evet» ya da «Vazgeç». «Evet»e 15 s
içinde basılmazsa, arada başka bir komut verilirse ya da Durdur/acil stop gelirse onay
düşer ve hiçbir şey gönderilmez. «Evet» sorudan sonraki ilk 1 s kapalıdır (`Önce soruyu
okuyun — «Evet» 1 s sonra açılır.`): düğmeye çift dokunuşun ikincisi, düğmenin yerinde
açılan banttaki «Evet»e denk gelip soruyu cevaplamasın; tam 1 s'de açılır (bant kalan
süreyi söyler, sayfa o an bir kez yeniler). İlk referansta (hiçbir eksen
referanslı değilken) sorulmaz. Negatif HOME_SEQUENCE'li bir eklem tek başına referans
alınmaz (LinuxCNC grubunu birlikte referanslar; düğme gerekçesiyle hangi eksenlerin
gideceğini yazar); INI yoksa sıra uydurulmaz: `INI yok — sıra bilinmiyor`.
Ayrıntı: docs/11, "M. Homing one axis, the question first, visible gates".

**Program** ekranı INI'nin `[DISPLAY]PROGRAM_PREFIX` dizinindeki G-code dosyalarını
(`.ngc`, `.nc`, `.tap`; gizliler ve alt dizinler hariç, dizin dışını gösteren symlink'ler
hariç) en yeni önce listeler; her satırda boyut, tarih ve «Yükle». Liste sayfa başına
200 dosya gösterir («Önceki» / «Sonraki», altında `toplam N dosya`; sayfa tarayıcının
görünüm durumudur, LinuxCNC'ye bir şey gitmez); 5000 dosyadan ötesi taranmaz. Dizin INI'nin
bulunduğu dizine göre çözülür, LinuxCNC'nin çözdüğü gibi (rig'de `nc_files/`).
Tarayıcıdan yalnız dosyanın adı gelir; sunucu adı kendi listesinde arar ve LinuxCNC'ye
mutlak yolu kendisi verir — liste dışı ad, yol, `..` hiçbir komut göndermez ve mesaj
satırı `Program listede yok: …` der. Yüklü dosya `YÜKLÜ` işaretlidir; altında tam yolu
ve satır sayısı. «Yeniden yükle» yüklü dosyayı tekrar açar, «Listeyi yenile» listeyi
yeniden okur (liste dizinin değişme zamanına göre önbellekte; 1 Hz yoklama dizini
taramaz). Program çalışırken, duraklatılmışken, MDI ya da referans alma sürerken veya
makine hareket ederken yükleme gerekçesiyle kapalıdır; makine kapalıyken yüklenebilir
(AXIS gibi). «Başlat» kapalıysa nedeni yazıyla durur; durum kapısı program yüklenmemişse
`PROGRAM YOK` der.

Koşu (plan WP09): başlatma satırında duruma göre tek birincil düğme — boştayken
«Başlat», program (ya da MDI satırı) koşarken «Duraklat», duraklamışken (M0/M1 dahil)
«Devam»; yanında «Adım» ve «Durdur». Duraklat konumu korur; Devam kaldığı yerden sürer;
Adım boştayken programı başında duraklamış başlatır, duraklamışken bir satır ilerletir.
Duraklamış program `HAZIR` görünmez: başlık `DURAKLATILDI` (son basılan «Adım»sa, tek
adımın hareketi sürerken `TEK ADIM`), koşarken `ÇALIŞIYOR · satır N`, MDI satırı `MDI
YÜRÜTÜLÜYOR`; yalnız «Adım»la sonuna kadar gelinmiş programda LinuxCNC duraklatılmış
kalır: başlık `PROGRAM SONU · DURDUR`, yalnız «Durdur» temizler. Adımlardan sonra
«Devam» önce bir duraklatma gönderir (LinuxCNC'nin `File ended …` hatası çıkmasın).
Duraklamışken MDI paneli kapalıdır; «Başlat»
duraklamışken `«Devam»` der. Koşarken `Satır N / M · %P` ve o satırın metni görünür.
Bitişte mesaj satırı `Program bitti: <ad>`, Durdur'dan sonra `Program durduruldu: <ad>`,
LinuxCNC hatasıyla `Program hatayla durdu: <ad>` der (hata kutuda kalır). Ayrıntı:
docs/11, "H. Programs, feed override and coordinate systems".

## Test

```bash
make test            # HER ŞEY: ui/tests + scripts/test_grbl2ini.py
make test-v          # ayrıntılı
make coverage        # satır kapsaması (stdlib, `coverage` paketi gerekmiyor)
```

1574 test (2026-11-14; 49'u `scripts/test_grbl2ini.py`), hepsi geçiyor, beklenen
başarısızlık yok. Simülatörde uçtan uca: `make sim-e2e` (`sim/README.md`). Kapsama **%99.6**; `adapter.py` %99.0; `iniload.py`, `mdi.py`, `messages.py`, `motionrules.py`, `profiles.py`, `programs.py`, `render.py` ve `viewmodel.py` %100. `make coverage`
paketteki modülleri dizinden bulur, yeni bir modül listeden düşemez.

`reference/` (gitignore'daki LinuxCNC 2.9 klonu) varsa 70 doğruluk testi sahteyi
ve kodun dayandığı LinuxCNC olgularını kaynakla karşılaştırır (içerikle; atıflardaki
satır numaraları denetlenmez, yalnız `adapter.py` docstring'indeki stat tablosununkiler
denetlenir); yoksa bu testler atlanır,
gerisi çalışır.

## Neden saf stdlib

Hedef makine bir LinuxCNC live USB — üzerinde hiçbir üçüncü parti Python paketi
yok ve bir hatayla teşhis arasına `pip install` koymak istemiyoruz. `linuxcnc`
modülü de PyPI'da olmayan, makineyle gelen bir CPython modülü. Dolayısıyla:

- **Python + stdlib**, başka hiçbir şey
- **Sunucu tarafı HTML** — `unittest` ile string olarak doğrulanabiliyor,
  tarayıcı veya DOM gerekmiyor
- `unittest` (pytest kurulu olan bir makinede `pytest` de çalışır)

## Katmanlar

```
app.py          stdlib http.server, --dump, --adapter, --boot-url (kiosk)
bootpage.py     kiosk'un başlatma ekranı — linuxcnc'ye dokunmaz (deploy/README.md)
browserwatch.py kiosk'un tarayıcı bekçisi: kapanan ya da ölen sayfayı yeniden açar,
                Chromium'un kendi sayfalarını (çöken sekme…) bizim sayfamızla değiştirir
render.py       view model -> HTML          (sunucu tarafı)
viewmodel.py    (profil, config, state) -> dict     ← testlerin çoğu burada
adapter.py      LinuxCNC sınırı: MachineAdapter / MockAdapter / LinuxCncAdapter
mdi.py          MDI kapısı — iki adaptörün ortak kuralı
motionrules.py  basılı limit switch'leri ve «Limitten kurtar»ın planı — ortak kural
messages.py     LinuxCNC hata metinlerinin Türkçesi (hata kutusu)
state.py        estop, homing, link health, "durum bilinmiyor"
configmodel.py  eksen/zamanlama değerleri — INI'den
profiles.py     test-rig ve marble tanımları
capabilities.py yetenek kataloğu
iniload.py      INI ayrıştırma (LinuxCNC'nin okuduğu gibi) + "ölçülmedi" tespiti
```

`configmodel.py`'nin üstünde **hiçbir donanım sayısı sabit yazılı değil** —
hepsi `configs/*/**.ini`'ye ya da `profiles.py`'deki bir `docs/` atfına
dayanıyor. Açık bir istisna var: INI'si olmayan profilde (marble) adaptörler
eklemleri X/Y/Z, HOME_SEQUENCE 0/1/2 ve HOME_SEARCH_VEL 0 olarak uyduruyor
(docs/11, Known gaps).

`iniload` INI'yi LinuxCNC'nin kendi okuyucusu gibi okur: tekrarlanan bölüm ya da
anahtarda **ilki** geçerlidir, sondaki ters eğik çizgi satırı bir sonrakiyle
birleştirir, değeri olmayan anahtar (`KEY =`) bulunamamış sayılır; aynı anahtarın sonraki
satırı da okunmaz.

## Tasarımın iki kuralı

**1. Olmayan yetenek gizlenmez, devre dışı görünür.**
Render `CAPABILITY_CATALOGUE`'u dolaşır, profilin ne beyan ettiğini değil. Bir
yeteneği beyan etmeyi unutan profil `validate_profile`'da **hata verir** —
sessizce kaybolmaz. (`docs/MERMER-GECIS.md:43-45`)

**2. Ölçülmemiş sayı gösterilmez.**
INI bir değeri tahmin diye işaretlerse (`# ASSUMPTION:`, `PROVISIONAL`,
`MUST BE MEASURED`) `iniload` bu işareti okur, UI sayı yerine "ölçülmedi" yazar;
işaret kelimeleri silinince UI **kod değişikliği olmadan** sayıları göstermeye
başlar. `soft_limits` yeteneği, KORUMA YOK uyarısı ve Z alt ucu metni de INI'yi
izler. Örnek, artık tarihçe: test-rig.ini'de strok 2026-11-12'ye kadar geçici
işaretliydi (LinuxCNC'nin bir sayıya ihtiyacı var) — `[JOINT_n]` limitleri de,
`[AXIS_*]` limitleri de; UI yalnızca `[JOINT_n]` limitlerini okuyor. Faz 4'te
ölçüldü, işaretler kalktı ve UI 3–399 / 3–387 / −96,5–−3 mm'yi kod değişikliği
olmadan gösteriyor.

Aynı kural LinuxCNC'den okunamayan durum için de geçerli: `LinuxCncAdapter`
LinuxCNC'nin durumunu okuyamazsa başlık **DURUM BİLİNMİYOR** der, DRO ve WCS `—`
gösterir, besleme düğmelerinden hiçbiri seçili görünmez; başlatma, jog ve MDI
kapanır. Okunamayan bir bağlantı pini
`—`'dir, `0` değil, ve bağlantıyı sağlıklı saydırmaz.

## Profil eklemek

`profiles.py`'de bir `MachineProfile` döndüren fonksiyon yaz, `PROFILES`'a
ekle. `validate_profile` her katalog yeteneğini beyan etmeye zorlar; bilmiyorsan
`todo(...)` kullan — `absent(...)` "baktım, yok" demektir.
