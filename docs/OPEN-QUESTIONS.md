# Açık Sorular / Blocker Kaydı

Bu dosya, cevaplanmadan ilerlenemeyecek soruların kaydıdır. Her soru
cevaplandığında **buraya cevabı ve tarihi yazılır** — silinmez, üstü çizilir.
Karar geçmişini burada tutuyoruz.

Son güncelleme: 2026-11-14 (arayüzün P0 işi: B13 eklendi, B12'nin denemesi docs/12'de; P0 temizliği: B13'te sayfalama kararı; önceki 2026-11-12: test tezgahında Faz 1–8: B3/B4 düzeltildi, B8–B12 eklendi; önceki: 2026-10-18 doküman temizliği + mermer makinelerinin Zhulong'da kalması kararı, bkz. B7; cevaplar 2026-08-03 ve 2026-08-05'ten)

> **Not (2026-10-18):** Bu dosyadaki "Faz N" numaraları projenin ilk (mermer) planındaki
> fazlardır — `PLAN-BUGUN.md`'deki test tezgahı Faz 1–9 ile karıştırma. B8–B12 test tezgahının
> işleri; onlarda geçen faz numaraları "test tezgahı Faz N" diye yazılı.

---

## 🟡 B1 — Y ekseninde kaç motor var?

**Durum:** AÇIK — 2026-08-05'te 🔴'dan 🟡'ya düşürüldü (B2 kapandı, giriş kısıtı yok)  
**Kim cevaplar:** Oğuzhan, panoyu açıp sayarak  
**Neyi bloke ediyor:** mermer projesi Faz 5 (ilk config), Faz 6 (kablolama), Faz 7 (güvenlik zinciri), `PLAN-BUGUN.md`'deki test tezgahı fazları değil

Bu tek sayı giriş bütçesini belirliyor:

| | Home | E-stop | Takım sensörü | Toplam | Kartta var |
|---|---:|---:|---:|---:|---:|
| Tek motorlu Y | 3 | 1 | 1 | **5** | ~~5 ✅ sınırda~~ → 14 (2026-08-05, B2; 9'u encoder klemensinde, ön uç doğrulanmadı) |
| Çift motorlu Y (portal) | 4 | 1 | 1 | **6** | ~~5 ❌ **sığmıyor**~~ → 14 (2026-08-05, B2; 9'u encoder klemensinde, ön uç doğrulanmadı) |

**Çift Y çıkarsa ne değişir:**

- LinuxCNC tarafı sorun değil. `trivkins coordinates=XYYZ` ile 4 joint tanımlanır
  (joint 0 = X, 1 = Y sol, 2 = Y sağ, 3 = Z), 3 eksene eşlenir.
  Portal kareleme negatif `HOME_SEQUENCE` ile yapılır — aynı mutlak değere sahip
  joint'ler son hareketi senkron tamamlar
  ([ini-homing.adoc:262-267](../reference/linuxcnc/docs/src/config/ini-homing.adoc#L262-L267),
  [code-notes.adoc:1411](../reference/linuxcnc/docs/src/code/code-notes.adoc#L1411)).
- Zhulong step tarafı da sorun değil: 6 AXIS konnektörü var, 4 lazım.
- **~~Tek sıkışan yer~~ izole girişler.** Kartta 5 tane, DB25/P1 konnektörü yok
  (bkz. [04-zhulong-board-hardware.md](04-zhulong-board-hardware.md)),
  ~~genişleme yalnızca Smart Serial üzerinden.~~
  → *(2026-10-18 notu)* B2 2026-08-05'te kapandı: `num_encoders=0` ile IO 25–33
  (encoder klemensleri) de GPIO; klemenslerin ön uç devresi ohmmetreyle henüz doğrulanmadı.

~~**⚠ Dikkat:** Tek Y çıksa bile 5/5 doluyuz. Kapı switch'i, probe, spindle arıza
girişi, su debi sensörü için **sıfır boşluk.** B2 her hâlükârda cevaplanmalı.~~
→ *(2026-10-18 notu)* B2 2026-08-05'te kapandı: 5 izole + IO 25–33'te 9 GPIO = 14 giriş;
encoder klemenslerinin ön ucu ohmmetreyle henüz doğrulanmadı. Aşağıdaki GÜNCELLEME'ye bak.

---

### 🟢 GÜNCELLEME (2026-08-05) — giriş bütçesi kısıtı ORTADAN KALKTI

Yukarıdaki "5 ❌ sığmıyor" satırı **artık geçerli değil.** [B2](#-b2--encoder-pinleri-gpio-olarak-kullanılabiliyor-mu--kapandi-evet)
kapandı: `num_encoders=0` ile IO 25-33 tam GPIO oluyor, yani **5 değil 14 giriş**
var. Çift motorlu portal Y'nin istediği 6 giriş rahatça sığıyor, üstüne kapı
switch'i / probe / debi sensörü için de yer kalıyor.

| | Home | E-stop | Takım | Toplam | Kullanılabilir | Sonuç |
|---|---:|---:|---:|---:|---:|---|
| Tek motorlu Y | 3 | 1 | 1 | 5 | 14 | ✅ 9 boşta |
| Çift motorlu Y | 4 | 1 | 1 | 6 | 14 | ✅ 8 boşta |

Ayrıca kart 6 StepGen sağlıyor ve biz 3 kullanıyoruz, yani dördüncü eksen
(Y-sağ) için StepGen 3, IO 10/11'de firmware değişikliği olmadan hazır
([10-7i92-pinout-verified.md](10-7i92-pinout-verified.md)). *(2026-11-12 notu: test tezgahında
Y'de de iki motor çıktı; StepGen 3 orada Y'nin ikinci sürücüsünü sürüyor — `num_stepgens=4`,
hareketle doğrulandı. Mermer makinesi için B1 yine açık.)*

**Yani B1 artık bir tasarım kısıtı değil, sadece bir bilgi eksiği.** Cevabı hâlâ
gerekiyor — kaç joint tanımlayacağımızı, `trivkins coordinates=XYYZ` mi `XYZ` mi
kullanacağımızı ve kaç home switch'i kablolayacağımızı belirliyor — ama artık
"cevap yanlış çıkarsa yeniden tasarım" riski yok. **🔴 → 🟡 düşürülebilir**, yine de
Faz 5 öncesi cevaplanması gereken ilk soru.

Not: kart 7i96 değil 7i92 çıktı, yani toplam IO 51 değil 34. Bu yukarıdaki
sayıları düşürdü ama sonucu değiştirmedi.

**Cevap:** _(bekliyor — panoyu açıp saymak yeterli)_

---

## ✅ B2 — Encoder pinleri GPIO olarak kullanılabiliyor mu? — KAPANDI: **EVET**

**Durum:** ✅ **KAPANDI (2026-08-05)**  
**Cevaplayan:** `mesaflash --device 7i92 --addr 10.10.10.10 --print-pd`  
**Neyi bloke ediyor:** Faz 5, Faz 6  
**İlgili:** [02-board-bringup.md](02-board-bringup.md) §3.4 Q4

Açık çevrim step motorda encoder kullanmıyoruz. Kartta 3 encoder konnektörü var,
yani **9 FPGA pini boşta duruyor.** Bunları GPIO olarak kullanabilirsek giriş
sayısı 5'ten 14'e çıkar ve B1 sorunu kendiliğinden çözülür.

Karar tamamen bitfile'a ait. `hm2_print_pin_usage()` iki ayırt edilebilir form
basıyor ([pins.c:887-913](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/pins.c#L887-L913)):

```
IO Pin 0NN (...): IOPort                          → pin boşta, GPIO ✅
IO Pin 0NN (...): Encoder #0, pin A (Input)       → firmware kilitlemiş ❌
```

**⚠ Kısmi cevap uyarısı:** Bu FPGA seviyesinde bir gerçek. Pinin GPIO'ya
açılması, terminalin arkasındaki **analog ön uç** hakkında hiçbir şey söylemez.
Encoder girişleri diferansiyel alıcı ya da farklı bir seviye devresi
arkasındaysa, 24V endüktif sensörü doğrudan bağlayamayabiliriz. Ohmmetre ile
kontrol şart.

**Cevap (2026-08-05): EVET — 9 pin GPIO olarak kullanılabilir.**

`num_encoders=0` verildiğinde QCount modülünün üç örneği de instantiate edilmiyor
ve **IO 25-33 tam GPIO olarak açılıyor**
([hostmot2.adoc:400-401](../reference/linuxcnc/docs/src/drivers/hostmot2.adoc#L400-L401)):
"General Purpose I/O pins on the board which are not used by a module instance
are exported to HAL as 'full' GPIO pins."

Pin tanımlayıcıları encoder pinlerinde Secondary Tag = QCount gösteriyor, yani
firmware onları talep *edebiliyor* ama etmek zorunda değil:
[printpd:110-154](board-dumps/printpd-10.10.10.10-2026-08-05.txt).

**Giriş bütçesi — sorun tamamen çözüldü:**

| Kaynak | Pin | Not |
|---|---:|---|
| Kalıcı GPIO (IO 20-24) | 5 | Secondary Tag **hiç yok** — hiçbir config ile kaybedilemez |
| Encoder pinleri (IO 25-33) | 9 | `num_encoders=0` ile serbest |
| Kullanılmayan StepGen ~~3-5 (IO 10-15)~~ 4-5 (IO 12-15) | ~~6~~ 4 | ~~`num_stepgens=3`~~ `num_stepgens=4` ile serbest (2026-11-12: StepGen 3 test tezgahında Y'nin ikinci motorunda) |
| PWM (IO 16) | 1 | serbest, ama mermer makinede spindle için gerekecek |
| SSerial (IO 0-3) | 4 | `sserial_port_0=xxxxxxxx` ile serbest |
| **Toplam GPIO** | ~~**28**~~ **26** | 34 pinin 26'sı; 8'i dört stepgen'de (2026-11-12'ye kadar 28 / 6 / üç). Giriş sayısı (14) değişmedi |

**B1 (çift Y motoru) artık giriş bütçesi açısından sorun değil.** En kötü senaryo —
çift motorlu portal Y — 4 home + 1 e-stop + 1 takım sensörü = **6 giriş** istiyor.
Elimizde 5 kalıcı + 9 encoder = **14 giriş** var, artı dördüncü eksen için
StepGen 3 (IO 10/11) firmware değişikliği olmadan hazır (test tezgahında 2026-11-12'den beri Y'nin ikinci motorunda). Rahatça sığıyor.

Not: kart 7i96 değil **7i92** çıktı (34 IO, 51 değil) — bu, kullanılabilir pin
sayısını düşürdü ama yine de fazlasıyla yetiyor. Bkz.
[10-7i92-pinout-verified.md](10-7i92-pinout-verified.md).

**⚠ AÇIK KALAN KAVEAT — ön uç devresi doğrulanmadı.** Yukarıdaki uyarı aynen
geçerli. Bu FPGA seviyesinde bir gerçek; dökümler pin tanımlayıcılarını gösteriyor,
klemensin arkasındaki devreyi göstermiyor. **24 V sensörü doğrudan bağlamadan önce
ohmmetre ile kontrol et.** Bu B2'yi açık tutmuyor — soru "GPIO olarak
kullanılabilir mi" idi, FPGA tarafında cevap kesin EVET — ama kullanım öncesi
ayrı bir donanım kontrolü gerekiyor. Fiilen B6'nın encoder klemensleri için
tekrarı.

---

## ✅ B3 — AXIS blokları diferansiyel mi, tek uçlu mu? — KAPANDI

**Cevap (2026-08-03, üretici diyagramı):** **Tek uçlu.**
Her AXIS bloğunun 4 terminali: `GND` `STEP` `DIR` `5V`.
Dördüncü terminal diferansiyel çiftin eşi değil, **5V beslemesi** — optokuplörlü
sürücülerin anot ucunu beslemek için konmuş.

Sonuç: ~~ortak anot bağlantı kullanılıyor, `5V → CLK+/CW+`, `STEP → CLK−`,
`DIR → CW−`.~~ **Düzeltme (2026-11-12, makinede görüldü ve ölçüldü):** test tezgahı **ortak
katot** bağlı — `5V` boş, `CLK−`/`CW−` GND'de, `STEP → CLK+`, `DIR → CW+`; kartın çıkışları
3,3 V lojik ve bir çıkış yalnız bir sürücüyü sürebiliyor. "Tek uçlu" cevabı değişmedi. Detay:
[09-tb6560-drivers.md](09-tb6560-drivers.md).

---

## ✅ B4 — Sürücüler 5V mi 24V sinyal bekliyor? — KAPANDI (test tezgahı için)

**Cevap (2026-08-03):** Test tezgahında sürücüler **TB6560**. Girişleri
optokuplörlü ve ~~Zhulong'un 5V hattıyla~~ doğrudan sürülüyor — seviye çevirici
gerekmiyor. **Düzeltme (2026-11-12):** sürücüler kartın 5V hattıyla değil, **3,3 V lojik**
`STEP`/`DIR` çıkışlarıyla sürülüyor (ortak katot, 5V boş). Tek sürücüde `DIR` yüksek seviyesi
3,11 V; aynı çıkışa iki sürücü bağlıyken 2,43 V'a düştü ve iki sürücü de yönü göremedi →
**bir çıkış = bir sürücü** (Y'nin ikinci sürücüsü AXIS 3'e taşındı).

Detaylı zamanlama, akım ayarı ve 15 kHz hız tavanı:
[09-tb6560-drivers.md](09-tb6560-drivers.md).

> **Mermer makinesi için hâlâ AÇIK.** Oradaki sürücü kutuları farklı; etiket ve
> DIP fotoğrafı bekleniyor ([01-machine-survey.md](01-machine-survey.md) §2).

---

## 🔵 B5 — Bildirilen saat frekansı gerçek mi? — **DÜŞÜRÜLDÜ: blocker değil**

**Durum:** 🔵 **DÜŞÜK ÖNCELİK (2026-08-05'te blocker'dan düşürüldü)** — açık, ama
hiçbir şeyi bloke etmiyor  
**Kim cevaplar:** Osiloskop / mantık analizörü — **yazılımdan tespit edilemez**  
**Neyi bloke ediyor:** ~~Faz 9~~ → **hiçbir şey.** İyi hijyen, gereklilik değil.

`stepgen.c`'de `steplen`, `stepspace`, `dirsetup`, `dirhold` parametrelerinin
dördü de `clock_frequency` ile ölçekleniyor
([stepgen.c:348-385](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/stepgen.c#L348-L385)).
Bu değer firmware'in IDROM'undan geliyor
([hostmot2.c:780-784](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/hostmot2.c#L780-L784)) —
sürücü onu sorgulamadan kabul ediyor.

Firmware yanlış beyan ederse step darbeleri sessizce yanlış genişlikte üretilir:

```
steplen = 2000 ns, beyan edilen 33.33 MHz  → reg = 66 sayım
gerçek saat 50 MHz ise                     → 66 × 20 ns = 1320 ns
                                              istenenin %66'sı, hata mesajı yok
```

Karttaki 50 MHz kristal **tek başına şüphe sebebi değil** — o FPGA'nın giriş
saati, iç saatler DCM/PLL ile türetiliyor. Mesa'nın kendi 7i96'sı da 50 MHz
osilatörle çalışıp 33.33 MHz ClockLow bildiriyor.

En azından dmesg'in bastığı `clock_frequency: %d Hz` satırı kaydedilmeli
([stepgen.c:1225](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/stepgen.c#L1225)).
Devreye alma sırasında adım kaybı görülürse ilk şüpheli bu.

---

### Kısmi cevap (2026-08-05) — saat **100 MHz**, ve şüphe yanlış yerdeydi

Dökümler saati verdi:

```
Clock Low frequency:  100.0000 MHz     readhmid:17
Clock High frequency: 200.0000 MHz     readhmid:18
StepGen ClockFrequency: 100.000 MHz    readhmid:68   ← stepgen bunu kullanıyor
PWM     ClockFrequency: 200.000 MHz    readhmid:77   ← 200 MHz'deki tek modül
```

**Yukarıdaki 33.33 MHz hesabı geçersiz** — o rakam stok 7i96'dan geliyordu ve
kart 7i96 değil, 7i92 çıktı. Doğru sayılar:

```
steplen = 10000 ns, beyan edilen 100 MHz  → reg = 1000 sayım  (limit 16383)
dirsetup = 50000 ns                       → reg = 5000 sayım
100 MHz'de temsil edilebilir maksimum     → 163.8 us
```

Hepsi rahatça sığıyor; en büyük değerimiz aralığın %31'ini kullanıyor, taşma
ya da clamp uyarısı beklenmiyor.

**50 MHz kristal konusunda yukarıdaki not doğruydu ama yeterince güçlü değildi:**
kristal iç saat hakkında **hiçbir kanıt değil**. FPGA 100/200 MHz'i ondan PLL ile
türetiyor. Fotoğraftaki kristali şüphe sebebi saymak, kanıtı fazla okumaktı.

### Neden artık blocker değil

1. **IDROM kendi içinde tutarlı.** Geometri kontrolleri geçti
   (`hostmot2.c:692, :698, :708`) — IDROM'un beyan ettiği 2 × 17 = 34, sürücünün
   7i92 için hardcode ettiğiyle birebir uyuşuyor. Modül saatleri de tutarlı
   (biri hariç hepsi ClockLow, PWM ClockHigh).
2. **Kart Mesa'nın gerçek bir 7i92'si gibi davranıyor**, uydurma bir klon gibi
   değil. Firmware'in saat konusunda yalan söylemesi için bir sebep yok.
3. **Kayıt zaten alınıyor.** `test-rig.hal` `debug_modules=1 debug_idrom=1` ile
   yükleniyor, yani her açılışta dmesg'e `clock_frequency` satırı basılıyor.

**Kalan risk:** IDROM yalnızca firmware'in ne *iddia ettiğini* söyler. Bunu
kesinleştirmenin tek yolu hâlâ osiloskop. Ama artık "yapılmadan devam edilemez"
değil, "sıra gelince yapılır" kategorisinde.

**Yapılacak (öncelik: düşük):** [02-board-bringup.md](02-board-bringup.md) Step 7,
test tezgahında — ucuz makine, sonuçsuz hata. Adım kaybı semptomu görülürse
öncelik derhal yükselir.

**Cevap:** Saat 100 MHz (IDROM). Bağımsız fiziksel doğrulama bekliyor, blocker değil.

*(2026-11-12 notu: test tezgahı Faz 1–8'i bu zamanlamayla geçti. Görülen iki adım kaybının biri
X'in motor kablosundaki temassızlıktı — onarımdan sonra homing tekrarı 0,0017 mm —, diğeri Z'nin
program koşusu başına ~0,02 mm tek yönlü kayması; şüphe ivmede, B8. İvme denemesi kaymayı
gidermezse step zamanlaması ve bu madde öne alınır.)*

---

## 🟡 B6 — İzole girişler 24V sensörü doğrudan kabul ediyor mu?

**Durum:** AÇIK  
**Kim cevaplar:** Optokuplör parça numarasını okumak + ohmmetre  
**Neyi bloke ediyor:** Faz 6

Giriş bloğunun üstünde her giriş için `472` (4.7 kΩ) ve `511` (510 Ω) direnç
çifti görünüyor (OBSERVED). 4.7k seri direnç + optokuplör kombinasyonu 24V'ta
yaklaşık 5 mA sürer — tipik opto sürme akımı. Bu, girişlerin 24V için
tasarlandığına işaret ediyor ama **INFERRED**, doğrulanmadı.

**Cevap:** _(bekliyor)_

---

## 🔴 B7 — İkinci, izole 24V güç kaynağı

**Durum:** AÇIK — 2026-10-18 kararı: mermer makineleri Zhulong kartında kalıyor, yani B7 hâlâ gerekli.  
**Kim cevaplar:** Satın alma  
**Neyi bloke ediyor:** Faz 8 (spindle + su kilitlemesi). Test tezgahında gerekmiyor.

Üretici diyagramı kartta **iki ayrı 24V girişi** olduğunu gösteriyor:

1. `24V 主电源输入` — ana besleme, kartı çalıştırır
2. `外部设备24V供电` — harici cihaz beslemesi, **yalnızca** PIN18/PIN19 NMOS
   çıkışlarını ve 0-10V analog çıkışı besler

Üreticinin uyarısı birebir: *"Bu güç kaynağı ana güç kaynağından karşılıklı
olarak izole olmalıdır, ikisi ortak şaseye bağlanamaz."*

Sonuç: **OUT1, OUT2 ve 0-10V çıkışlarını kullanmak için ikinci ve galvanik
olarak izole bir 24V kaynak şart.** Tek kaynaktan ikisini birden besleyemezsin,
GND'leri de birleştiremezsin.

Mermer makinesinde her ikisi de lazım — su pompası OUT1'den, VFD hız kontrolü
0-10V'tan. Yani malzeme listesine ikinci bir SMPS giriyor.

İyi haber: OUT1/OUT2 NMOS çıkışları **24V/50W yükü doğrudan sürüyor**, yani
su pompası için ayrı röle gerekmiyor (pompa 50 W altındaysa — envanterde
ölçülecek).

Detay: [08-zhulong-pinout-confirmed.md](08-zhulong-pinout-confirmed.md).

**Cevap:** _(bekliyor)_

---

## 🟡 B8 — Z neden her program koşusunda tek yönde kayıyor?

**Durum:** AÇIK (2026-11-12, test tezgahı Faz 8)
**Kim cevaplar:** Makinede deneme
**Neyi bloke ediyor:** hiçbir şeyi — test tezgahı Faz 8 bu şerhle kabul edildi; ama uzun programda Z derinliği birikerek kayar

Test tezgahı Faz 8'de `nc_files/faz8-kare.ngc` dört kez hatasız koştu. Üç koşudan sonra
homing'in `joint.N.motor-offset`'leriyle bakılan adım kaybı: Y 0,0001, X 0,036, **Z 0,066 mm** —
Z tek yönde, koşu başına ~0,02 mm. Şüphe: Z için 50 mm/s² ivme yerçekimine karşı fazla.

**Yapılacak:** Z'nin `MAX_ACCELERATION`'ını (`[JOINT_2]` ve `[AXIS_Z]`) 25 mm/s²'ye indir,
Faz 8 kontrolünü tekrarla.

**Cevap:** _(bekliyor)_

---

## 🟡 B9 — Y'nin iki yanı kare kalıyor mu?

**Durum:** AÇIK — test tezgahı Faz 4'te ertelendi
**Kim cevaplar:** Makinede, portalın iki yanını ayrı ayrı ölçerek
**Neyi bloke ediyor:** hiçbir şeyi; kesimde köşegen hatası olarak görünür

Y'nin iki motoru tek eklem: `stepgen.01` (AXIS 1) ve `stepgen.03` (AXIS 3) aynı komutu alıyor,
ayrı referans ve kareleme yok. Faz 3 doğrulamasında 50,005 mm komutta sol 50,3, sağ 49,7 mm gitti.
İki yanın birbirine göre kayıp kaymadığı (racking) ölçülmedi.

**Cevap:** _(bekliyor)_

---

## 🟡 B10 — PC normal açılışta neden kararıyor?

**Durum:** AÇIK (2026-11-12)
**Kim cevaplar:** Makinede, açılış ve ekran kartı denemeleriyle
**Neyi bloke ediyor:** kiosk kurulumu — güç verilince kendiliğinden doğru açılması ([../deploy/README.md](../deploy/README.md))

Binadaki elektrik kesintisinden sonra PC yalnız GRUB failsafe modunda kullanılabilir açılıyor
(`noapic noapm nodma nomce nosmp nosplash vga=788`, tek CPU). Normal modda masaüstü arka planından
sonra ekran kararıyor; persistence (`/dev/sda2`, ext3) temiz. Monitör AMD iGPU'da (HDMI); PC'de
ayrıca bir NVIDIA GA102 var.

Failsafe'te servo-thread `tmax` ~0,52 ms, paket hatası 0 — önceki normal modun (çok çekirdek)
3,4 ms'sinden iyi. Tek çekirdekte Firefox açmak `tmax`'ı 1,25 ms'ye çıkardı (servo periyodu 1 ms).
Failsafe parametreleri gecikme için iyi, ama kiosk'un tarayıcısı tek çekirdekte risk; BIOS / gecikme
ayarı aynı işin parçası.

**Cevap:** _(bekliyor)_

---

## 🟡 B11 — USB Wi-Fi çubuğu SSH için yeterli mi?

**Durum:** AÇIK (2026-11-12)
**Kim cevaplar:** Daha iyi bir çubuk ya da kablolu LAN denemesi
**Neyi bloke ediyor:** Mac'ten güvenilir erişim (`deploy/machine.sh`)

Çubuk sık kopuyor. PC'nin ev ağındaki adresi 192.168.1.104'ten .107'ye değişti
(`deploy/machine.local`). Erişim noktası istemcilerin MAC adresini değiştirdiği için ARP PC'yi
tanıtamıyor; makine adı `debian` kullanılmalı. Kablolu LAN için kartın kullanmadığı bir Ethernet
portu ya da USB Ethernet adaptörü gerekir: PC'nin kendi portu karta ayrıldı
([../deploy/README.md](../deploy/README.md), `board-network`).

**Cevap:** _(bekliyor)_

---

## 🟡 B12 — Arayüz AXIS'in yanında yalnız izleme olarak nasıl çalışır?

**Durum:** AÇIK (2026-11-12) — Faz 9'da depoya `--observe` eklendi; makinede AXIS'in yanında denenince kapanır
**Kim cevaplar:** Arayüz geliştirmesi (`ui/`)
**Neyi bloke ediyor:** arayüzün kiosk'tan önce gerçek makinede, AXIS'le yan yana denenmesi

2026-11-12'de arayüz depo dışındaki bir izleyiciyle AXIS'in yanında yalnız izleme olarak
çalıştırıldı. O gün depodaki uygulama bunu kendisi yapamıyordu: `io_error` varken sayfa yüklenince
`state(STATE_ESTOP)` gönderiyordu (`_stop_on_link_fault`) ve NML hata kanalını boşaltıyordu
(`_drain_errors`); ikisi de AXIS'in yanında çalışmayla çelişiyordu. O gün görülen diğer kusurlar:
[11-ui-implementation.md](11-ui-implementation.md) "Known gaps".

**Cevap (kısmi, Faz 9):** `--observe` —
`python3 -m bettercnc.app --adapter linuxcnc --observe --ini FILE`
([../ui/README.md](../ui/README.md)). Hata kanalı hiç açılmıyor, LinuxCNC'nin mesajları AXIS'te
kalıyor; ekrandan yalnız yazılım acil stop'u gidiyor, diğer düğmeler kapalı ve her ekran salt
izleme olduğunu söylüyor. Kart bağlantısı koparsa giden acil stop bilerek kaldı: gerçek sürücüde
kablo kopunca yalnız `io_error` kalkıyor, LinuxCNC makineyi AÇIK tutuyor (DURUM, 2026-10-21
denetimi). Yalnız `fake_linuxcnc` ile test edildi (`ui/tests/test_observe_and_live_hal.py`);
makinede AXIS'in yanında henüz denenmedi.

**Hâlâ bekliyor (2026-11-14):** gözlem sonucu. Deneme makinede, AXIS'in yanında:
[12-operator-handover.md](12-operator-handover.md) "Mod A" ve PC listesinin 4–8. adımları.
B12 bu adımlar geçince kapanır.

---

## 🟡 B13 — Arayüzün seçilmiş ama ölçülmemiş sabitleri

**Durum:** AÇIK (2026-11-14)
**Kim cevaplar:** Makinede, [12-operator-handover.md](12-operator-handover.md)'deki PC listesi
**Neyi bloke ediyor:** arayüzün makineyi sürmesi değil (her sabitin yanlışlığı güvenli tarafta:
ret ya da ikinci basış), ama kiosk `enable` kararı bu ölçümlerden sonra

Her biri kodda "Chosen, not measured" yorumu ve docs/11'de yazılı. Simülatör gerçek zamanlı
değil, step ve takip hatası yok: oradaki geçişler bunları ölçmez. Kodda adıyla bulunur
(`grep -n AD ui/bettercnc/*.py`).

| Sabit | Değer | Yer | Yanlışsa belirti | PC listesi adımı |
|---|---|---|---|---|
| `WAIT_TIMEOUT_S` | 0,25 s | `adapter.py` | Tekrar denenince geçen ret (`MDI moduna geçilemedi`, `MANUAL moduna geçilemedi` gibi) | 11 |
| Acil stoptan çıkma süresi | 3 × `[EMCIO]CYCLE_TIME` = 0,3 s | `adapter.py` (`_estop_reset_deadline_s`) | `Yazılım sıfırlaması reddedildi — LinuxCNC 0.3 s içinde acil stoptan çıkmadı.` | 10, 11 |
| `FOLLOW_UP_WINDOW_S`, `MDI_RESULT_WINDOW_S` | 2 s, 2 s | `adapter.py` | MDI geçmişinde yanlış renk, kutuda genel hata | 17 |
| `HOMING_GAP_RECHECK_S` | 0,03 s | `adapter.py` | Home All'da eklemler arasında jog gider (`Can't jog any joint while homing.`) | 14 |
| `MOVING_VEL_EPS` | 1e-6 mm/s | `adapter.py` | Dururken `moving` true | 5 |
| `_MAX_ERRORS_PER_DRAIN` | 64 | `adapter.py` | — (bir boşaltmanın sınırı) | 20 |
| `RECOVERY_STEP_MM` | 3 mm | `motionrules.py` | Kurtarma sonrası makine yine durur, kart yine çıkar | 13 |
| `RECOVERY_INSIDE_MARGIN_MM`, `RECOVERY_MAX_STEP_MM` | 0,5 mm, 10 mm | `motionrules.py` | Referanslı eksende kurtarma reddi | 13 |
| `UNHOMED_MAX_STEP_MM`, `UNHOMED_NO_SWITCH_MAX_MM` | 10 mm, 1 mm (Z−) | `motionrules.py` | Referanssız jog fazla kısa/uzun | 12 |
| `AT_SOFT_LIMIT_MM`, `JOG_LIMIT_MARGIN_MM` | 0,001 mm, 0,0001 mm | `motionrules.py` | Limitte kırpılmış jog LinuxCNC'ce sessizce atılır | 16 |
| `CONFIRM_TIMEOUT_S`, `CONFIRM_REFUSED_S`, `CONFIRM_MIN_S` | 15 s, 5 s, 1 s | `app.py` | Onay soruyu okumadan düşer / çift dokunuş onaylar | 14 |
| `READY_MARGIN_MS` | 30 ms | `static/app.js` ([../deploy/README.md](../deploy/README.md) "Süreler") | «Evet» bir yenileme geç açılır | 14 |
| `REQUEST_TIMEOUT_S` | 10 s | `app.py` | — | — |
| `PAGE_SIZE`, `MAX_FILES`, `RACY_S`, `MAX_COUNT_BYTES` | 200 (sayfa), 5000 (tarama sınırı), 2 s, 32 MiB | `programs.py` | Sayfa uzun/kısa; 5000'den ötesi listelenmez; liste geç güncellenir; büyük dosyada satır sayısı yok | 18 |
| `MAX_TEXT_BYTES`, `LINE_TEXT_MAX` | 1 MiB, 120 | `programs.py` | Satır metni görünmez / kesik | 18 |
| `HALCMD_TIMEOUT_S`, `LINK_SETTLE_S`, `LOOP_STALL_S` | 2 s, 0,5 s, 2 s | `adapter.py` ([../deploy/README.md](../deploy/README.md) "Süreler") | Sıfırlama takılır / bağlantı yanlış "kurulamadı" / kiosk LinuxCNC'yi gereksiz yeniden başlatır | 20, 21 |

Sabit olmayan, ölçülecek bir yük: **tek çekirdekte 1 Hz yoklama.** PC şimdi failsafe'te tek
çekirdekle açılıyor (B10); servo periyodu 1 ms, failsafe'te `tmax` ~0,52 ms, Firefox'la 1,25 ms.
Arayüzün sayfası açıkken (Program ekranında poll başına fazladan bir `os.stat`) `tmax` ve CPU
PC listesinin 3. ve 7. adımlarında ölçülür; `tmax` 1 ms'ye yaklaşırsa kiosk'a geçilmez, plan
WP19 (hafif yenileme) öne alınır.

Ayrıca açık, ölçümle değil kararla (duraklamış bir MDI satırının arkasına yazılan MDI satırı
artık reddediliyor, son P0 incelemesi: «Devam» onu da koştururdu): programlarda M0/M1 kullanılacak
mı. ~~Listede en yeni 200 dosyadan ötesi yüklenemiyor (sayfalama gerekir mi?)~~ — **karar verildi
(P0 temizliği, 2026-11-14): sayfalama.** Program listesi sayfa başına 200 dosya gösterir
(«Önceki» / «Sonraki», `toplam N dosya`), listelenen her dosya yüklenir; tarama 5000 dosyada durur
ve ötesini söyler. Sayfa tarayıcının görünüm durumudur, LinuxCNC'ye bir şey gitmez, observe
modunda da çalışır.

**Cevap:** _(bekliyor — PC listesi)_

---

## Kapanmış sorular

| # | Soru | Cevap | Tarih |
|---|---|---|---|
| B2 | Encoder pinleri GPIO olur mu? | Evet: `num_encoders=0` ile IO 25-33 GPIO (FPGA tarafı); klemens ön ucu ohmmetreyle doğrulanmadı | 2026-08-05 |
| B3 | AXIS blokları diferansiyel mi? | Hayır, tek uçlu: `GND/STEP/DIR/5V`. Test tezgahı ortak katot bağlı, `5V` boş (2026-11-12) | 2026-08-03 |
| B4 | Sürücüler 5V mi 24V mi? | Test tezgahı: TB6560, optokuplörlü, ~~5V~~ kartın 3,3 V çıkışıyla doğrudan; bir çıkış = bir sürücü (düzeltme 2026-11-12). Mermer makinesi için hâlâ açık | 2026-08-03 / 2026-11-12 |
| — | Giriş etiketleri (20-24) global IO numarası mı? | Evet. Üretici diyagramında `PIN20`…`PIN24` | 2026-08-03 |
| — | Test tezgahı firmware'i ne? | GRBL 1.1 (varsayılan parmak izinden) | 2026-08-03 |
| — | Hangi RJ45 Ethernet? | En alttaki, LED'li olan | 2026-08-03 |
| — | Test tezgahında Y'de kaç motor? | İki (iki TB6560); ikinci sürücü AXIS 3 / `stepgen.03`'e taşındı | 2026-11-12 |
| — | Test tezgahının Y DIR arızası nerede? | Kabloda değil: iki sürücü tek 3,3 V çıkıştaydı, çıkış iki optokuplörü süremiyordu | 2026-11-12 |
