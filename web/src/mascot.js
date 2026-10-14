// Just Standup mascot (from the "Just Standup 吉祥物" design sheet artifact): ponytail + curls, one arm flexing,
// one hand holding a camera (= "photograph your space"). Pure: mascot(options) -> HTML string (an SVG, maybe wrapped).
//
//   style: clay | vinyl | neon | medal | sticker | line      (default clay, the one picked on the sheet)
//   pose:  flex (done screen) | snap (upload / build space) | cheer (streak)
//   face:  smile | wink | wow
//   anim:  hop + arm pump (+ camera flash for snap)

const BASE = { skin: "#F4B98F", hair: "#3A2420", top: "#C6FF3D", shorts: "#24302C", cam: "#2B3532", shoe: "#F1F5F3", band: "#FF6B9A", lens: "#5EE6D0" };
export const STYLES = {
  clay: { kind: "grad", pal: BASE, L: .26, D: .3, hi: .3, blur: 2.6, ink: "#24140F", shadow: "drop-shadow(0 16px 14px rgba(0,0,0,.5))" },
  vinyl: { kind: "grad", pal: { skin: "#F7AC7C", hair: "#2A1838", top: "#B5FF1A", shorts: "#22307A", cam: "#1B2321", shoe: "#FFFFFF", band: "#FF2E88", lens: "#38D9FF" },
    L: .55, D: .45, hi: .92, blur: .2, ink: "#15091E", shadow: "drop-shadow(0 10px 6px rgba(0,0,0,.6))" },
  neon: { kind: "neon", line: { skin: "#5EE6D0", hair: "#FF6B9A", top: "#C6FF3D", shorts: "#5EE6D0", cam: "#C6FF3D", shoe: "#5EE6D0", band: "#FF6B9A", lens: "#C6FF3D" },
    ink: "#C6FF3D", shadow: "drop-shadow(0 0 3px rgba(198,255,61,.9)) drop-shadow(0 0 12px rgba(94,230,208,.45))" },
  medal: { kind: "grad", wrap: "medal", pal: { skin: "#ECF8A6", hair: "#7E9A12", top: "#D2FF4F", shorts: "#8CAA18", cam: "#6E8710", shoe: "#F4FBCF", band: "#B7DB2C", lens: "#F6FFD0" },
    L: .35, D: .3, hi: .5, blur: 1, stroke: "#5C7208", sw: 1.3, ink: "#3F4F00",
    shadow: "drop-shadow(-1px -1px 0 rgba(255,255,255,.6)) drop-shadow(2px 3px 0 rgba(40,52,0,.5))" },
  sticker: { kind: "flat", pal: BASE, stroke: "#14201C", sw: 2.6, ink: "#14201C", outline: true, shadow: "none" },
  line: { kind: "flat", wrap: "plate", pal: Object.fromEntries(Object.keys(BASE).map((k) => [k, "#FBFBF8"])), stroke: "#111", sw: 3.2, ink: "#111", shadow: "none" },
};

const hex = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
const mix = (a, b, t) => "#" + hex(a).map((v, i) => Math.round(v + (hex(b)[i] - v) * t).toString(16).padStart(2, "0")).join("");
let uid = 0;

