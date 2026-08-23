
const PATH = [[70,270],[70,60],[150,60],[150,200],[230,200],[230,60],[310,60],[310,270],[190,270],[190,150],[110,150],[110,270],[70,270]];
const PREVIEW = [[70,240],[70,60],[160,60],[160,180],[250,180],[250,60],[340,60],[340,240],[70,240]];

const GCODE = ["G21 G90 G54","G0 Z5.000","G0 X0.000 Y0.000","G1 Z-1.500 F300","G1 Y80.000 F900","G1 X36.000","G1 Y24.000","G1 X72.000","G1 Y80.000","G1 X108.000","G1 Y0.000","G1 X56.000","G1 Y44.000","G1 X20.000","G1 Y0.000","G0 Z5.000","M30"];

const FILES = [
  { name: "kapak_plakasi_v3.ngc", meta: "12 Ağu · 2.4 MB · T1, T4 · 4 812 satır", est: "18 dk", warn: false },
  { name: "govde_cebi.ngc", meta: "9 Ağu · 860 KB · T1 · 1 640 satır", est: "7 dk", warn: false },
  { name: "delik_deseni_8mm.ngc", meta: "4 Ağu · 112 KB · T3 · 302 satır", est: "3 dk", warn: false },
  { name: "yuzey_alma_240x180.ngc", meta: "1 Ağu · 48 KB · T2 · strok sınırında", est: "11 dk", warn: true },
  { name: "test_kare_50mm.ngc", meta: "28 Tem · 6 KB · T1 · 44 satır", est: "1 dk", warn: false }
];

const TOOLS = [
  { id: "T1", name: "3 mm düz freze · 2 ağız", dia: "3.000", len: "42.180", state: "takılı" },
  { id: "T2", name: "6 mm yüzey alma", dia: "6.000", len: "38.400", state: "hazır" },
  { id: "T3", name: "8 mm matkap ucu", dia: "8.000", len: "61.250", state: "hazır" },
  { id: "T4", name: "V-uç 60° gravür", dia: "6.000", len: "44.900", state: "hazır" },
  { id: "T5", name: "2 mm elmas uç", dia: "2.000", len: "—", state: "ölçülmedi" },
  { id: "T6", name: "Prob (tool sensörü)", dia: "—", len: "—", state: "sabit" }
];

const INPUTS = [
  { name: "X referans / limit", pin: "gpio.020" },
  { name: "Y referans / limit", pin: "gpio.021" },
  { name: "Z referans / limit", pin: "gpio.022" },
  { name: "Acil stop zinciri", pin: "gpio.023" },
  { name: "Takım boyu sensörü", pin: "gpio.024" },
  { name: "Su debi sensörü", pin: "gpio.025" }
];

const PROBE_MODES = [
  { id: "z", name: "Z yüzey bulma", desc: "Takım boyunu iş yüzeyine göre sıfırlar" },
  { id: "corner", name: "Köşe bulma", desc: "X ve Y'yi parçanın köşesinden alır" },
  { id: "center", name: "Delik merkezi", desc: "Dört nokta ile merkez hesaplar" },
  { id: "mesh", name: "Yüzey haritası", desc: "Eğrilik telafisi için ızgara tarama" }
];

class Component extends DCLogic {
  state = {
    screen: "kontrol", wcs: "G54", estop: false, running: false, fov: 100,
    pos: { X: 0, Y: 0, Z: 5 }, step: 1, spindle: false, coolant: true,
    progress: 0, file: FILES[0].name, tool: "T1",
    probe: "z", probeStep: 0, lastMdi: "G0 Z5.0",
    inputs: [false, false, false, false, false, true],
    acked: false,
    message: "Konfigürasyon yüklendi. Referans alınmadı."
  };

  componentDidMount() {
    this.timer = setInterval(() => {
      if (!this.state.running) return;
      this.setState(s => {
        const p = Math.min(1, s.progress + 0.0035 * (s.fov / 100));
        const t = p * (PATH.length - 1);
        const i = Math.floor(t), f = t - i;
        const a = PATH[Math.min(i, PATH.length - 1)], b = PATH[Math.min(i + 1, PATH.length - 1)];
        return {
          progress: p, running: p < 1,
          pos: { X: (a[0] + (b[0] - a[0]) * f - 70) * 0.45, Y: (270 - (a[1] + (b[1] - a[1]) * f)) * 0.45, Z: -1.5 },
          message: p < 1 ? "Program çalışıyor." : "Program tamamlandı."
        };
      });
    }, 60);
  }
  componentWillUnmount() { clearInterval(this.timer); }

