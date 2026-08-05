# 08 — Zhulong V2.0 Pinout (üretici diyagramından)

Kaynak: üreticinin Çince açıklamalı kart diyagramı, 2026-08-03.  
Durum: **OBSERVED** — üretici dokümanı, artık `readhmid` ile çapraz doğrulandı.

> Bu doküman [04-zhulong-board-hardware.md](04-zhulong-board-hardware.md)'deki
> tahminlerin yerini alır. 04'te INFERRED/UNVERIFIED işaretli olan çoğu satır
> artık kesinleşti.

---

> ## ⚠️ DÜZELTME (2026-08-05) — kart 7i96 değil, **7i92** türevi
>
> Bu doküman yazıldığında kartın bir **Mesa 7i96** türevi olduğunu varsayıyorduk.
> **Bu yanlıştı.** Kart kendini LBP16 üzerinden `7I92` diye tanıtıyor, IDROM
> `BoardName MESA7I92` diyor, ve `mesaflash --device 7i96` "no 7I96 board found"
> hatası veriyor.
>
> | | Varsayılan (7i96) | **Gerçek (7i92)** |
> |---|---|---|
> | IO pin sayısı | 51 (3 × 17) | **34 (2 × 17)** |
> | Konnektörler | P1, TB1, TB2, TB3 | **P2 (IO 0–16), P1 (IO 17–33)** |
> | HAL öneki | `hm2_7i96.0` | **`hm2_7i92.0`** |
> | Sürücü dalı | `hm2_eth.c:1319` | **`hm2_eth.c:1183`** |
> | Clock Low | 33 MHz (varsayım) | **100 MHz** |
>
> Kaynak: [readhmid-10.10.10.10-2026-08-05.txt](board-dumps/readhmid-10.10.10.10-2026-08-05.txt):4-6, :12, :15-18
>
> **Doğrulanmış tam pin haritası: [10-7i92-pinout-verified.md](10-7i92-pinout-verified.md)**
>
> Bu dokümandaki **konnektör ve terminal gözlemleri geçerli** — üretici
> diyagramından geliyorlar ve dökümlerle çelişmiyorlar. Aşağıdaki `hm2_7i96.0`
> örnekleri ve "Stok Mesa 7i96 ile karşılaştırma" bölümü düzeltildi.

---

## Konnektör haritası

### Üst kenar — 六轴输出 (altı eksen çıkışı)

| Blok | Terminaller | Not |
|---|---|---|
| `AXIS 0` … `AXIS 5` | **`GND` `STEP` `DIR` `5V`** | 6 blok, her biri 4 terminal |
| `0-10V` | analog çıkış | PWM0 → analog dönüştürücü çip → 0-10 V, VFD için |
| `CW` | **PIN 17** | Dijital çıkış, VFD ileri yön kontrolü |
| `OUT 1` | **PIN 18** | NMOS çıkış |
| `OUT 2` | **PIN 19** | NMOS çıkış |

### Alt kenar

| Blok | Terminaller | Not |
|---|---|---|
| Dijital girişler | **`PIN20` `PIN21` `PIN22` `PIN23` `PIN24` `GND` `5V`** | 5 yol izole giriş |
| `Encoder 0/1/2` | `A` `B` `Z` `GND` `5V` | 3 yol ABZ enkoder girişi |

### Sol kenar

