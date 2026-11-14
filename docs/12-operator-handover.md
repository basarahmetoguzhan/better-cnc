# 12 — Operatör devri: AXIS ile bizim arayüz arasında geçiş

Test tezgahında makineyi AXIS'ten bizim arayüze (`ui/bettercnc`) ve geri güvenle geçirmek için
kısa prosedür, operatör özeti ve PC'ye dönünce yapılacak denemelerin tam listesi. Kiosk servisi
(`deploy/kiosk.sh enable`) bu prosedürün parçası değil: o karar en sondadır (aşağıda, PC
listesinin 22. adımı) ve kullanıcınındır.

Durum (2026-11-14): P0 paketleri WP01–WP10 Mac'te, sahte LinuxCNC ve LinuxCNC 2.9.9
simülatörüyle denendi. Arayüz gerçek makineyi **hiç sürmedi**; 2026-11-12'de yalnız AXIS'in
yanında, depo dışı bir izleyiciyle salt izleme olarak çalıştı (docs/11, "Known gaps").
Ayrıntılı davranış: [11-ui-implementation.md](11-ui-implementation.md); ölçülmemiş sabitler:
[OPEN-QUESTIONS.md](OPEN-QUESTIONS.md) B13.

## Önce: üç kural

1. **Gerçek durdurma güç anahtarıdır.** Rig'de donanım acil stopu yok; ekrandaki ACİL STOP
   yalnız yazılımdır, motor gücünü kesmez. Her denemede bir el güç anahtarında.
2. **Komut veren tek arayüz.** AXIS açıkken bizim arayüz **yalnız `--observe` ile** açılır.
   `--observe`'suz açılırsa komut gönderir ve LinuxCNC'nin hata kanalını AXIS'ten çalar: bunu
   engelleyen bir kilit henüz yok (plan WP12). İki arayüz aynı anda komut vermez.
3. **Limit ve referans ayarlarına dokunulmaz.** `configs/test-rig/test-rig.ini` ve `.hal`
   değişmez; Mod B'nin INI'si (`test-rig-kiosk.ini`) ondan türetilir, yalnız
   `[DISPLAY]DISPLAY` farklıdır (`deploy/make_kiosk_ini.py`).

Bütün komutlar repo kökünden (`cd` ile depo dizinine geçip), LinuxCNC'yi başlatan kullanıcıyla
çalıştırılır.

## Mod A — AXIS + gözlem

Makineyi AXIS sürer; bizim arayüz yanında yalnız okur.

1. LinuxCNC'yi her zamanki gibi AXIS'le aç: `linuxcnc configs/test-rig/test-rig.ini`.
2. AXIS açıldıktan **sonra**, ayrı bir terminalde:

   ```bash
   PYTHONPATH=ui python3 -m bettercnc.app --adapter linuxcnc --observe --ini configs/test-rig/test-rig.ini --port 8080
   ```

   Başlangıç satırında `LinuxCncAdapter` ve `SALT İZLEME` görünmeli. LinuxCNC'den önce
   başlatılırsa ya da başka kullanıcıyla başlatılırsa Türkçe bir mesajla 1 koduyla çıkar.
3. Sıradan bir tarayıcıda http://127.0.0.1:8080/ aç. Her ekranın üstünde
   `SALT İZLEME — makine AXIS'ten kullanılıyor` bandı; ACİL STOP dışında her düğme kapalı ve
   gerekçesi yazılı; DURDUR kapalı (Durdur AXIS'ten, Esc).
4. `pgrep -a axis` bir AXIS süreci, `pgrep -af bettercnc.app` tek bir `--observe`'lu süreç
   göstermeli.

Bilinmesi gerekenler:

- Bizim ACİL STOP'umuz gözlemde de gider (rayda, ya da Pause tuşu). Kart bağlantısı koparsa
  (`io_error`) arayüz makineyi kendisi acil stopa alır: bu bilerek gözlemde de açık.
- LinuxCNC'nin hata mesajları AXIS'te kalır; bizim hata kutumuz yalnız bizim acil stopumuzu
  gösterir. «Tamam» yereldir, LinuxCNC'ye bir şey göndermez.
- AXIS, referans alınırken jog tıklamasını LinuxCNC'ye gönderir (AXIS'in `manual_ok`'u homing'e
  bakmaz); LinuxCNC reddeder ve referans alma iptal olur, eksen switch'te kalabilir. AXIS'te
  Home All sürerken jog'a basmayın.
- AXIS kapanınca LinuxCNC kapanır; bizim gözlem arayüzü de kendiliğinden çıkar (HAL bileşeni
  `halcmd unload all` ile SIGTERM alır). Elle kapatmak için terminalinde Ctrl+C.

## Mod B — bizim arayüz (AXIS kapalı)

Makineyi bizim arayüz sürer; arayüz LinuxCNC'nin kendi `[DISPLAY]` programıdır
(`deploy/bettercnc-display`), yani LinuxCNC'den sonra ve onun kullanıcısıyla başlar, kapanınca
LinuxCNC de kapanır.

1. AXIS'i kapat (File ▸ Quit); LinuxCNC kapanır. Mod A'nın gözlem arayüzü de çıkmış olmalı
   (`pgrep -af bettercnc.app` boş): ikisi de 8080'i kullanır.