  fmt(v) { return (v < 0 ? "" : " ") + v.toFixed(3); }

  jog(axis, dir) {
    if (this.state.estop || this.state.running) return;
    this.setState(s => ({ pos: { ...s.pos, [axis]: s.pos[axis] + dir * s.step } }));
  }

  renderVals() {
    const s = this.state;
    const acc = "#9184d9", accLight = "#b5abfc", accDeep = "#5d5294", accGhost = "#2b2741";
    const ok = "#84d9a5", bad = "#d9848c", warn = "#f0a92b";
    const surf = "#1a1c2c", line = "#292b31", muted = "#75798c";

    const stateLabel = s.estop ? "ACİL STOP" : s.running ? "Çalışıyor" : "Hazır";
    const stateColor = s.estop ? bad : s.running ? warn : accLight;

    const t = s.progress * (PATH.length - 1);
    const i = Math.floor(t), f = t - i;
    const a = PATH[Math.min(i, PATH.length - 1)], b = PATH[Math.min(i + 1, PATH.length - 1)];
    const done = Math.floor(t);
    const gline = Math.min(GCODE.length - 1, Math.floor(s.progress * GCODE.length));
    const totalMin = 18;
    const remMin = Math.max(0, Math.round(totalMin * (1 - s.progress)));

    const screens = {
      kontrol: ["Kontrol", "Çalışma ekranı · " + s.wcs],
      program: ["Program", "G-code dosyaları ve önizleme"],
      prob: ["Probing", "Yüzey, köşe ve merkez bulma"],
      takim: ["Takımlar", "Takım tablosu ve boy telafisi"],
      alarm: ["Alarmlar", "Giriş durumları ve olay kaydı"],
      makine: ["Makine", "Yapılandırma ve kart bilgisi"]
    };
    const activeAlarms = s.estop ? 1 : 0;

    const navDef = [["kontrol","Kontrol",""],["program","Program",""],["prob","Probing",""],["takim","Takımlar",""],["alarm","Alarmlar", activeAlarms ? String(activeAlarms) : ""],["makine","Makine",""]];

    const mkJog = (label, axis, dir) => ({
      label,
      cursor: axis ? "pointer" : "default",
      bg: axis ? surf : "transparent",
      border: axis ? line : "transparent",
      fg: axis ? (s.estop ? "#595d6c" : "#e9e9ed") : "transparent",
      press: axis ? () => this.jog(axis, dir) : () => {}
    });

    const probeStepsByMode = {
      z: ["Takımı sensörün üstüne getir", "Yavaş inişle temas ara", "Takım boyunu kaydet", "Z'yi iş sıfırına al"],
      corner: ["X kenarına yaklaş", "Y kenarına yaklaş", "Uç yarıçapını düş", "Köşeyi sıfır kabul et"],
      center: ["Deliğe ortala ve in", "Dört yönde temas ara", "Merkezi hesapla", "X/Y sıfırla"],
      mesh: ["Izgara adımını seç", "Tarama alanını doğrula", "Noktaları tara", "Telafi tablosunu yaz"]
    };
    const stepsList = probeStepsByMode[s.probe];
    const results = { z: ["", "", "42.180 mm", "Z = 0.000"], corner: ["", "", "", "X0 Y0"], center: ["", "", "Ø 19.98", "X0 Y0"], mesh: ["5 × 4", "", "20 nokta", "±0.06 mm"] };

    const selTool = TOOLS.find(x => x.id === s.tool) || TOOLS[0];

    return {
      configName: this.props.configName ?? "test-rig · hm2_7i92.0",
      showMachineCoords: this.props.showMachineCoords ?? true,

      nav: navDef.map(([id, name, badge]) => ({
        name, badge,
        badgeFg: badge ? bad : "transparent",
        bg: s.screen === id ? "#1e2035" : "transparent",
        fg: s.screen === id ? "#e9e9ed" : "#9397ab",
        dot: s.screen === id ? acc : "#3f424d",
        select: () => this.setState({ screen: id })
      })),
      isKontrol: s.screen === "kontrol", isProgram: s.screen === "program", isProb: s.screen === "prob",
      isTakim: s.screen === "takim", isAlarm: s.screen === "alarm", isMakine: s.screen === "makine",
      screenTitle: screens[s.screen][0],
      screenSub: screens[s.screen][1],

      linkStatus: [
        { label: "Kart", value: "10.10.10.10", color: ok },
        { label: "Gecikme", value: "38 µs", color: ok },
        { label: "Servo döngü", value: "1.0 ms", color: "#9397ab" }
      ],

      stateLabel, stateColor,
      stateBg: s.estop ? "#2a1e24" : "transparent",
      stateBorder: s.estop ? bad : line,
      estopLabel: s.estop ? "Sıfırla" : "ACİL STOP",
      estopBg: s.estop ? bad : "#241a22",
      estopFg: s.estop ? "#161826" : bad,
      toggleEstop: () => this.setState(st => ({
        estop: !st.estop, running: false, spindle: false,
        inputs: st.inputs.map((v, ix) => ix === 3 ? !st.estop : v),
        message: st.estop ? "Acil stop sıfırlandı. Referans gerekli." : "ACİL STOP — sürücü gücü donanımsal kesildi."
      })),

      homeChips: ["X", "Y", "Z"].map(n => ({ name: n, label: "ref", color: ok })),
      homeAll: () => this.setState({ pos: { X: 0, Y: 0, Z: 0 }, message: "Referans tamam — Z, sonra X, sonra Y." }),

      fovs: [50, 100, 150].map(v => ({
        label: v + "%",
        bg: s.fov === v ? accGhost : "transparent",
        fg: s.fov === v ? accLight : "#9397ab",
        border: s.fov === v ? accDeep : line,
        select: () => this.setState({ fov: v })
      })),

      axes: ["X", "Y", "Z"].map(n => ({
        name: n,
        work: this.fmt(s.pos[n]),
        machine: this.fmt(s.pos[n] + (n === "Z" ? 118.4 : 40)),
        zero: () => this.setState(st => ({ pos: { ...st.pos, [n]: 0 }, message: n + " ekseni " + st.wcs + " sıfırına alındı." }))
      })),
      wcsList: ["G54", "G55", "G56"].map(w => ({
        name: w,
        bg: s.wcs === w ? accGhost : "transparent",
        fg: s.wcs === w ? accLight : "#75798c",
        border: s.wcs === w ? accDeep : line,
        select: () => this.setState({ wcs: w })
      })),

      jogPad: [mkJog("", null), mkJog("Y+", "Y", 1), mkJog("", null), mkJog("X−", "X", -1), mkJog("⌖", null), mkJog("X+", "X", 1), mkJog("", null), mkJog("Y−", "Y", -1), mkJog("", null)],
      jogZ: [mkJog("Z+", "Z", 1), mkJog("", null), mkJog("Z−", "Z", -1)],
      steps: [0.01, 0.1, 1, 10, 50].map(v => ({
        label: String(v),
        bg: s.step === v ? accGhost : "transparent",
        fg: s.step === v ? accLight : "#9397ab",
        border: s.step === v ? accDeep : line,
        select: () => this.setState({ step: v })
      })),

      toggleSpindle: () => this.setState(st => ({ spindle: !st.spindle })),
      toggleCoolant: () => this.setState(st => ({ coolant: !st.coolant })),
      spindleBg: s.spindle ? accGhost : "transparent",
      spindleFg: s.spindle ? accLight : "#9397ab",
      spindleBorder: s.spindle ? accDeep : line,
      coolantBg: s.coolant ? accGhost : "transparent",
      coolantFg: s.coolant ? accLight : "#9397ab",
      coolantBorder: s.coolant ? accDeep : line,

      lastMdi: s.lastMdi,
      mdiQuick: [["G0 Z5", "G0 Z5.0"], ["G28", "G28"], ["M5", "M5"], ["G92.1", "G92.1"]].map(([label, cmd]) => ({
        label, press: () => this.setState({ lastMdi: cmd, message: "MDI: " + cmd + " yürütüldü." })
      })),

      pathAll: PATH.map(p => p.join(",")).join(" "),
      pathDone: PATH.slice(0, done + 1).concat([[a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f]]).map(p => p.join(",")).join(" "),
      headX: a[0] + (b[0] - a[0]) * f,
      headY: a[1] + (b[1] - a[1]) * f,
      previewPath: PREVIEW.map(p => p.join(",")).join(" "),
      extentLabel: "108 × 80 mm",

      programName: s.file,
      gcodeView: GCODE.map((text, ix) => ({
        no: String(ix + 1).padStart(2, "0"), text,
        bg: ix === gline && s.running ? accGhost : "transparent",
        fg: ix < gline ? "#595d6c" : ix === gline ? warn : "#cfd3e5"
      })).slice(Math.max(0, gline - 3), Math.max(9, gline + 6)),

      tiles: [
        { label: "İLERLEME HIZI", value: s.running ? String(Math.round(900 * s.fov / 100)) : "0", sub: "mm/dk", color: "#e9e9ed" },
        { label: "İĞ DEVRİ", value: s.spindle ? "12 000" : "0", sub: "d/dk", color: s.spindle ? accLight : "#e9e9ed" },
        { label: "AKTİF TAKIM", value: s.tool, sub: selTool.name, color: "#e9e9ed" },
        { label: "SU DEBİSİ", value: s.coolant ? "2.4" : "0.0", sub: s.coolant ? "L/dk · kilit açık" : "L/dk · iğ mili kilitli", color: s.coolant ? ok : bad }
      ],

      files: FILES.map(fl => ({
        name: fl.name, meta: fl.meta, est: fl.est,
        estColor: fl.warn ? warn : "#9397ab",
        bg: s.file === fl.name ? "#1e2035" : surf,
        border: s.file === fl.name ? accDeep : line,
        select: () => this.setState({ file: fl.name, progress: 0, running: false, message: fl.name + " yüklendi." })
      })),
      fileStats: [
        { label: "Satır", value: "4 812" },
        { label: "Takımlar", value: "T1, T4" },
        { label: "Süre tahmini", value: "18 dk" },
        { label: "X aralığı", value: "0 – 108 mm" },
        { label: "Y aralığı", value: "0 – 80 mm" },
        { label: "Z en derin", value: "−1.500 mm" }
      ],

      probeModes: PROBE_MODES.map(p => ({
        name: p.name, desc: p.desc,
        bg: s.probe === p.id ? "#1e2035" : surf,
        border: s.probe === p.id ? accDeep : line,
        fg: s.probe === p.id ? accLight : "#e9e9ed",
        select: () => this.setState({ probe: p.id, probeStep: 0 })
      })),
      probeParams: [
        { label: "Yaklaşma hızı", value: "300 mm/dk" },
        { label: "Temas hızı", value: "25 mm/dk" },
        { label: "Geri çekilme", value: "2.0 mm" }
      ],
      probeSteps: stepsList.map((text, ix) => {
        const doneStep = ix < s.probeStep;
        const active = ix === s.probeStep;
        return {
          no: String(ix + 1), text,
          dotBg: doneStep ? accGhost : "transparent",
          dotFg: doneStep ? accLight : active ? accLight : muted,
          dotBorder: doneStep || active ? accDeep : "#3f424d",
          fg: doneStep ? "#9397ab" : "#e9e9ed",
          result: doneStep ? results[s.probe][ix] : "",
          resultColor: ok
        };
      }),
      probeButton: s.probeStep >= stepsList.length ? "Tamamlandı" : s.probeStep === 0 ? "Probing başlat" : "Sonraki adım",
      probeRun: () => this.setState(st => ({ probeStep: Math.min(stepsList.length, st.probeStep + 1) })),
      probeReset: () => this.setState({ probeStep: 0 }),

      tools: TOOLS.map(t => ({
        id: t.id, name: t.name, dia: t.dia, len: t.len, state: t.state,
        tColor: s.tool === t.id ? accLight : "#e9e9ed",
        stateColor: t.state === "takılı" ? ok : t.state === "ölçülmedi" ? warn : "#75798c",
        bg: s.tool === t.id ? "#1e2035" : "transparent",
        select: () => this.setState({ tool: t.id })
      })),
      selTool: { name: selTool.id + " · " + selTool.name },
      selToolRows: [
        { label: "Çap", value: selTool.dia + " mm" },
        { label: "Boy telafisi", value: selTool.len === "—" ? "—" : selTool.len + " mm" },
        { label: "Durum", value: selTool.state },
        { label: "Önerilen devir", value: selTool.id === "T5" ? "8 000 d/dk" : "12 000 d/dk" }
      ],
      loadTool: () => this.setState({ tool: selTool.id, message: selTool.id + " takıldı, boy telafisi uygulandı." }),
      lastToolMeasure: "42.180 mm · 12 Ağu 14:22",

      inputs: INPUTS.map((inp, ix) => {
        const active = s.inputs[ix];
        return {
          name: inp.name, pin: inp.pin,
          state: active ? "AÇIK" : "kapalı",
          color: active ? (ix === 3 ? bad : ok) : "#595d6c",
          border: active ? (ix === 3 ? bad : accDeep) : line,
          toggle: () => this.setState(st => ({ inputs: st.inputs.map((v, j) => j === ix ? !v : v) }))
        };
      }),
      alarms: (s.acked ? [] : [
        { time: "14:31", text: "Acil stop zinciri açıldı — sürücü gücü kesildi", color: bad, fg: "#e9e9ed", source: "gpio.023", hide: !s.estop }
      ]).filter(x => !x.hide).concat([
        { time: "14:22", text: "Takım boyu ölçümü tamam: T1 = 42.180 mm", color: ok, fg: "#9397ab", source: "prob" },
        { time: "14:18", text: "yuzey_alma_240x180.ngc yumuşak limit sınırında — Y 182 mm", color: warn, fg: "#9397ab", source: "yorumlayıcı" },
        { time: "14:05", text: "Referans alma tamamlandı (Z, X, Y)", color: ok, fg: "#9397ab", source: "homing" },
        { time: "14:04", text: "hm2_eth: 7i92 bulundu, IDROM eşleşti", color: ok, fg: "#9397ab", source: "hm2_eth" },
        { time: "14:04", text: "Su debi sensörü sinyal veriyor — iğ mili kilidi açık", color: ok, fg: "#9397ab", source: "gpio.025" }
      ]),
      ackAll: () => this.setState({ acked: true }),

      axisConfig: [
        { name: "X", scale: "80.000 adım/mm", vel: "40 mm/s", acc: "500 mm/s²", travel: "0 – 300 mm" },
        { name: "Y", scale: "80.000 adım/mm", vel: "40 mm/s", acc: "500 mm/s²", travel: "0 – 180 mm" },
        { name: "Z", scale: "400.000 adım/mm", vel: "8 mm/s", acc: "300 mm/s²", travel: "−45 – 0 mm" }
      ],
      timing: [
        { label: "steplen", value: "2 000 ns" },
        { label: "stepspace", value: "2 000 ns" },
        { label: "dirsetup", value: "20 000 ns" },
        { label: "dirhold", value: "20 000 ns" }
      ],
      boardRows: [
        { label: "Kart", value: "Zhulong V2.0 (Mesa 7i92)" },
        { label: "HAL öneki", value: "hm2_7i92.0" },
        { label: "FPGA", value: "Spartan-6 XC6SLX9" },
        { label: "IO pinleri", value: "34 (2 × 17)" },
        { label: "Saat", value: "100 MHz" },
        { label: "Bağlantı", value: "hm2_eth · 10.10.10.10" }
      ],
      rtRows: [
        { label: "Çekirdek", value: "PREEMPT_RT arm64", color: "#e9e9ed" },
        { label: "LinuxCNC", value: "2.9.10 uspace", color: "#e9e9ed" },
        { label: "Maks. jitter", value: "38 µs", color: ok }
      ],

      progressLabel: Math.round(s.progress * 100) + "% · satır " + (gline + 1) + " / " + GCODE.length,
      progressWidth: (s.progress * 100).toFixed(1) + "%",
      remaining: remMin + " dk",
      message: s.message,
      messageColor: s.estop ? bad : "#75798c",

      startBorder: s.estop ? "#3f424d" : accDeep,
      startFg: s.estop ? "#595d6c" : accLight,
      start: () => { if (!s.estop) this.setState(st => ({ running: true, progress: st.progress >= 1 ? 0 : st.progress, message: "Program çalışıyor." })); },
      pause: () => this.setState({ running: false, message: "Duraklatıldı — devam etmek için Başlat." }),
      stop: () => this.setState({ running: false, progress: 0, message: "Program durduruldu." })
    };
  }
}

