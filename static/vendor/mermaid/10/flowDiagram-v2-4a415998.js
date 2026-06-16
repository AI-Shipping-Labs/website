import { p as e, f as o } from "./flowDb-a8ed66c7.js";
import { f as t, g as a } from "./styles-eaa5a822.js";
import { u as i } from "./mermaid-500b880f.js";
import "./graph-76cbc52e.js";
import "./index-2c3f9202.js";
import "./layout-1b996aa5.js";
import "./clone-8daec071.js";
import "./edges-2ea5eef4.js";
import "./createText-2e0595ac.js";
import "./line-b38de265.js";
import "./array-2ff2c7a6.js";
import "./path-428ebac9.js";
import "./channel-ce12445b.js";
const M = {
  parser: e,
  db: o,
  renderer: t,
  styles: a,
  init: (r) => {
    r.flowchart || (r.flowchart = {}), r.flowchart.arrowMarkerAbsolute = r.arrowMarkerAbsolute, i({ flowchart: { arrowMarkerAbsolute: r.arrowMarkerAbsolute } }), t.setConf(r.flowchart), o.clear(), o.setGen("gen-2");
  }
};
export {
  M as diagram
};
