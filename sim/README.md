# sim/ — Mac'te LinuxCNC simülatörü

Gerçek **LinuxCNC 2.9.9**, Mac'te Docker içinde, **arm64** üzerinde çalışır.
Raspberry Pi 4'ün mimarisi de bu. Kart, motor ve gerçek zamanlı çekirdek yok;
bunların yerine aşağıdaki taklitler var. Amaç, yazılım tarafını (arayüz,
`LinuxCncAdapter`, INI/HAL mantığı) makineye ve USB'ye gitmeden denemek.

```bash
make sim-build     # bir kez: imajı kur (~2.9 GB, birkaç dakika)
make sim           # LinuxCNC + arayüz → http://localhost:8770/
make sim-stop      # durdur
make sim-test      # bütün test paketi imajın içinde (arm64, Python 3.13, gerçek linuxcnc modülü)
make sim-e2e       # uçtan uca: arayüz HTTP ile sürülür, LinuxCNC'nin kendisi denetlenir (aşağıda)
```

Diğer komutlar `sim/sim.sh` içinde: `logs` (arayüz çıktısı), `log` (LinuxCNC
logu), `halcmd ...`, `press`/`release`/`switches` (limit switch canlandırma,
aşağıda), `shell`. Repo konteynere **salt okunur** bağlanır; Mac'te
yapılan değişiklik konteynerde anında görünür. INI, HAL ya da arayüz kodu
değişince `make sim-stop && make sim` ile yeniden başlat.

## Ne gerçek, ne taklit

| Gerçek | Taklit / yok |
|---|---|
| LinuxCNC 2.9.9 (linuxcnc.org deposu, imzası doğrulanmış) | Kart yok: `hm2_eth` yüklenmez |
| Rig'in INI'si, birebir: `configs/test-rig/test-rig.ini` (yalnız `[HAL]HALFILE`, `[DISPLAY]DISPLAY` ve `[DISPLAY]PROGRAM_PREFIX` değişir, `make_sim_ini.py`; sonuncusu mutlak `<repo>/nc_files`: sim INI'si `/tmp/better-cnc-sim`'de, oradan rig'in `../../nc_files/`'ı var olmayan `/nc_files` olurdu) | Step yok: `motor-pos-cmd` doğrudan `motor-pos-fb`'ye döner → **SCALE ve step zamanlaması denenmez**; takip hatası ve yavaşlama gerçekçi değil |
| motmod, trivkins, homing, yumuşak limitler, estop_latch zinciri, task/io | Beş switch `sim_home_switch` ile; konumlar rig'in 2026-11-12'de ölçülen strokundan (Faz 4): her max switch, rig'deki gibi `MAX_LIMIT`'in ~3,7 mm ötesinde |
| Arayüz `--adapter linuxcnc` ile, gerçek NML ve HAL üzerinden | Bağlantı pinleri (`hm2_7i92.0.*`) `hm2_link_stub.py`'den: değerler okuma değil. Switch girişleri (`hm2_7i92.0.gpio.020`–`024.in_not`) aynı adla, ama sürücüdeki gibi yazan değil, switch sinyaline bağlı okuyan pin: Alarmlar'daki Durum sütunu simüle switch'i gösterir |
| Python 3.13, Debian 13, arm64 | Gerçek zamanlı çekirdek yok → **gecikme/zamanlama ölçümü anlamsız** |

Switch'ler rig'deki gibi bağlı (`configs/test-rig/test-rig.hal`, 2026-11-12'den beri,
Faz 5): X'in minimumu sol (`gpio.021`), Y'ninki ön (`gpio.023`; Y+ = arka), Z'de yalnız
üst switch var. Rig'de Y'nin ikinci motoru `stepgen.03`'te (AXIS 3); simülatörde step
üreteci olmadığı için karşılığı yok. Ölçümler:
`docs/board-dumps/rig-measurements-2026-11-12.json`.

## Arıza canlandırma

