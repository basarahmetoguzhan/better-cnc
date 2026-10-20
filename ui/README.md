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

`LinuxCncAdapter` makinede **hiç çalışmadı**, ama 2026-10-19'dan beri gerçek bir
LinuxCNC 2.9.9'a karşı simülatörde çalışıyor ([sim/](../sim/README.md): Mac'te
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

Makinede bilinmesi gereken bir yan etki: bağlantı pinlerini okumak için UI pinsiz
bir HAL bileşeni (`bettercnc-ui-<pid>`) açar. LinuxCNC kapanırken
`halcmd unload all` bu sürece SIGTERM gönderir ve UI de kapanır; LinuxCNC'den
sonra yeniden başlatın.

## Test

```bash
make test            # HER ŞEY: ui/tests + scripts/test_grbl2ini.py
make test-v          # ayrıntılı
make coverage        # satır kapsaması (stdlib, `coverage` paketi gerekmiyor)
```

808 test (49'u `scripts/test_grbl2ini.py`), hepsi geçiyor, beklenen başarısızlık
yok. Kapsama **%99.5**; `adapter.py` %99.1, `mdi.py` %100. `make coverage` paketteki
modülleri dizinden bulur, yeni bir modül listeden düşemez.

`reference/` (gitignore'daki LinuxCNC 2.9 klonu) varsa 20 doğruluk testi sahteyi
ve kodun dayandığı LinuxCNC olgularını kaynakla karşılaştırır (içerikle; atıflardaki
satır numaraları denetlenmez); yoksa bu testler atlanır,
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
INI'de strok değerleri var (LinuxCNC'nin bir şeye ihtiyacı var) ama geçici
olarak işaretli (`# ASSUMPTION:`, `PROVISIONAL`) — `[JOINT_n]` limitleri de,
`[AXIS_*]` limitleri de; UI bugün yalnızca `[JOINT_n]` limitlerini okuyor.
`iniload` bu işareti okuyor, UI "ölçülmedi" yazıyor. Strok ölçülüp işaret
kelimeleri (`ASSUMPTION:`, `PROVISIONAL`, `MUST BE MEASURED`) silindiğinde UI
**kod değişikliği olmadan** sayıları göstermeye başlar; `soft_limits` yeteneği,
KORUMA YOK uyarısı ve Z alt ucu metni de INI'yi izler.

Aynı kural LinuxCNC'den okunamayan durum için de geçerli: `LinuxCncAdapter`
LinuxCNC'nin durumunu okuyamazsa başlık **DURUM BİLİNMİYOR** der, DRO ve WCS `—`
gösterir, besleme düğmelerinden hiçbiri seçili görünmez; başlatma, jog ve MDI
kapanır. Okunamayan bir bağlantı pini
`—`'dir, `0` değil, ve bağlantıyı sağlıklı saydırmaz.

## Profil eklemek

`profiles.py`'de bir `MachineProfile` döndüren fonksiyon yaz, `PROFILES`'a
ekle. `validate_profile` her katalog yeteneğini beyan etmeye zorlar; bilmiyorsan
`todo(...)` kullan — `absent(...)` "baktım, yok" demektir.