| Port | İşlev |
|---|---|
| Smart Serial 1 (智能串口1) | Genişleme kartı bağlantısı |
| Smart Serial 0 (智能串口2) | Genişleme kartı bağlantısı |
| RJ45 (LED'li, en alt) | **Üst bilgisayarla haberleşme** — `hm2_eth` buraya bağlanır |

### Sağ kenar

| Giriş | İşlev |
|---|---|
| `24V 主电源输入` | **Ana besleme** — kartı besler |
| `外部设备24V供电` | **Harici cihaz beslemesi** — PIN18/PIN19 MOS çıkışlarını ve 0-10 V analog çıkışı besler |

#### 🛑 TUZAK: iki 24V terminali serigrafide AYNI etiketle yazılmış

Kartın üzerinde iki adet `24V GND` etiketli 2'li klemens var. **Etiketleri
birbirinin aynısı**, ama işlevleri tamamen farklı. Yanlışına bağlarsan:

- Kartta bir LED yanar (o hattın göstergesi), yani "besleme var" sanırsın
- **Ama FPGA konfigüre olmaz, Ethernet PHY hiç uyanmaz, link LED'leri yanmaz**
- Teşhisi zor, çünkü kart yarı canlı görünür

**2026-08-04'te bu yaşandı ve saatler kaybettirdi.**

**Ana beslemeyi nasıl ayırt edersin:** buck çevirici bileşenlerinin yanındaki
terminal ana beslemedir — büyük `470 16V` elektrolitik kondansatör ve `150`
işaretli SMD bobinin hemen yanında duran klemens. Diğeri (izole DC-DC ve MOS
çıkış sürücülerinin yanındaki) harici cihaz beslemesidir.

**Kontrol:** doğru terminale bağlıysan güç verdiğin an Ethernet jakının
LED'leri (sarı + yeşil) kablo takılıyken yanar. Yanmıyorsa yanlış terminaldesin.

**Ve unutma:** ikisini aynı kaynaktan besleyemezsin, üretici ortak şaseyi
yasaklıyor (bkz. aşağıdaki bölüm). Şu an sadece ana besleme bağlı olmalı.

---

## 🛑 KRİTİK: İki 24V beslemesi izole olmak ZORUNDA

Üretici notu, birebir çeviri:

> "Bu 24V güç girişi, PIN18 ve PIN19 MOS transistör çıkışları ile 0–10 V analog
> çıkış için besleme sağlar. Harici 24V mutlaka bağlanmalıdır, aksi hâlde MOS
> çıkışları ve 0–10 V analog çıkış çalışmaz."
>
> **"Dikkat: bu güç kaynağı ana güç kaynağından karşılıklı olarak izole olmalıdır,
> ikisi ortak şaseye bağlanamaz."**

Sonuçlar:

1. **OUT1, OUT2 ve 0-10V çıkışlarını kullanacaksan ikinci ve ayrı bir 24V
   güç kaynağı gerekir.** Tek kaynaktan ikisini birden besleyemezsin.
2. İki beslemenin GND'leri **birbirine bağlanmamalı.** Galvanik izolasyon
   kasıtlı — MOS çıkışları ve analog çıkış izole tarafta.
3. Test tezgahında spindle ve pompa olmadığı için şimdilik gerekmiyor. Ama
   **mermer makinesinde şart:** su pompası (OUT1) ve VFD hız kontrolü (0-10V)
   ikisi de o hattan besleniyor.

Bu, malzeme listesine ikinci bir 24V SMPS ekliyor.

---

## OUT1 / OUT2 — mermer makinesi için doğrudan kullanışlı

Üretici notu: *"18 ve 19 numaralı pinler MOS transistör çıkışıdır,
**24V su pompasını doğrudan sürebilir, anma gücü 50 watt.**"*

Yani su pompası için ayrı bir röle veya kontaktör gerekmiyor — pompa 50 W
altındaysa OUT1'e doğrudan bağlanır. Faz 8'deki su kilitlemesi bu sayede
tek kablo işi.

Mermer makinesindeki pompanın gücünü envanterde ölçmek gerekiyor; 50 W'ı
aşıyorsa araya röle girer.

---

## Global IO numaraları — kısmi harita

Üretici diyagramından doğrudan okunanlar:

| IO | İşlev |
|---:|---|
| 17 | `CW` — VFD ileri yön dijital çıkışı |
| 18 | `OUT 1` — NMOS |
| 19 | `OUT 2` — NMOS |
| 20–24 | 5 yol izole dijital giriş |

**Giriş etiketlerinin global IO numarası olduğu hipotezi DOĞRULANDI** — hem üretici
diyagramı hem de `printpd` döküm çıktısı aynı şeyi söylüyor. Serigrafideki
`20 21 22 23 24` sayıları terminal sırası değil, LinuxCNC IO numarası. Yani
HAL'de doğrudan (önek `hm2_7i92.0`, bkz. yukarıdaki düzeltme):

```
hm2_7i92.0.gpio.020.in
hm2_7i92.0.gpio.021.in
...
```

**Ek doğrulama (2026-08-05):** IO 17–24 pinlerinin pin tanımlayıcılarında
**hiç Secondary Tag yok** ([printpd:94-109](board-dumps/printpd-10.10.10.10-2026-08-05.txt)).
Yani bu pinler "bir modül talep edene kadar GPIO" değil — onları talep
*edebilecek* bir modül yok. Config string ne olursa olsun kalıcı olarak GPIO.
Bu, üreticinin 5 izole girişi + 3 çıkışının hiçbir config değişikliğiyle
kaybedilemeyeceği anlamına geliyor.

Step/dir, PWM ve enkoder pinlerinin IO numaraları diyagramda yazmıyordu —
`--readhmid` bunları tamamladı, [doc 10](10-7i92-pinout-verified.md)'da tam
tablo var. Özet: StepGen N = AXIS N konnektörü, IO 4–15; PWM = IO 16;
enkoderler IO 25–33; SSerial IO 0–3.

---

## Mesa 7i92 ile karşılaştırma — kart gerçekten bir 7i92

**Bu bölüm 2026-08-05'te tamamen değişti.** Önceden stok `7i96d` ile
karşılaştırıyordu; yanlış aileydi.

Önemli fark şu: **7i96 belirli bir I/O kartıdır** (sabit klemensler, SSR
çıkışları, TB1/TB2/TB3), ama **7i92 sadece iki DB25'lik bir breakout kartıdır**.
7i92'nin "stok modül kompozisyonu" diye tek bir şey yok — hangi bitfile
yüklendiyse ona göre değişir. pncconf'un kendi 7i92 kayıtları bunu gösteriyor
(`private_data.py:968, 979, 990, 1001`): aynı kart için 6 enkoder / 0 stepgen'den
2 enkoder / 10 stepgen'e kadar dört ayrı varyant listelenmiş.

Sürücünün gördüğü geometri açısından kart **birebir bir 7i92**:

| | Mesa 7i92 | Zhulong V2.0 | Sonuç |
|---|---|---|---|
| IO port sayısı | 2 | **2** | ✅ aynı |
| Port genişliği | 17 | **17** | ✅ aynı |
| Toplam IO | 34 | **34** | ✅ aynı |
| Konnektör adları | P2, P1 | **P2, P1** | ✅ aynı |
| FPGA | XC6SLX9 | **XC6SLX9, 9 KGates, 144 pin** | ✅ aynı |
| pncconf `MAXGPIO` | 34 (`private_data.py:969`) | **34** | ✅ aynı |

Kaynak: [readhmid:13-16](board-dumps/readhmid-10.10.10.10-2026-08-05.txt),
`hm2_eth.c:1187-1191`.

**Üreticinin kendi katkısı iki şey:**

1. **Kendi bitfile'ı** — 6 StepGen + 3 QCount + 1 PWM + 1 SSerial (2 kanal)
   karışımı ([readhmid:25-97](board-dumps/readhmid-10.10.10.10-2026-08-05.txt)).
   Bu, listelenen stok 7i92 varyantlarının hiçbirine denk gelmiyor, yani
   üretici kendi konfigürasyonunu derlemiş. Ama bu 7i92 için **normal** bir
   durum — kart bunun için var.
2. **DB25 yerine kendi ön yüzü** — vidalı klemensler, STEP/DIR için optokuplör
   sürücüler, girişler için 24 V izole optokuplörler, PWM'den 0-10 V analog
   dönüştürücü, NMOS çıkışlar.

**Yani "yalan söyleyen klon" değil.** Kart gerçekten 7i92 sınıfı bir cihaz,
üzerine üreticinin I/O ön yüzü eklenmiş. Sürücü açısından hiçbir uyumsuzluk yok:
IDROM ile sürücünün hardcoded geometrisi birebir uyuşuyor, `hm2_read_idrom()`
kontrolleri geçiyor, kart temiz yolda yükleniyor
([doc 02](02-board-bringup.md) Outcome A).

> **Yan not — 00'daki bir şüphe doğrulandı.** [00-upstream-reference-map.md](00-upstream-reference-map.md)
> §e'de pncconf'un 7i96 kayıtlarındaki `MAXGPIO = 34` değerinin 3 × 17 = 51 ile
> çelişmesine dikkat çekmiş ve "7i92 satırlarından kopyala-yapıştır artığı gibi
> görünüyor" demiştik. 7i92 için 34 **doğru** değer (2 × 17), yani o tahmin
> yerindeydi.

### Flashlama konusu

Önceki uyarı geçerliliğini koruyor ama gerekçesi netleşti: **hiçbir stok Mesa
bitfile'ını flashlama.** Ne 7i96'nınkini (yanlış aile, yanlış geometri) ne de
stok bir 7i92 bitfile'ını (doğru geometri ama üreticinin ön yüz devresine uymayan
pin atamaları — 0-10 V dönüştürücü PWM'in IO 16'da olmasını, optokuplörler
girişlerin IO 20-24'te olmasını bekliyor).

Zaten flashlamaya **gerek yok**: mevcut firmware ihtiyacımız olan her şeyi
sağlıyor (3+ StepGen, bol GPIO) ve temiz yükleniyor.

---

## Kapanan sorular

| # | Soru | Cevap |
|---|---|---|
| **B3** | AXIS blokları diferansiyel mi? | **HAYIR — tek uçlu.** `GND / STEP / DIR / 5V`. 4. terminal 5V beslemesi, diferansiyel çift değil |
| — | Giriş etiketleri global IO mu? | **EVET.** PIN20–PIN24 |
| — | OUT1/OUT2 ne? | NMOS, 24V/50W doğrudan yük sürebilir. PIN18/PIN19 |
| — | Hangi RJ45 Ethernet? | En alttaki, LED'li olan |

## Yeni açılan soru

| # | Soru | Neden önemli |
|---|---|---|
| **B7** | İkinci, izole 24V kaynağı temin edildi mi? | OUT1/OUT2 ve 0-10V onsuz çalışmaz. Mermer makinesinde su pompası ve VFD kontrolü buna bağlı |