```bash
sim/sim.sh halcmd setp hm2_7i92.0.sim-link-down 1   # kablo çekildi: gerçek sürücü gibi
                                                    # io_error kalkar, makine AÇIK kalır
sim/sim.sh halcmd setp hm2_7i92.0.sim-link-down 0   # kablo takıldı; sonra arayüzden «Yazılım sıfırla»
sim/sim.sh halcmd sets watchdog TRUE                  # elle ısırma: has_bit → LinuxCNC ESTOP
sim/sim.sh halcmd setp hm2_7i92.0.packet-error-total 7
sim/sim.sh halcmd setp hm2_7i92.0.io_error 1
```

Limit switch'ler için: aşağıdaki «Limit switch canlandırma».

Arayüzün «Yazılım sıfırla»sı kart bağlantısı bayraklarını kendisi `halcmd` ile indirir
(rig'de de öyle): önce `io_error`, sonra sürücünün o an bulduğu ısırık (`has_bit`).
Kablo hâlâ takılı değilse `io_error` hemen geri gelir ve sıfırlama "bağlantı kurulamadı"
der. Taklidin ilk sürümü `has_bit`'i tutuyordu — gerçek sürücünün tersi (2026-10-21
denetimi); gerçek davranış `sim/hm2_link_stub.py`nin başında.

## Limit switch canlandırma

```bash
sim/sim.sh press Z+      # Z üst switch'i, eksenin durduğu yerde basılı (X+ X- Y+ Y- Z+; Z- yok)
sim/sim.sh switches      # beş switch: konum, motor konumu, basılı mı
sim/sim.sh release       # hepsi test-rig-sim.hal'deki yerine (ya da: release Z+)
```

Makine AÇIKKEN basılan switch LinuxCNC'yi durdurur (`joint N on limit switch error`).
Eksen switch'te kaldıkça «Yazılım sıfırla» makineyi açmaz; Kontrol ekranında limit kartı
çıkar ve «Limitten kurtar: X+ yönünde 3 mm» tek basışta ekseni switch'ten çıkarır (plan
WP05: limit aşma, makineyi aç, switch'ten uzağa tek jog). Simülatörün yolu da durur:
`release` switch'i yerine koyar, sonra «Yazılım sıfırla».

Neden switch'in yeri taşınıyor: `halcmd sets x-max-limit TRUE` "already has writer(s)"
ile, `halcmd setp joint.0.pos-lim-sw-in TRUE` "is connected to a signal" ile başarısız
olur (sinyalin tek yazarı `sim_home_switch`). Doğru yol switch'in `home-pos`'unu taşımak;
`press` ve uçtan uca testlerin `place_switch` / `place_switch_ahead`'i bunu yapar
(`sim/e2e/harness.py`).

- **İşaret tuzağı.** `home-pos` MOTOR biriminde ve işareti switch'in tarafını seçer:
  `>= 0` pozitif taraf (o değerde ve üstünde basılı), `< 0` negatif taraf (o değerde ve
  altında basılı) (`reference/linuxcnc/src/hal/components/sim_home_switch.comp:29-59`).
  0'ı geçen bir değer switch'i taşımaz, ters çevirir: hiç basılmaz ya da hep basılı
  kalır. `press` ve `place_switch` bunu reddeder.
- **Motor = makine + `joint.N.motor-offset`.** Referanstan önce offset 0; referanstan
  sonra X ve Y'de −9,9, Z'de +4,9 civarı (`sim/sim.sh halcmd getp joint.0.motor-offset`).
- **`press`'in yapamadıkları:** taze sim'de bütün motorlar 0'da, orada X+, Y+ ve Z+
  basılır ama X− ve Y− basılamaz (negatif taraf `home-pos < 0` ister): önce ekseni 1 mm
  eksi yönde sürün. Referanstan sonra Z motoru −0,1'de (makine −5): Z+ için önce Z'yi
  motor 0'ın üstüne sürün. `press` reddedince ne yapılacağını yazar. Tetik noktası eksenin
  0,5 mm gerisine konur; eksen 0,5 + 0,1 mm (histerezis) uzaklaşınca switch bırakır.