export function mascot({ style = "clay", pose = "flex", face = "smile", anim = false } = {}) {
  const S = STYLES[style], u = `m${++uid}`, keys = Object.keys(BASE);
  const A = (k) => {
    if (S.kind === "neon") return ` fill="#0C1311" stroke="${S.line[k]}" stroke-width="2.2" stroke-linejoin="round"`;
    const fill = S.kind === "grad" ? `url(#${u}-${k})` : S.pal[k];
    return ` fill="${fill}"` + (S.stroke ? ` stroke="${S.stroke}" stroke-width="${S.sw}" stroke-linejoin="round"` : "");
  };
  const ink = S.ink, isLine = style === "line";
  const hairLine = S.kind === "neon" ? S.line.hair : isLine ? "#111" : S.pal.hair;
  const camLine = S.kind === "neon" ? S.line.cam : isLine ? "#111" : S.pal.cam;
  const hi = (cx, cy, rx, ry, rot = 0) => S.hi ? `<ellipse cx="${cx}" cy="${cy}" rx="${rx}" ry="${ry}" transform="rotate(${rot} ${cx} ${cy})" fill="#fff" opacity="${S.hi}" filter="url(#${u}-blur)"/>` : "";
  const sparkC = isLine ? "#111" : style === "medal" ? "#5C7208" : "#C6FF3D";

  const defs = (S.kind === "grad" ? keys.map((k) => {
    const c = S.pal[k];
    return `<radialGradient id="${u}-${k}" cx=".32" cy=".28" r=".85"><stop offset="0" stop-color="${mix(c, "#ffffff", S.L)}"/><stop offset=".55" stop-color="${c}"/><stop offset="1" stop-color="${mix(c, "#000000", S.D)}"/></radialGradient>`;
  }).join("") : "")
    + `<filter id="${u}-blur" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="${S.blur ?? 1}"/></filter>`
    + (S.outline ? `<filter id="${u}-out" x="-20%" y="-20%" width="140%" height="140%"><feMorphology in="SourceAlpha" operator="dilate" radius="7" result="d"/>
      <feFlood flood-color="#ffffff"/><feComposite in2="d" operator="in" result="w"/><feOffset in="w" dy="6" result="o"/>
      <feFlood flood-color="#000000" flood-opacity=".4"/><feComposite in2="o" operator="in" result="s"/>
      <feMerge><feMergeNode in="s"/><feMergeNode in="w"/><feMergeNode in="SourceGraphic"/></feMerge></filter>` : "");

  const flexArm = `<g class="pump">
      <path d="M96 146 C93 128 72 121 61 133 C59 124 60 113 62 104 L42 104 C39 124 39 146 45 158 C49 167 60 167 70 165 L96 165 Z"${A("skin")}/>
      ${hi(79, 131, 8, 4, -12)}
      <rect x="40" y="108" width="24" height="8" rx="3"${A("band")}/>
      <circle cx="52" cy="96" r="14"${A("skin")}/>${hi(47, 89, 4.5, 3, -20)}
      <path d="M46 92 v6 M52 90 v6 M58 92 v6" stroke="${ink}" stroke-width="1.6" stroke-linecap="round" opacity=".45" fill="none"/>
      <path d="M30 84 l-9 -5 M37 71 l-4 -9 M53 66 l1 -10" stroke="${sparkC}" stroke-width="3.2" stroke-linecap="round" fill="none"/>
    </g>`;

  const lensRing = S.kind === "neon" ? ` stroke="${S.line.cam}" stroke-width="2.2"` : S.stroke ? ` stroke="${S.stroke}" stroke-width="${S.sw}"` : "";
  const camera = (tx, ty) => `<g transform="translate(${tx} ${ty})">
      <rect x="105" y="150" width="15" height="8" rx="3"${A("cam")}/>
      <rect x="131" y="151" width="9" height="6" rx="2"${A("band")}/>
      <rect x="99" y="156" width="46" height="31" rx="8"${A("cam")}/>
      <circle cx="122" cy="171.5" r="11.5" fill="${S.kind === "neon" ? "#0C1311" : isLine ? "#FBFBF8" : "#0E1514"}"${lensRing}/>
      <circle cx="122" cy="171.5" r="7.2"${A("lens")}/>
      <circle cx="118.6" cy="168" r="2.4" fill="#fff" opacity="${isLine ? 0 : .85}"/>
      <rect x="134" y="160" width="6" height="4" rx="1" fill="#fff" opacity=".55"/>
    </g>`;
  const strapPath = `<path d="M103 158 L112 139 M141 158 L128 139" stroke="${camLine}" stroke-width="3" fill="none"/>`;

  let rightArm, hand = "", strap = "", cam, extra = "";
  if (pose === "snap") {
    rightArm = `<path d="M142 145 C154 149 166 160 168 172 C170 150 170 124 168 104 L152 106 C154 126 155 146 153 157 C150 152 146 149 141 149 Z"${A("skin")}/>`;
    cam = camera(16, -76);
    hand = `<circle cx="161" cy="100" r="10"${A("skin")}/>`;
    extra = `<g class="flash"><path d="M182 54 l4 10 l10 4 l-10 4 l-4 10 l-4 -10 l-10 -4 l10 -4 z" fill="#FFE45E"/>
      <path d="M182 46 v-6 M200 56 l5 -4 M200 72 l6 2" stroke="#FFE45E" stroke-width="3" stroke-linecap="round"/></g>`;
  } else if (pose === "cheer") {
    rightArm = `<g transform="translate(240 0) scale(-1 1)">${flexArm}</g>`;
    strap = strapPath;
    cam = camera(0, 0);
  } else {
    rightArm = `<path d="M142 144 C155 146 164 157 165 171 C166 182 159 188 149 187 L134 185 L135 168 L148 169 C148 161 146 155 140 152 Z"${A("skin")}/>`;
    strap = strapPath;
    cam = camera(0, 0);
    hand = `<circle cx="138" cy="177" r="9"${A("skin")}/>`;
  }

  const eye = (cx) => `<ellipse cx="${cx}" cy="98" rx="4.8" ry="6" fill="${ink}"/>` + (S.kind === "neon" ? "" : `<circle cx="${cx + 1.6}" cy="95.6" r="1.8" fill="#fff"/>`);
  const eyes = face === "wink"
    ? `<path d="M101 99 Q107 92 113 99" stroke="${ink}" stroke-width="3" stroke-linecap="round" fill="none"/>${pose === "snap" ? "" : eye(133)}`
    : eye(107) + (pose === "snap" ? "" : eye(133));
  const mouth = face === "wow"
    ? `<ellipse cx="120" cy="115" rx="6.5" ry="7.5" fill="${S.kind === "neon" ? "none" : ink}" stroke="${ink}" stroke-width="${S.kind === "neon" ? 2.2 : 0}"/><ellipse cx="120" cy="119" rx="3.6" ry="2.4" fill="#FF7C8E"/>`
    : S.kind === "neon" ? `<path d="M110 110 Q120 123 130 110" stroke="${ink}" stroke-width="2.6" stroke-linecap="round" fill="none"/>`
    : `<path d="M110 110 Q120 125 130 110 Q120 114 110 110 Z" fill="${ink}"/><path d="M114.5 115.5 Q120 121 125.5 115.5 Q120 117.5 114.5 115.5 Z" fill="#FF7C8E"/>`;
  const blush = isLine ? "" : S.kind === "neon"
    ? `<ellipse cx="97" cy="110" rx="6" ry="3" fill="none" stroke="#FF6B9A" stroke-width="1.6"/><ellipse cx="143" cy="110" rx="6" ry="3" fill="none" stroke="#FF6B9A" stroke-width="1.6"/>`
    : `<ellipse cx="97" cy="110" rx="7" ry="4" fill="#FF7C8E" opacity=".45"/>${pose === "snap" ? "" : `<ellipse cx="143" cy="110" rx="7" ry="4" fill="#FF7C8E" opacity=".45"/>`}`;
  const shadowFill = S.kind === "neon" ? "rgba(198,255,61,.18)" : "rgba(0,0,0,.35)";

  const svg = `<svg class="mascot${anim ? " anim" : ""}" viewBox="0 0 240 264" aria-hidden="true" style="filter:${S.shadow}">
  <defs>${defs}</defs>
  ${S.wrap ? "" : `<ellipse class="m-shadow" cx="120" cy="252" rx="56" ry="7" fill="${shadowFill}"/>`}
  <g class="m-all"${S.outline ? ` filter="url(#${u}-out)"` : ""}>
    <path d="M104 58 C92 26 70 14 52 22 C66 26 78 36 84 52 C70 46 60 50 56 58 C72 56 86 62 96 72 Z"${A("hair")}/>
    <circle cx="150" cy="74" r="24"${A("hair")}/><circle cx="146" cy="52" r="20"${A("hair")}/><circle cx="124" cy="44" r="20"${A("hair")}/>
    <circle cx="100" cy="52" r="18"${A("hair")}/><circle cx="160" cy="98" r="15"${A("hair")}/>
    <rect x="101" y="198" width="16" height="42" rx="8"${A("skin")}/><rect x="123" y="198" width="16" height="42" rx="8"${A("skin")}/>
    <rect x="91" y="234" width="30" height="15" rx="7.5"${A("shoe")}/><rect x="119" y="234" width="30" height="15" rx="7.5"${A("shoe")}/>
    <path d="M94 244 h24 M122 244 h24" stroke="${sparkC}" stroke-width="2.4" stroke-linecap="round"/>
    <path d="M93 186 L147 186 L149 212 Q135 217 122 212 L120 203 L118 212 Q105 217 91 212 Z"${A("shorts")}/>
    <path d="M90 146 Q120 132 150 146 L148 192 Q120 200 92 192 Z"${A("top")}/>${hi(102, 160, 5, 11)}
    <rect x="111" y="122" width="18" height="20" rx="6"${A("skin")}/>
    <path d="M108 139 Q120 152 132 139 Z"${A("skin")}/>
    ${rightArm}
    <g>${flexArm}</g>
    <circle cx="83" cy="99" r="7"${A("skin")}/><circle cx="157" cy="99" r="7"${A("skin")}/>
    <circle cx="120" cy="92" r="38"${A("skin")}/>${hi(103, 75, 11, 6, -25)}
    <path d="M83 90 C80 62 100 48 122 49 C146 50 160 66 158 88 C151 76 140 69 129 72 C123 63 108 63 100 73 C94 71 87 78 83 90 Z"${A("hair")}/>${hi(121, 56, 10, 3.5, -8)}
    <path d="M100 87 q7 -4 13 -1 M127 86 q7 -3 13 1" stroke="${hairLine}" stroke-width="2.8" stroke-linecap="round" fill="none"/>
    ${eyes}${blush}${mouth}
    ${strap}${cam}${hand}${extra}
  </g>
</svg>`;
  return S.wrap ? `<div class="${S.wrap}">${svg}</div>` : svg;
}