2. Sahipsiz motion belleğini temizle (LinuxCNC çalışırken hiçbir şeye dokunmaz):

   ```bash
   deploy/kiosk.sh clean-shm
   ```

3. Kiosk INI'sini üret (her seferinde; `test-rig.ini`'nin güncel hâlinden):

   ```bash
   python3 deploy/make_kiosk_ini.py configs/test-rig/test-rig.ini
   ```

   `configs/test-rig/test-rig-kiosk.ini` yazılır (gitignore'da).
4. Masaüstündeki bir terminalden LinuxCNC'yi bu INI'yle aç (servis `enable` YOK):

   ```bash
   linuxcnc configs/test-rig/test-rig-kiosk.ini
   ```

5. Sıradan bir tarayıcıda http://127.0.0.1:8080/ aç. Başlıkta `DEVRE DIŞI`; ilk iş
   «Yazılım sıfırla» (acil stoptan çıkar ve makineyi açar).
6. `pgrep -a axis` hiçbir şey basmamalı; `pgrep -af bettercnc.app` tek bir `--adapter linuxcnc`
   süreci göstermeli (`--observe` YOK).

Bu modda sayfa kiosk modunda değildir (`--boot-url` yok): tooltip'ler, sağ tık ve F12 durur,
bağlantı koparsa başlatma ekranına gidilmez. Kapalı düğmelerin gerekçesi zaten ekranda yazıyla
durur. Arayüz `--exit-if-linuxcnc-lost 3` ile çalışır: LinuxCNC'nin bir parçası ölürse ya da
takılırsa ~3 s'de çıkar ve LinuxCNC kapanır; kiosk servisi olmadığı için yeniden başlatan yok.

Simülatörde denendi (2026-11-14, `sim/sim.sh kiosk`, LinuxCNC 2.9.9): arayüz süreci `linuxcnc`
betiğinin çocuğu (`--ini …/test-rig-sim.ini`), `axis` süreci yok; «Yazılım sıfırla» → ON;
Program ekranı `nc_files`'ı listeledi, `faz8-kare.ngc` yüklendi (`stat.file` mutlak yol);
Pause tuşu → ESTOP_RESET, hata kutusunda `Yazılım acil stop gönderildi. Sürücü gücü
DEĞİŞMEDİ.`; Esc → `Durduruldu.` Kiosk açıldıktan sonraki ilk Pause basışı sayfaya ulaşmadı,
sonraki üç basış ulaştı (`wtype` ile; gerçek klavyede PC listesi 22).

## AXIS'e geri dönüş

Her adımda hazır olan yol:

1. Bizim arayüzde makine boştayken ACİL STOP (ya da önce DURDUR).
2. LinuxCNC'yi kapat: Mod B'yi başlattığın terminalde Ctrl+C (`linuxcnc` betiği SIGINT'te
   temizler, `reference/linuxcnc/scripts/linuxcnc.in:771-772`). Arayüz de çıkar.
3. `deploy/kiosk.sh clean-shm`.
4. `linuxcnc configs/test-rig/test-rig.ini` → AXIS.

LinuxCNC açılmıyorsa ("A configuration error is preventing LinuxCNC from starting"): sahipsiz
paylaşımlı bellek; `deploy/README.md`, "LinuxCNC açılmıyorsa".

Bizim arayüzün aldığı referanslar AXIS'e geçmez: AXIS'te yeniden referans alın.

## Operatör özeti (Mod B)

- **Ray (sağ kenar, her ekranda).** Kırmızı **ACİL STOP**: basışta gider, hiçbir durumda
  kapanmaz; yazılım acil stopudur (motor gücü değişmez). Sarı **DURDUR**: LinuxCNC abort —
  programı, MDI'ı, jog'u ve referans almayı durdurur. Klavye: Esc = DURDUR, Pause = ACİL STOP
  (MDI alanında da).
- **Açmak.** «Yazılım sıfırla» (Kontrol ▸ ACİL STOP kartı): acil stoptan çıkar, makineyi açar.
- **Hata kutusu** (üst bant, kırmızı): Türkçe, ekseni/switch'i adlandırır, altında
  LinuxCNC'nin kendi metni; «Tamam»a basılana kadar kalır.
- **Limitten kurtarma.** Bir eksen switch'te kaldıysa Kontrol'de limit kartı çıkar:
  «Limitten kurtar: X+ yönünde 3 mm». Tek basış: limit aşma izni, makineyi aç, switch'ten uzağa
  3 mm. Jog sürerken başlıkta `LİMİT KORUMASI GEÇİCİ KAPALI`; jog bitince kalkar. Switch
  basılıyken «Yazılım sıfırla» makineyi açmaz, Home All reddedilir, switch'e doğru jog gitmez.
  Yetmezse kart yine çıkar: bir kez daha basın.
- **Referans.** «Tümünü referansla»: Z → Y → X, son konum X 5, Y 5, Z −5. Her eksenin çipinde
  «Z referansla» gibi kendi düğmesi. Herhangi bir eksen referanslıyken, ya da Z referanssızken X
  veya Y tek başına istenince **onay bandı** ne hareket edeceğini yazar: «Evet» (sorudan 1 s
  sonra açılır, 15 s geçerli) ya da «Vazgeç». Referans alırken DURDUR → o eksenler referanssız
  kalır; latch anında DURDUR ekseni switch'te bırakabilir → «Limitten kurtar», sonra yeniden
  referans.
- **Jog.** Yalnız artımlı. Referanssız eksende adım en çok 10 mm, Z− yönünde en çok 1 mm
  (Z'nin alt switch'i yok); 50 mm çipi referanstan sonra açılır. Referanslıyken fazla uzun
  adım yumuşak limite kadar kısaltılır ve söylenir; limitteki eksen o yöne gitmez (X 3…399,
  Y 3…387, Z −96.5…−3). Hareket sürerken, referans alınırken, program ya da MDI sürerken
  (duraklamışken de) jog yok; kapalı düğmenin gerekçesi pedin altında yazılı.
- **MDI.** Limit dışı hedef gönderilmez, örn. `G0 Z0` → bantta `Z hedefi yumuşak limit
  dışında (izinli −96.5…−3 …)`, geçmişte kırmızı satır.
- **Program.** Program ekranı `nc_files/`'ı listeler; «Yükle». Yüklü dosya `YÜKLÜ`, altında
  yolu ve satır sayısı. Başlatma satırında tek birincil düğme: «Başlat» / «Duraklat» / «Devam»;
  yanında «Adım» ve «Durdur». Bitişte `Program bitti: <ad>`, Durdur'dan sonra `Program
  durduruldu: <ad>`, LinuxCNC hatasıyla `Program hatayla durdu: <ad>`. Referans alınmadan
  «Başlat» kapalı ve nedeni yazılı.

## PC'ye dönünce: deneme listesi

Planın PC kontrol listesi (`pc_checklist`) adım adım, sırası ve metni değişmeden; her adımın
altında WP01–WP09'un raporladığı ek kontroller (tekrarlar birleştirildi). Listede «Makineyi aç»
diye geçen düğme ekranda **«Yazılım sıfırla»**dır. "Kiosk" yazan ek kontroller ancak 22. adımda
(servis, kiosk klavye haritası, tooltip'siz sayfa) yapılabilir.

### 1

Hazırlık (Mac'te, PC'ye gitmeden önce):
- P0 paketleri WP01–WP10 bitmiş olmalı;
- make test, make sim-test ve make sim-e2e yeşil;
- commit ve push kararı kullanıcıda;
- docs/12-operator-handover.md makinedeki operatör için hazır.

### 2

PC'de:
- live USB klonunda git pull (kullanıcı yapar);
- mümkünse normal açılış; tek çekirdek failsafe ise not al (OPEN-QUESTIONS B10);
- makinede make test çalıştır; reference/ yoksa ilgili testler atlanır.

### 3

Temel ölçüm, yalnız AXIS: LinuxCNC'yi test-rig.ini ile aç. Referans değeri olarak servo-thread.tmax'ı not al (halcmd show thread).

### 4

AXIS yanında gözlem modu:
- PYTHONPATH=ui python3 -m bettercnc.app --adapter linuxcnc --observe --ini configs/test-rig/test-rig.ini --port 8080 ile başlat;
- sıradan bir tarayıcıda aç;
- SALT İZLEME bandı görünmeli, acil stop dışında her şey kapalı ve gerekçeli olmalı;
- ps ile komut veren tek GUI'nin AXIS olduğunu doğrula.

Ek kontroller:
- (WP08) AXIS'in dosya penceresinden yüklenen program Program ekranında yüklü görünmeli (yol ve
  satır sayısı); yükleme düğmelerinin hepsi gözlem gerekçesiyle kapalı.

### 5

Okuma doğrulama (gözlem):
- AXIS'te makineyi aç ve referans al;
- bizim DRO'da makine ve iş konumu AXIS ile aynı olmalı;
- G92 rozetine bak; G92 sıfır değilse not al: AXIS oturumlarından kalmış olabilir;
- referans çipleri ve header durumu doğru olmalı;
- makine KAPALIYKEN beş switch'e tek tek elle bas → Kontrol ve Alarmlar'da doğru etiket 'basılı' ve hard_limits bayrağı görünmeli.

Ek kontroller (`GET /api/state`'in `motion` bölümü; tarayıcıda
http://127.0.0.1:8080/api/state):
- (WP02) Makine dururken `moving` false ve `stat.current_vel` tam 0.0 (`MOVING_VEL_EPS` 1e-6
  seçildi); AXIS'te 10 mm jog sırasında `moving` true, bitince false; AXIS'te bir MDI hareketi
  sırasında `task_mode` `mdi` ve `mdi_running` true.
- (WP02) Dururken `motion.commanded` ile DRO'daki makine konumu arasındaki fark en çok bir adım
  (1/SCALE mm); teleop jogda komut konumu gerçek konumun önünde (sim'de 3 mm/s'de 0,009 mm).
- (WP02) AXIS'te Home All sırasında: referans alınan eklemin `hard_limits` bayrağı switch
  üstündeyken true, makine trip olmamalı (HOME_IGNORE_LIMITS); `moving`'i 1 Hz izle — arama ve
  geri çekilmede true, fazlar arasında kısa false okumaları görülürse not al.
- (WP02) Makine ON, referanslı, hareketsiz: AXIS'ten MDI `G92 X10` → bizim İş X'i AXIS'in göreli
  DRO'su ile aynı (makine X − G54 X − G92 X), `g92_active` true; `G92.1` → İş X yine makine X −
  G54 X, `g92_active` false. Açılışta `g92_active` zaten true ise `test-rig.var`'daki
  5210–5213'ü not al.
- (WP04) LinuxCNC'nin dili İngilizce olmalı (hata metinleri İngilizce değilse çeviriler
  tutmaz).
- (WP05) Gözlemde AXIS'ten sürerken bir switch'e çarp: bizim hata kutusu switch'i ve AXIS'teki
  çıkış yolunu adlandırmalı (`Switch'ten AXIS'te çıkın …`), limit kartının düğmesi gözlem
  gerekçesiyle kapalı.
- (son P0 incelemesi) AXIS'te bir programı AXIS'in duraklat düğmesiyle duraklat: bizim başlık
  `HAZIR` değil `DURAKLATILDI` okumalı; Program ekranındaki bütün denetimler gözlem gerekçesiyle
  kapalı kalmalı.

### 6

Override davranışını hareketsiz gözle:
- makine kapalıyken bir eksen switch'teyken AXIS'te override kutusunu işaretle → bizim gözlem arayüzünde 'LİMİT KORUMASI GEÇİCİ KAPALI' rozeti görünmeli;
- 5 s bekle: rozet kalıyor mu? (Sim'de kalıyor; brief'teki 'yerine oturunca kalkar' dersi bununla netleşir.)
- kutuya ikinci kez tıklamak AUTO'ya geçirip maskeyi siler; bunu bilerek gözle.

### 7

Hata kanalı ve yük:
- AXIS'te MDI 'G0 Z0' → hata AXIS'te görünmeli; bizim arayüz hatayı çalmamalı;
- AXIS ve bizim sayfa açıkken 5 dakika servo-thread.tmax'ı ve CPU'yu izle;
- tmax 1 ms periyoda yaklaşırsa kiosk'a geçme ve WP19'u öne al.

Ek kontroller:
- (WP09) Program ekranı açıkken poll başına fazladan bir `os.stat` (ilerleme için) tek
  çekirdekte fark ediliyor mu: tmax ve CPU'yu Program ekranında da izle.

### 8

Gözlemden acil stop:
- AXIS'te makine ON ve boştayken bizim raydaki ACİL STOP'a fareyle basılı tutarak bas → ESTOP_RESET; AXIS'ten tekrar aç;
- AXIS'te yavaş jog sürerken bizim acil stop → durmalı.

Ek kontroller:
- (WP04) Acil stoptan sonra hata kutusunda «Tamam»: kutu silinmeli, AXIS başka hiçbir komut
  görmemeli.

### 9

Devir:
- AXIS'i kapat; LinuxCNC kapanır;
- deploy/kiosk.sh clean-shm;
- python3 deploy/make_kiosk_ini.py configs/test-rig/test-rig.ini;
- masaüstünden linuxcnc configs/test-rig/test-rig-kiosk.ini (servis enable YOK);
- tarayıcıda http://127.0.0.1:8080/;
- ps ile axis süreci olmadığını doğrula.
Geri dönüş yolu her adımda hazır: kapat, clean-shm, test-rig.ini ile AXIS.

### 10

Açma ve durdurma:
- «Makineyi aç» → ON;
- ray ACİL STOP → ≤0.3 s'de durum değişmeli; bir kez de basılı tutarak dene;
- Esc → DURDUR, Pause → acil stop;
- tekrar aç.

Ek kontroller:
- (WP03) Tek çekirdekte DURDUR'u 10 mm jog ve MDI G0 sırasında zamanla (sim: jog 0,15–0,20 s,
  G0 0,38–0,43 s, POST'tan duruşa). Durdurmadan sonra yersiz `Durdurma gönderildi — LinuxCNC
  henüz boşta değil.` çıkıyor mu, not al.
- (WP03) Sıfırlama ile acil stop yarışı: LinuxCNC ESTOP'tayken «Yazılım sıfırla», ~0,3 s içinde
  ACİL STOP → LinuxCNC ON'da kalmamalı, mesaj `Acil stop basıldı — komut gönderilmedi.` (ya da
  acil stop metni). «Yazılım sıfırla»ya çift tık + hemen ACİL STOP ile tekrarla: makine kapalı
  kalmalı.
- (WP03) «Yazılım sıfırla» sürerken gelen DURDUR'dan sonra başlık makineyi kapalı göstermeli;
  ikinci «Yazılım sıfırla» makineyi normal açmalı.
- (WP03) PC'nin gerçek ekran çözünürlüğünü not al: ray hiçbir içeriği örtmemeli, ACİL STOP ve
  DURDUR tam görünmeli, tuş ipucu kötü kırılmamalı; rayın altında yatay kaydırma çubuğu yok;
  ekran ~680 px'ten darsa «Makine» menüsü rayın altına giriyor mu.
- (WP04) «Tamam» fareyle/dokunarak kolay basılmalı; hatayla ~130 px'lik bant operatörün
  ihtiyaç duyduğu kontrolleri örtmemeli. «Tamam»dan sonra aynı metin mesaj satırında kırmızı
  görünüyor: operatöre "hâlâ etkin" gibi okunuyor mu, sor.
- (WP10) Mod B'de açılıştan sonraki **ilk** tuş basışı (Pause ya da Esc) sayfaya ulaşmalı
  (sim'de `wtype` ile ilk Pause ulaşmadı, sonrakiler ulaştı).

### 11

Zamanlama sabitleri: ~20 eylem boyunca sahte ret var mı izle ('… geçilemedi', 'sıfırlama süresi'). WAIT_TIMEOUT_S = 0.25 s ve 3×[EMCIO]CYCLE_TIME için sonucu not al.

### 12

Referanssız jog:
- her eksende ±1 mm: yönü ve mesafeyi DRO ve cetvelle doğrula;
- 50 mm çipi kapalı olmalı;
- Z− 10 mm reddedilmeli;
- 10 mm jog sürerken ikinci tık reddedilmeli;
- 10 mm jog sırasında DURDUR hareketi durdurmalı.

Ek kontroller:
- (WP06) Adım 10'dayken Z− düğmesi kapalı ve gerekçesi pedin altında okunur; `Yumuşak
  limitler: etkin değil (referans yok) — …` satırı görünür; 1 mm Z− tıkları yine gider.

### 13

Limit kurtarma, her switch için tek tek, makine boşta ve yavaş. Sıra: X sağ, X sol, Y arka, Y ön, Z üst.
- ekseni switch'e 1 mm adımlarla yaklaştır → trip olur;
- hata kutusu doğru switch'i adlandırmalı;
- «Makineyi aç» ON göndermemeli ve kurtarmayı söylemeli;
- «Limitten kurtar» → makine açılır, eksen 3 mm geri gelir, switch serbest kalır, rozet jog bitince kalkar.
Fazla yolu ve 3 mm'nin yettiğini ölç; yetmiyorsa RECOVERY_STEP_MM'yi büyüt (kod değişikliği Mac'te, testle).

Ek kontroller:
- (WP05) Makine kapalıyken beş switch'e elle bas: limit kartı switch'i adlandırmalı ve fiziksel
  olarak ondan uzak yönü önermeli — X sol → X+, X sağ → X−, Y ön → Y+, Y arka → Y−, Z üst → Z−.
  Basılıyken Home All `Limitten kurtar` metniyle reddedilmeli.
- (WP05) Her kurtarma jogunda başlıkta kırmızı `LİMİT KORUMASI GEÇİCİ KAPALI`, jog bitince
  kalkmalı; ardından tek bir mesaj (`… serbest; limit koruması yeniden etkin.`, referanssızsa
  `Referans alın.` de) ve limit kartı kaybolmalı.
- (WP01) Trip'ten sonra makine kapalıyken `halcmd getp motion.current-vel` oku: sim'de
  ESTOP_RESET'te jog hızında (3.000) kalıyor; 2.9.10'da da öyle mi?
- (WP05, isteğe bağlı, el güç anahtarında) İki switch birden basılıysa (örn. X sol ve Y ön,
  güvenle ayarlanabilirse): ilk basış X'i kurtarır ve Y'yi adlandırır, makine Y'de yine durur,
  ikinci basış Y'yi kurtarır.
- (WP05, isteğe bağlı, yavaş, el güç anahtarında) «Limitten kurtar»dan hemen sonra, eksen hâlâ
  switch'teyken (3 mm/s'de ~0,2 s içinde) ACİL STOP: sonra «Yazılım sıfırla» makineyi açmamalı
  ve switch'i adlandırmalı; tek «Limitten kurtar» ekseni yeni bir `joint N on limit switch
  error` olmadan kurtarmalı.
- (WP05) Limit kartı çıkıp kaybolurken «Yazılım sıfırla»lı kart kayıyor: hiçbir şey ekran
  dışına ya da rayın altına gitmemeli; «Limitten kurtar» (hareket ettirir) «Yazılım sıfırla»
  yerine yanlışlıkla basılamamalı.

### 14

Home All:
- Z → Y → X sırası, son konum 5/5/−5;
- yeniden referansta onay bandı çıkmalı;
- homing sırasında DURDUR → iptal etmeli;
- tekrar Home All.

Ek kontroller:
- (WP01, WP06) Home All boyunca ~0,2 s'de bir jog düğmesine dokun (özellikle Z bitip Y başlarken
  ve Y bitip X başlarken) ve bir jog'u bayat bir sayfadan gönder: her biri `Referans alma
  sürüyor — jog yok. Durdurmak için «Durdur».` ile reddedilmeli; Jog kartında `Referans alma
  sürüyor — jog kapalı. Durdurmak için «Durdur».`; referans alma Z → Y → X, 5/5/−5'te, üç eklem
  referanslı biter; hiçbir eksen switch'te kalmaz; `Can't jog any joint while homing.` ne
  Alarmlar'da ne hata kutusunda (`HOMING_GAP_RECHECK_S`).
- (WP03) Home All'da Y referans alırken (Z bitmiş) DURDUR, bir kez de X alırken: durdurmadan
  sonra sıradaki hiçbir eklem harekete başlamamalı.
- (WP06) Home All'ı DURDUR'la yarıda bırak (örn. yalnız Z referanslı), sonra jog: tıklar
  gerekçesiz gider (~30 ms fazla gecikme); X ve Y referanssız sınırlarla eklem jogu (10 mm;
  Z− 1 mm), Z yumuşak limitleri içinde.
- (WP05, isteğe bağlı, yavaş, el güç anahtarında; WP03 de) Home All'da Z (sonra Y) switch'ten
  latch hızında geri çekilirken DURDUR: eksen switch'te kalır, makine kapalı (`joint N on limit
  switch error`), kart görünür; «Limitten kurtar» 3 mm'yle kurtarmalı, ikinci Home All
  tamamlanmalı.
- (WP07) Referanslı makinede «Evet»e 15 s'den sonra bas: hiçbir şey kımıldamamalı, bant
  `Onay geçersiz …` demeli.
- (WP07) «Evet»/«Vazgeç» PC'nin giriş aygıtıyla kolay basılıyor mu; 15 s soruyu okuyup makineye
  bakmaya yetiyor mu (okuma süresini ölç); `CONFIRM_MIN_S` = 1 s bir çift tık/dokunuştan uzun
  mu; «Evet»in 1–2 s sonra açılması kafa karıştırıyor mu; ekran kayınca bant görünür kalıyor mu.
- (WP07) Kontrol ~840 px'ten darda tek sütuna düşüyorsa referans kartını mesaj bandının hemen
  altına kaydır, referanslı makinede «Tümünü referansla»ya ve bir eksen düğmesine çift dokun:
  hiçbir şey kımıldamamalı; «Evet» gri, `Önce soruyu okuyun — «Evet» 1 s sonra açılır.`, ~2 s
  içinde açılmalı.
- (P0 temizliği) «Evet» artık sorudan ~1 s sonra açılmalı (1–2 s değil; mock'ta tarayıcıda
  1,035 s ölçüldü): PC'nin tarayıcısında ve kiosk'ta bandın açılışından «Evet»in açılmasına
  süreyi ölç; 1 s'den önce açılmamalı, açılmadan basılan «Evet» hiçbir şey kımıldatmamalı.

### 15

Tek eksen referans:
- yalnız Z;
- sonra X (Z referanslıyken);
- Z referanssızken X'in onay istediğini doğrula.

Ek kontroller:
- (WP07) Son referanssız ekseni tek başına referansla (Z ve Y referanslı, sonra «X referansla»):
  motion kendiliğinden teleop'a geçmeli (`reference/linuxcnc/src/emc/motion/homing.c:1400`,
  `control.c:261-263`); sonraki jog yumuşak limitlere uyan
  eksen (teleop) jogu olmalı.
- (WP07) Y'yi tek başına yeniden referansla (iki motor, tek eklem): «Y referansla» sormalı,
  «Evet» yeniden referanslar; Y'nin iki yanı birlikte gitmeli.
- (WP07) Bir eklem referans alırken (X ~80 s sürebilir) kartta `Referans alma sürüyor —
  durdurmak için «Durdur».` ve DURDUR notu, bütün referans düğmeleri kapalı; bitince geri gelir.
- (WP07) Yazılım acil stopu açıkken referans düğmeleri kapalı, altında `Makine devre dışı —
  referans alınamaz.`
- (WP07) Tek eksen referansının arama fazında DURDUR: eksen durur, referanssız kalır, makine ON;
  latch fazında DURDUR: eksen switch'te, makine durur, referans kartı switch'i ve «Limitten
  kurtar»ı adlandırır.
- (P0 temizliği) Rig'in INI'sinde negatif HOME_SEQUENCE yok: üç eksen düğmesi de açık olmalı,
  hiçbirinin altında `negatif HOME_SEQUENCE ile … birlikte referanslanır` yazmamalı.

### 16

Referanslı jog:
- Z −5'te Z+10 → 2 mm'ye kırpılır ve mesaj verir;
- X 3.0'dayken X− reddedilir;
- jog yumuşak limitlerde durur.

Ek kontroller:
- (WP06) Z −5'te Z+10 sonrası Z −3.000'da, LinuxCNC hatası yok; `halcmd getp
  axis.z.teleop-pos-cmd` = `GET /api/state` `motion.commanded.Z` (1e-6 mm içinde); ikinci Z+
  hiçbir şey kımıldatmadan reddedilir.

### 17

MDI:
- limitlerin içinde G0;
- 'G0 Z0' → bantta Türkçe özgül gerekçe (izinli −96.5…−3) ve geçmişte kırmızı satır.

Ek kontroller:
- (WP04) `G0 Z0`'dan hemen sonra iyi bir `G0 X50`: Z0'ın satırı Z metniyle reddedildi, X50
  tamamlandı; hata kutusu `Geçersiz doğrusal hareket`e dönmemeli. Düz `G21` tamamlandı'ya
  ulaşmalı (sarıda takılmamalı). Z0'ın üç ham raporunun Alarmlar'daki zaman aralığını not al
  (`FOLLOW_UP_WINDOW_S`, `MDI_RESULT_WINDOW_S`).
- (WP04) Reddedilen `G0 Z0`'dan sonra LinuxCNC ON kalmalı, hiçbir şey kımıldamamalı, sıfırlama
  gerekmemeli.
- (WP04) Kısa bir satır (örn. X 9'dan `G0 X10`) ve hemen jog: geçmiş satırı `Tamamlandı.`
  olmalı (jog gerekçesiyle reddedildi değil); kutu jog'un reddini yalnız LinuxCNC reddettiyse
  gösterir.
- (WP09, son P0 incelemesi) Çalışan bir MDI satırını (örn. `G1 X200 F300`) «Duraklat», başka
  bir MDI satırı yaz: `Duraklatılmış bir program ya da MDI satırı var — MDI kabul edilmedi. …`
  ile reddedilmeli, hiçbir şey kımıldamamalı; «Devam» yalnız ilk satırı sürdürmeli.
- (son P0 incelemesi) M0'da (AUTO) ya da «Duraklat» ile duraklamış bir programda MDI satırı yaz:
  aynı metinle reddedilmeli, hiçbir şey kımıldamamalı; «Devam» yalnız programı sürdürmeli.
- (son P0 incelemesi) Duraklatmadan sona kadar koşan bir programdan (M2/M30) hemen sonra bir MDI
  satırı hemen kabul edilmeli (`stat.paused` 0, yorumlayıcı IDLE): yeni duraklama kuralı temiz bir
  koşudan sonra satır reddetmemeli.
- (P0 temizliği) Program ya da MDI satırı duraklamışken Kontrol'deki MDI paneli kapalı olmalı,
  altında `Duraklatılmış bir program ya da MDI satırı var — MDI kabul edilmedi. Önce «Devam» ya da
  «Durdur».` yazmalı; «Devam» ya da «Durdur»dan sonra yeniden açılmalı.
- (P0 temizliği) Referanslı makinede `G0 XABS[-50]` (X 50'ye gider) ve `G21` normal gitmeli: MDI
  kapısı yalnız referanssızken daha çok reddeder (rig'de `NO_FORCE_HOMING` yok, referanssız MDI
  zaten reddedilir).

### 18

Program (Faz 8 gibi havada kuru koşu: Z üstte, malzeme yok):
- Program ekranında nc_files/faz8-kare.ngc listelenir; yükle, satır sayısını kontrol et;
- başlat; ortada duraklat (konum sabit kalmalı); devam; tek adım;
- DURDUR, yeniden başlat;
- sonda 'Program bitti';
- besleme %50 ile de dene.

Ek kontroller:
- (WP08) LinuxCNC'yi başlatan kullanıcıyla Program ekranı `nc_files/`'ı listeler (dizinde okuma
  ve çalıştırma izni). `faz8-kare.ngc` yüklenince `stat.file` PC'deki mutlak
  `<repo>/nc_files/faz8-kare.ngc`, satır sayısı dosyayla aynı.
- (WP08) Arayüz çalışırken `nc_files`'a yeni bir `.ngc` kopyala: «Listeyi yenile»siz ~1–3 s'de
  görünmeli. Bir dosyayı yerinde düzenle: boyutu ancak «Listeyi yenile»den sonra değişir.
- (WP08) Türkçe (ASCII olmayan) adlı bir dosya yükle: LinuxCNC açmalı, `YÜKLÜ` ve satır sayısı
  görünmeli.
- (WP08) Büyük bir programın yüklenme süresini ölç (satır sayısı bir kez, oturum kilidi altında
  okunur); Program ekranında 1 Hz poll gecikmemeli.
- (WP09) «Duraklat» bütün eksenleri olduğu yerde durdurmalı, Y'nin iki motoru birlikte; 10 s
  bekle, kayma ya da takip hatası yok; «Devam» aynı noktadan sürer; duraklamışken «Adım» tam bir
  satır ilerletir.
- (WP09) «Duraklat»a basıştan hareketin durmasına süreyi 5 mm/s'de ölç (0,25 s sınırlı bekleme +
  yavaşlama). 10 mm/s'de «Duraklat»tan hemen sonra başlıkta `DURAKLATILDI`'dan önce `TEK ADIM`
  görünüyor mu, ne kadar.
- (WP09) Boştayken «Adım», bir «Adım» daha, sonra «Devam» sona kadar: LinuxCNC (sim 2.9.9'daki
  gibi) `File ended with no percent sign (%) or program end (M2)` diyor mu; AXIS de aynısını
  gösteriyor mu.
- (WP09) M0'lı bir program başlıkta `DURAKLATILDI` ile durmalı, «Devam» sürdürmeli.
- (WP09) `(MSG, ...)` yorumlu bir program `Program bitti` ile bitmeli (`hatayla durdu` değil).
- (WP09) Yumuşak limitlerin reddettiği bir MDI satırı, ~1 s içinde «Başlat»: temiz biten
  program `Program bitti` demeli.
- (son P0 incelemesi, P0 temizliği) Kısa bir programı yalnız «Adım» ile (hiç «Devam» basmadan)
  sonuna kadar ilerlet: sim 2.9.9'da `stat.paused` 1 kaldı. Başlık `PROGRAM SONU · DURDUR`, mesaj
  `Program bitti: … — LinuxCNC hâlâ duraklatılmış gösteriyor; «Durdur» ile bitirin.` olmalı;
  «Devam» kapalı ve gerekçesi görünür, «Başlat», «Adım», jog, referans, yükleme ve MDI hiçbir şey
  göndermeden reddedilmeli; tek «Durdur» `stat.paused`'ı 0 yapmalı, başlık `HAZIR`.
- (P0 temizliği) «Duraklat»tan hemen sonra (10 mm/s, yavaşlarken) başlıkta `TEK ADIM`
  görünmemeli, doğrudan `DURAKLATILDI`; duraklamışken «Adım»ın hareketi sürerken `TEK ADIM`.
- (P0 temizliği) Boştayken «Adım», bir «Adım» daha, sonra «Devam»: arayüz «Devam»dan önce bir
  duraklatma gönderir; program `Program bitti` ile bitmeli, Alarmlar'da `File ended with no
  percent sign (%) or program end (M2)` olmamalı (sim 2.9.9'da çıkmadı). AXIS'in kendi «Devam»ı
  bu hatayı yine verebilir.
- (P0 temizliği) Operatörün izniyle `nc_files`'a geçici bir `m2.ngc` (tek satır `M2`) koy,
  yükle, «Başlat»: mesaj `Program bitti: m2.ngc` olmalı, `Başlatma gönderildi — LinuxCNC programı
  başlatmadı` değil. Sonra dosyayı sil.
- (P0 temizliği) Program listesinin altında `toplam N dosya` görünmeli; 200'den çok dosyada
  `Sayfa 1 / K · toplam N dosya` ile «Önceki» / «Sonraki» çıkar ve her sayfadaki dosya yüklenir
  (rig'de bu kadar dosya yoksa birim testleri yeter).

### 19

Program sırasında ACİL STOP; sonra «Makineyi aç». Referanslar korunmalı (VOLATILE_HOME yok).

Ek kontroller:
- (P0 temizliği) Bir eylem sürerken (örn. büyük bir programın «Yükle»si ya da «Yazılım sıfırla»)
  ACİL STOP: ray bandı `ACİL STOP GÖNDERİLDİ — onay bekleniyor` hemen çıkmalı ve sonraki
  yenilemede kalkmalı; ekranda `Komut gönderilemedi` örtüsü çıkmamalı (cevap kilidi beklemez).

### 20

Bağlantı hatası (makine ON, boşta, referanslı):
- Ethernet kablosunu çek → ≤1 s'de ACİL STOP; WP11 yapıldıysa tarayıcı kapalıyken de dene;
- kabloyu tak, «Makineyi aç» → 'yeniden kuruldu' mesajı, referanslar silinmiş olmalı;
- ilk jog eklem jogu olmalı (TELEOP değil).

Ek kontroller:
- (WP02) Kablo çekiliyken ve makine dururken bir switch'e elle bas: `motion.hard_limits` ve
  Alarmlar'daki giriş durumu DEĞİŞMEMELİ (hm2 `io_error`'da girişleri yenilemiyor); kablo
  takılıp bağlantı kurulunca ikisi gerçek duruma dönmeli.
- (WP04) Kablo takılıp «Yazılım sıfırla»: estop mandalı arkadan düşerse (`has_bit`) hata kutusu
  `Kartla bağlantı koptu — makine durduruldu …` demeli, hostmot2'nin `Watchdog has bit! …`
  metnini değil. Çekme ve takma sırasında Alarmlar'a düşen ham hm2/hm2_eth satırlarını yaz
  (henüz çevrilmeyen LinuxCNC hataları).
- (WP04) MDI `G0 Z0` (reddedilir), 2 s içinde kabloyu çek: kutu bağlantı kopması metnine
  geçmeli, `Z hedefi yumuşak limit dışında …`'da kalmamalı.

### 21

Yeniden başlatma: LinuxCNC'yi kapat (arayüz de çıkar), clean-shm, yeniden aç. Sahipsiz shm 0x64 sorunu olmamalı.

### 22

Hepsi geçerse ve kullanıcı karar verirse:
- deploy/kiosk.sh enable;
- ilk kiosk açılışında kısa duman testi: acil stop, makineyi aç, referans, jog, program yükle/başlat/durdur.

Ek kontroller (kiosk):
- (WP03) Kiosk klavye haritasıyla gerçek klavyede Pause Chromium'a `Pause` olarak gelip acil
  stopu, Esc DURDUR'u göndermeli. Klavyede Pause yoksa not al.
- (WP03) Dokunmatik ekran varsa: ACİL STOP ve DURDUR'a dokunuş parmak değdiğinde gider; 1 Hz
  yenileme sırasında jog düğmesine dokunuş kaybolmaz; `#app` üstünde 3 s'den uzun duran parmak
  ekranın yenilenmesine yine izin verir; ray dokunuşla yana kaydırılamaz.
- (WP06, WP07) Tooltip'ler silinmişken (kiosk modu): kapalı jog ve referans düğmelerinin
  gerekçeleri yazıyla okunur (Z− adım 10'da, `Referans alma sürüyor …`, `Makine devre dışı —
  referans alınamaz.`).

### 23

Ölçümleri dokümanlara işle: kurtarma adımı, fazla yol, tmax, zamanlamalar, G92 durumu. Commit yalnız kullanıcı isteyince.
