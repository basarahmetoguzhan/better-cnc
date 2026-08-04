# 08 — Zhulong V2.0 Pinout (üretici diyagramından)

Kaynak: üreticinin Çince açıklamalı kart diyagramı, 2026-08-03.  
Durum: **OBSERVED** — üretici dokümanı. `readhmid` ile çapraz doğrulanacak.

> Bu doküman [04-zhulong-board-hardware.md](04-zhulong-board-hardware.md)'deki
> tahminlerin yerini alır. 04'te INFERRED/UNVERIFIED işaretli olan çoğu satır
> artık kesinleşti.

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

**Giriş etiketlerinin global IO numarası olduğu hipotezi DOĞRULANDI.** Serigrafideki
`20 21 22 23 24` sayıları terminal sırası değil, LinuxCNC IO numarası. Yani
HAL'de doğrudan:

```
hm2_7i96.0.gpio.020.in
hm2_7i96.0.gpio.021.in
...
```

Step/dir, PWM ve enkoder pinlerinin IO numaraları diyagramda yazmıyor.
`--readhmid` çıktısı tamamlayacak.

---

## Stok Mesa 7i96 ile karşılaştırma — neden farklı bitfile

| | Stok Mesa `7i96d` | Zhulong V2.0 |
|---|---|---|
| StepGen | 5 | **6** |
| Enkoder | 1 | **3** |
| Smart Serial | 1 port | **2 port** |
| Dijital giriş | 11 (TB3) | **5** (PIN20-24) |
| Çıkış | 6 SSR | **2 NMOS** (PIN18/19) |
| Analog çıkış | — | **0-10V (PWM0)** |
| P1 / DB25 | 17 GPIO | **yok** |

[00-upstream-reference-map.md](00-upstream-reference-map.md)'deki çıkarım
doğrulandı: bu kart Mesa'nın bitfile'ını çalıştırmıyor. Stok 7i96 bitfile'ını
**kesinlikle flashlama** — pin atamaları bu kartın dış devresine uymaz.

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