## Uçtan uca testler (make sim-e2e)

```bash
make sim-e2e                        # sim/e2e/test_*.py, hepsi
make sim-e2e E2E=test_baseline.py   # tek modül
```

Her modül için taze bir LinuxCNC: `sim/e2e/run.sh` simülatörü kendi adıyla ve
portuyla açar (`better-cnc-e2e`, 8771 — geliştiricinin 8770'teki `make sim`'ine
dokunmaz), modülü konteynerde `python3 -m unittest` ile koşar, ne olursa olsun
kapatır. Konum, referans bayrakları, motor offset'leri ve taşınmış switch'ler
çalışan LinuxCNC'de kalır; bu yüzden modüller LinuxCNC paylaşmaz (modül başına
~4 s açma/kapama).

`sim/e2e/harness.py` konteynerin içinde çalışır (stdlib + `linuxcnc` + `halcmd`):

- Makineye yalnız arayüz üzerinden komut gider: `POST /api/action`,
  `Content-Type: application/json`, tarayıcının gönderdiği gibi.
- `linuxcnc.command()` ve `linuxcnc.error_channel()` asla açılmaz: hata kanalı bir
  kuyruktur, okunan mesaj arayüzün kaydına düşmez (`test_deploy` bunu sabitler).
- Doğrulama LinuxCNC'nin kendisinden: `linuxcnc.stat()` ve `halcmd getp`.
- HAL'de yalnız simülatörün taklit ettiği yerler değişir: switch'lerin `home-pos`'u ve
  `hm2_7i92.0.sim-link-down`. Repo salt okunur: harness dosya yazmaz.
- LinuxCNC'nin hataları POST'tan sonra gelir, aynı reddin raporları bile ayrı
  yoklamalara düşebilir; mesaj satırında son yazan kazanır, hata kutusu
  (`status.last_error`, WP04) en özgül hatayı tutar: iddialar alarm kaydı
  yoklanarak yapılır (`wait_alarm`), POST cevabına güvenilmez.
- Hareketin bitişi: `current_vel == 0` ve hedef konum (teleop jogda `stat.inpos` hep
  True kalır). Makine kapalıyken `current_vel` bayat kalabilir: teleop jog sırasında
  limit tetiklenince ESTOP_RESET'te 3,000 mm/s okundu. "Hareket" = ON ve
  `current_vel > 0` (`moving()`).
- Zaman aşımları simülatörde ölçülenin ~2 katı (Home All 14 s → 40 s).

Modüller:

| Modül | İçerik | Bugün |
|---|---|---|
| `test_baseline.py` | program dizini, Alarmlar'da switch durumu, sıfırla+AÇ, Home All Z→Y→X (5/5/−5), her eksende ±1 mm jog, limit içi/dışı MDI, besleme %50, MDI hareketinde acil stop (≤0,5 s, referanslar korunur), kablo çekilince tek GET'te ESTOP_RESET, kablo geri + sıfırla → referanslar silinir | geçer, ~25 s (açma/kapama ile ~30 s) |
| `test_stop.py` | DURDUR: Home All sırasında (Z ararken) → referanslar silinir, Home All yeniden tamamlanır; 50 mm X jog ve MDI `G0 X200` POST'tan 0,5 s içinde durur (istek sürerken ölçülür), interp IDLE; ray (ACİL STOP + DURDUR) altı ekranda, `#app` dışında; boşta Durdur → `Durduruldu.` | geçer, ~17 s (açma/kapama ile ~21 s) |
| `test_messages.py` | hata kutusu: referanssız X sol switch'e sürülünce `X sol uç switch'ine çarptı — makine durdu. «Limitten kurtar» ile switch'ten çıkın.` (sonraki yoklamalarda kalır; durma da aynı metinle kayda, `komut bu ekrandan gelmedi` değil); `G0 Z0` → LinuxCNC AÇIK kalır, hiçbir eksen kımıldamaz (`linuxcnc.stat`), `Z hedefi yumuşak limit dışında (izinli −96.5…−3 …)`, satır `rejected`, genel `invalid params` gelince kutu değişmez; `G0 X50` → `done`; «Tamam» yalnız kendi kimliğiyle siler | geçer, ~24 s (açma/kapama ile ~28 s) |
| `test_limits.py` | X sol / Z üst / X sağ / Y ön switch'e sürülen referanssız eksen: hata kutusunda switch'in adı, «Yazılım sıfırla» ON göndermez ve kurtarmayı söyler, kart «Limitten kurtar: X+ yönünde 3 mm» der, tek basış makineyi açar (cevap sayfasında `LİMİT KORUMASI GEÇİCİ KAPALI` rozeti) ve ekseni 3 mm çıkarır, jog bitince limit aşma kalkar, switch serbest, makine AÇIK kalır; Y ön basılıyken Home All reddedilir; referanslı makinede teleop jog'la trip → 3 mm geri, yumuşak limitlerin içinde, sonraki jog yine teleop; Home All'da Y'nin ve Z'nin latch anında DURDUR → eksen switch'te, makine kapalı → kart ve kurtarma; kurtarma jogu switch'teyken acil stop → LinuxCNC limit aşmayı açık gösterse de «Yazılım sıfırla» ON göndermez (motion maskeyi açılışta düşürür, ON hemen düşerdi) ve tek basış yine kurtarır | geçer, ~53 s (açma/kapama ile ~57 s) |
| `test_jog_rules.py` | referanssızken 50 mm adım ve 10 mm Z− reddedilir, hiçbir eksen kımıldamaz (10 mm X ve 1 mm Z− gider; 50 mm çipi ve Z− düğmesi gerekçeyle kapalı); Home All'da Y switch'ini ararken jog tıklaması gönderilmeden reddedilir, referans alma normal biter (`linuxcnc.stat`: hepsi referanslı, 5/5/−5, ON, hiçbir switch basılı değil, LinuxCNC'den jog reddi yok); referanslı Z −5'te 10 mm Z+ → 2 mm'ye kısaltılır, Z −3'e gider ve mesaj söyler (`stat.position` teleop planlayıcının hedefi `axis.z.teleop-pos-cmd` ile aynı), limitteyken Z+ reddedilir; X 5'te 10 mm X− → X 3; 10 mm jog sürerken ikinci tık reddedilir, toplam 10 mm | geçer (WP06), ~32 s (açma/kapama ile ~35 s) |
| `test_homing.py` | Z referanssızken tek başına X sorulur (`Z referanssız: takım aşağıdaysa X hareketi çarpabilir. …`), «Vazgeç» hiçbir şeyi oynatmaz; tek başına Z sorulmaz ve yalnız Z referanslanır (`[0, 0, 1]`, Z −5, X ve Y yerinde, motion serbest modda; referans sürerken kart kapalı: `Referans alma sürüyor — durdurmak için «Durdur».`); Z referanslıyken Y sorulmaz (`[0, 1, 1]`); Y yeniden sorulur, sorunun hemen ardından gelen «Evet» (çift dokunuşun ikincisi) hiçbir şeyi oynatmaz ve soru durur, 1 s sonraki «Evet» yeniden referanslar; eksenler referanslıyken Home All sorulur (hareketler sırasıyla), «Evet»ten önce hiçbir şey oynamaz, yanlış jetonlu «Evet» hiçbir şey göndermez ve bunu söyler, onaylı Home All 5/5/−5'te biter (motion teleop'ta); Home All'da Z ararken DURDUR → bütün eksenler referanssız, hiçbir switch basılı değil; ardından Home All sorulmadan tamamlanır | geçer (WP07), ~45 s (açma/kapama ile ~48 s) |
| `test_program.py` | Program ekranı `/repo/nc_files`'ı listeler (`faz8-kare.ngc`, etkin); «Yükle» → `stat.file` `/repo/nc_files/faz8-kare.ngc` (mutlak yol), ekranda `YÜKLÜ`, tam yol ve satır sayısı; `../../etc/passwd`, `/etc/passwd`, `../nc_files/faz8-kare.ngc` → `stat.file` değişmez, `Program listede yok`; «Yeniden yükle»; referanslı makinede koşu (liste kapalı, gerekçesiyle; koşarken yükleme reddedilir) sonuna kadar (~47 s), ikinci koşuyu Durdur POST'tan 0,2 s içinde durdurur | geçer (WP08), ~67 s (açma/kapama ile ~70 s) |
| `test_run.py` | referanslı makinede `faz8-kare.ngc` koşarken başlık `ÇALIŞIYOR · satır N`, birincil düğme «Duraklat»; «Duraklat» → 1 s içinde `current_vel` 0, interp PAUSED, başlık `DURAKLATILDI`, birincil «Devam», konum 2 s sabit, «Başlat» reddedilir ve hiçbir şey değişmez; «Devam» → hareket sürer; yeniden duraklatıp «Adım» → tam bir satır (`motion_line` + 1, ekrandaki `line` de) ve yine durur; «Devam» → sonuna kadar, mesaj `Program bitti`, `program.line` hiç geri gitmez ve ilerler, ekran yine «Başlat»; boştayken «Adım» → program AUTO'da başında duraklamış başlar, hiçbir şey kımıldamaz, DURDUR → `Program durduruldu: faz8-kare.ngc`; MDI `G1 X40 F300` duraklatılınca yazılan `G0 Y60` reddedilir (`queued_mdi_commands` 0), «Devam» yalnız X 40'a gider; «Adım», «Adım», «Devam» → `Program bitti`, `File ended …` hatası yok (arayüz devamdan önce duraklatır); yalnız «Adım» ile sonuna kadar → `stat.paused` 1 kalır, başlık `PROGRAM SONU · DURDUR`, «Devam» kapalı ve hiçbir şey kımıldamaz, DURDUR temizler | geçer (WP09, son P0 incelemesi, P0 temizliği), ~171 s (açma/kapama ile ~175 s) |
| `test_state.py` | `GET /api/state`'in `motion` bölümü LinuxCNC'ye karşı: makine kapalı ve referanssızken X sol switch'i → eklem 0 `min`; Home All sonrası modlar ve komut konumu; 10 mm jogda `moving` (komut konumu gerçek konumun önünde: 3 mm/s'de 0,009 mm); MDI hareketinde `mdi_running`; makine X 20'deyken `G92 X10` → iş X 10 (= makine X − 10) ve `g92_active`, `G92.1` → yine eşit | geçer, ~19 s (açma/kapama ile ~23 s) |

Hepsi birden ~6,5 dk (2026-11-14, plan WP09'dan sonra: 391 s; state 4, stop 5,
messages 4, limits 9, jog_rules 6, homing 6, program 5 ve run 2 test geçti; son P0 incelemesinden sonra run 3, beklenen
hata kalmadı; baseline'ın 11. adımı o koşuda bilinen biçimde zaman aşımına düştü, tek
başına hemen ardından geçti). P0 temizliğinden sonra run 5 test, ~95 s daha uzun: hepsi birden
~8 dk bekleniyor. Baseline'ın son adımı (kablo geri + sıfırla) tam
koşunun ilk modülüyken ara sıra zaman aşımına düşüyor (docs/11, "End to end"); tek başına
koşunca geçiyor. `harness.py`'de arayüzün okuyucuları: `dro()` (Kontrol DRO'su sayı
olarak), `motion()` (görünüm modelinin `motion` bölümü) ve `last_error_text()` (hata
kutusu, metin olarak).

Bilinen boşluklar `@unittest.expectedFailure` ile işaretli; her iş paketi yalnız kendi
dosyasındaki işaretleri kaldırır. Beklenmedik başarı (`unexpected success`) koşuyu
kırmızı yapar: boşluğu kapatan paket işaretini kaldırmak zorunda kalır. Senaryonun
bugün çalışan hazırlığı (switch'i yerleştirip eksene trip ettirmek gibi) `setUp`'tadır:
orada bir arıza beklenen hata sayılmaz, hata olarak görünür.

Bu testler yazılırken görülen: Home All sırasında (Y switch'ini ararken) bir jog
tıklaması homing'i kesti ve Y'yi switch'inin **üstünde**, makine kapalı bıraktı.
WP06'dan beri arayüz bu tıklamayı LinuxCNC'ye bir şey göndermeden reddediyor
(`test_jog_rules.py` B); kural kapatılınca aynı test eski arızayı yeniden üretiyor. Testlerin `tearDown`'u switch'te kalan ekseni önce
arayüzün «Limitten kurtar»ıyla kurtarır (WP05); olmazsa simülatörün yoluyla
(`rescue_from_switches`: switch'i kaydır, aç, ekseni içeri sür, switch'i yerine koy),
hangisi olduysa stderr'e yazar.

Simülatörün sınırları, uçtan uca testler için de geçerli: step üreteci yok (Y'nin
ikinci motoru, yavaşlama ve takip hatası denenmez), çekirdek gerçek zamanlı değil
(zaman toleransları ×2), konteyner durumu kalıcı (modül başına açma/kapama).

## 2026-10-19'da bulunanlar

- `test-rig.ini`'de `[EMC]VERSION = 1.0` vardı. LinuxCNC 2.9 `1.1` dışındaki her
  değerde `update_ini`'yi çalıştırır (`scripts/linuxcnc.in:489-501`): ekran yoksa
  açılmaz, ekran varsa onay penceresi çıkarır ve onaylanırsa INI'yi **yerinde
  değiştirip** yanına `test-rig.old/` yedeği açar. Repoda `1.1` yapıldı. Makinedeki
  laptop kopyası bu yüzden repodan farklı olabilir (`docs/DURUM.md` sonundaki not).
- Arayüzde iki tarayıcı hatası (MDI alanına tıklamak boş komut gönderiyordu; MDI
  odaktayken sayfa her saniye MDI'ya geri kayıyordu, acil stop butonuna
  çıkılamıyordu) — düzeltildi, `docs/11` "What is NOT covered" madde 3.
- Üç açık daha bulundu ve 2026-10-20'de düzeltildi: %150 butonu, HAL kaynaklı
  e-stop'ta eski kalan mesaj, LinuxCNC'den önce başlatılan arayüz — `docs/11`
  "Found in `sim/` on 2026-10-19 and fixed".
- Arayüz, LinuxCNC'den **sonra** ve LinuxCNC'yi başlatan kullanıcıyla (aynı
  `HOME`) başlatılmalı: LinuxCNC'nin Python modülü takım tablosunu
  `$HOME/.tool.mmap`'te arar; ilk denemede bulamazsa o süreç bir daha durum
  okuyamaz. Pi 4'te servis kurarken de geçerli.
- `docs/11` "Next steps"teki makine kontrol listesinin hepsi burada geçti; rig'de
  yine yapılacak, önce zamanlama.

## Neden Docker, neden 2.9.9

Mac'te Docker Desktop zaten kurulu ve Linux'u arm64 olarak yerel hızda çalıştırıyor;
ayrı bir VM'e gerek yok. Laptop'taki live USB 2.9.10, ama 2.9.10'un arm64 paketi
yok (linuxcnc.org'da en yenisi 2.9.9, Debian 13'ün kendi paketi 2.9.4; 2026-10-19).
Aradaki fark hata düzeltmeleri. Docker'ın varsayılanında olmayan tek yetki
`IPC_OWNER`: setuid root çalışan `rtapi_app`'in, başka kullanıcının açtığı ortak
belleğe erişmesi için gerekli.
